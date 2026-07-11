"""Flask backend for the language learning video tracker."""

import json
import os
import secrets
import sqlite3
import sys
import traceback
from contextlib import closing
from datetime import date
from functools import wraps
from hashlib import pbkdf2_hmac

from flask import Flask, jsonify, make_response, request, send_from_directory
from werkzeug.exceptions import HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

from youtube_search import search as yt_search, normalize as yt_normalize  # noqa: E402
from youtube_subtitles import get_info, fetch as fetch_subs, vtt_to_text  # noqa: E402
from kanji_levels import compute_breakdown as kanji_breakdown  # noqa: E402

DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "videos.db"))
DEFAULT_GOAL_MINUTES = 30
SESSION_COOKIE = "japapp_session"
HASH_ITERATIONS = 260000

app = Flask(__name__, static_folder=HERE, static_url_path="")


@app.errorhandler(Exception)
def verbose_json_error(error):
    if isinstance(error, HTTPException):
        return jsonify({
            "error": error.description,
            "type": error.__class__.__name__,
            "status": error.code,
        }), error.code
    app.logger.exception("Unhandled server error")
    return jsonify({
        "error": str(error),
        "type": error.__class__.__name__,
        "traceback": traceback.format_exc(),
    }), 500


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${digest}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, expected = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        ).hex()
        return secrets.compare_digest(digest, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def user_count(db):
    return db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def current_user():
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with closing(get_db()) as db:
        return db.execute(
            "SELECT id, username FROM users WHERE session_token=?",
            (token,),
        ).fetchone()


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "authentication required"}), 401
        request.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def set_session_cookie(response, token):
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="Lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
    )
    return response

def _row_to_video(row):
    """Convert a sqlite3.Row into a dict, parsing the kanji_breakdown JSON blob."""
    d = dict(row)
    if d.get("kanji_breakdown"):
        try:
            d["kanji_breakdown"] = json.loads(d["kanji_breakdown"])
        except (ValueError, TypeError):
            d["kanji_breakdown"] = None
    return d


def _enrich_from_youtube(vid):
    """Pull metadata (title, channel, duration, language, etc.) from yt-dlp.
    Returns None on failure; caller should fall back to user-provided / defaults."""
    try:
        info = get_info(vid)
    except Exception:
        return None
    thumbs = info.get("thumbnails") or []
    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "thumbnail": (thumbs[-1].get("url") if thumbs else None),
        "description": info.get("description"),
        "language": info.get("language"),
    }


# ---------- DB helpers ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(db, table, col):
    return any(r["name"] == col for r in db.execute(f"PRAGMA table_info({table})").fetchall())


def init_db():
    with closing(get_db()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            channel TEXT,
            channel_url TEXT,
            duration_seconds INTEGER,
            view_count INTEGER,
            thumbnail TEXT,
            description TEXT,
            language TEXT,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            watched_seconds INTEGER NOT NULL DEFAULT 0,
            level_score REAL,
            level_band_en TEXT,
            level_band_jp TEXT,
            level_rated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_watch (
            date TEXT PRIMARY KEY,
            seconds INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS watch_sessions (
            session_token TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            seconds_watched INTEGER NOT NULL DEFAULT 0,
            last_position REAL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_started ON watch_sessions(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_video ON watch_sessions(video_id);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            session_token TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        # Idempotent migrations for older installs.
        if not _column_exists(db, "videos", "last_position_seconds"):
            db.execute("ALTER TABLE videos ADD COLUMN last_position_seconds REAL")
        if not _column_exists(db, "videos", "kanji_breakdown"):
            db.execute("ALTER TABLE videos ADD COLUMN kanji_breakdown TEXT")
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_goal_minutes', ?)",
            (str(DEFAULT_GOAL_MINUTES),),
        )
        db.commit()


# ---------- Japanese subtitle picker (strict — for rating) ----------
def find_japanese_subtitle_url(info):
    """Return (kind, url) for the best Japanese subtitle track, or None."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    def pick(entries):
        for fmt in ("vtt", "srv3", "ttml"):
            for e in entries:
                if e.get("ext") == fmt:
                    return e.get("url")
        return entries[0].get("url") if entries else None

    def find_ja(tracks):
        for key, entries in tracks.items():
            if key.split("-")[0] == "ja":
                return pick(entries)
        return None

    url = find_ja(manual)
    if url:
        return "manual", url
    url = find_ja(auto)
    if url:
        return "auto", url
    return None


def save_rating_from_text(vid, text, subtitle_kind):
    if not text.strip():
        return jsonify({"error": "transcript text is empty"}), 422

    breakdown = kanji_breakdown(text)
    if breakdown["total_kanji"] == 0:
        return jsonify({"error": "transcript contains no kanji"}), 422

    score = breakdown["difficulty_score"]
    band = breakdown["difficulty_band"]

    with closing(get_db()) as db:
        db.execute(
            """UPDATE videos SET
                 level_score=?, level_band_en=?, level_band_jp=NULL,
                 level_rated_at=datetime('now'),
                 kanji_breakdown=?
               WHERE id=?""",
            (score, band, json.dumps(breakdown, ensure_ascii=False), vid),
        )
        db.commit()
    return jsonify({
        "difficulty_band": band,
        "difficulty_score": score,
        "subtitle_kind": subtitle_kind,
        "chars_scored": len(text),
        "kanji_breakdown": breakdown,
    })


# ---------- Routes ----------
@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/api/auth/status")
def auth_status():
    with closing(get_db()) as db:
        has_user = user_count(db) > 0
    user = current_user() if has_user else None
    return jsonify({
        "has_user": has_user,
        "authenticated": bool(user),
        "username": user["username"] if user else None,
    })


@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username:
        return jsonify({"error": "username required"}), 400
    if not password:
        return jsonify({"error": "password required"}), 400

    token = secrets.token_urlsafe(32)
    with closing(get_db()) as db:
        if user_count(db) > 0:
            return jsonify({"error": "user already exists"}), 409
        db.execute(
            "INSERT INTO users (id, username, password_hash, session_token) VALUES (1, ?, ?, ?)",
            (username, password_hash(password), token),
        )
        db.commit()
    response = jsonify({"ok": True, "username": username})
    return set_session_cookie(response, token)


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT id, username, password_hash FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return jsonify({"error": "invalid username or password"}), 401
        token = secrets.token_urlsafe(32)
        db.execute("UPDATE users SET session_token=? WHERE id=?", (token, row["id"]))
        db.commit()
    response = jsonify({"ok": True, "username": row["username"]})
    return set_session_cookie(response, token)


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with closing(get_db()) as db:
            db.execute("UPDATE users SET session_token=NULL WHERE session_token=?", (token,))
            db.commit()
    response = make_response(jsonify({"ok": True}))
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.route("/api/videos", methods=["GET"])
@require_auth
def list_videos():
    with closing(get_db()) as db:
        rows = db.execute("SELECT * FROM videos ORDER BY added_at DESC").fetchall()
    return jsonify([_row_to_video(r) for r in rows])


@app.route("/api/videos", methods=["POST"])
@require_auth
def add_video():
    data = request.get_json(silent=True) or {}
    vid = data.get("id")
    if not vid:
        return jsonify({"error": "id required"}), 400

    # If the client didn't supply a real title (e.g. add-by-URL with blank
    # title), enrich from yt-dlp. Search-added videos already carry full
    # metadata, so no extra round-trip in that path.
    user_title = (data.get("title") or "").strip()
    needs_enrich = (not user_title) or user_title == f"Video {vid}"
    enriched = _enrich_from_youtube(vid) if needs_enrich else None

    def pick(key, fallback=None):
        return data.get(key) or (enriched.get(key) if enriched else None) or fallback

    fields = {
        "id": vid,
        "title": user_title or pick("title") or f"Video {vid}",
        "channel": pick("channel"),
        "channel_url": pick("channel_url"),
        "duration_seconds": pick("duration_seconds"),
        "view_count": pick("view_count"),
        "thumbnail": pick("thumbnail", f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"),
        "description": pick("description"),
        "language": data.get("language") or (enriched.get("language") if enriched else None),
    }
    cols = ",".join(fields.keys())
    placeholders = ",".join("?" * len(fields))
    with closing(get_db()) as db:
        try:
            db.execute(
                f"INSERT INTO videos ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "already in collection"}), 409
        row = db.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    return jsonify(_row_to_video(row)), 201


@app.route("/api/videos/<vid>", methods=["DELETE"])
@require_auth
def delete_video(vid):
    with closing(get_db()) as db:
        cur = db.execute("SELECT watched_seconds FROM videos WHERE id=?", (vid,)).fetchone()
        if not cur:
            return "", 204
        db.execute("DELETE FROM videos WHERE id=?", (vid,))
        db.commit()
    return "", 204


@app.route("/api/videos/<vid>/watch", methods=["POST"])
@require_auth
def add_watch_time(vid):
    data = request.get_json(silent=True) or {}
    try:
        seconds = int(data.get("seconds", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "seconds must be an integer"}), 400
    if seconds <= 0:
        return jsonify({"error": "seconds must be > 0"}), 400

    position = data.get("position")
    if position is not None:
        try:
            position = float(position)
        except (TypeError, ValueError):
            position = None
    token = data.get("session_token") or None

    today = date.today().isoformat()
    with closing(get_db()) as db:
        res = db.execute(
            """UPDATE videos
               SET watched_seconds = watched_seconds + ?,
                   last_position_seconds = COALESCE(?, last_position_seconds)
               WHERE id = ?""",
            (seconds, position, vid),
        )
        if res.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        db.execute(
            """INSERT INTO daily_watch (date, seconds) VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET seconds = seconds + excluded.seconds""",
            (today, seconds),
        )
        if token:
            db.execute(
                """INSERT INTO watch_sessions (session_token, video_id, seconds_watched, last_position)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_token) DO UPDATE SET
                     seconds_watched = seconds_watched + excluded.seconds_watched,
                     last_position = COALESCE(excluded.last_position, last_position),
                     updated_at = datetime('now')""",
                (token, vid, seconds, position),
            )
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/history")
@require_auth
def list_history():
    limit = max(1, min(200, int(request.args.get("limit", 100))))
    with closing(get_db()) as db:
        rows = db.execute(
            """SELECT s.session_token, s.video_id, s.started_at, s.updated_at,
                      s.seconds_watched, s.last_position,
                      v.title, v.channel, v.thumbnail, v.language,
                      v.duration_seconds, v.level_score, v.level_band_en
               FROM watch_sessions s
               LEFT JOIN videos v ON v.id = s.video_id
               ORDER BY s.started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/videos/<vid>/rate", methods=["POST"])
@require_auth
def rate_video(vid):
    """Fetch Japanese subtitles and compute a JLPT-kanji-level breakdown."""
    with closing(get_db()) as db:
        row = db.execute("SELECT id FROM videos WHERE id=?", (vid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    try:
        info = get_info(vid)
    except Exception as e:
        return jsonify({"error": f"failed to fetch video info: {e}"}), 502

    pick = find_japanese_subtitle_url(info)
    if not pick:
        return jsonify({"error": "no Japanese subtitles or auto-captions available"}), 422
    kind, url = pick

    try:
        text = vtt_to_text(fetch_subs(url))
    except Exception as e:
        return jsonify({"error": f"failed to fetch subtitles: {e}"}), 502
    if not text.strip():
        return jsonify({"error": "subtitle text was empty after cleanup"}), 422

    return save_rating_from_text(vid, text, kind)


@app.route("/api/videos/<vid>/rate-text", methods=["POST"])
@require_auth
def rate_video_text(vid):
    """Compute and save a rating from transcript text pasted by the user."""
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""

    with closing(get_db()) as db:
        row = db.execute("SELECT id FROM videos WHERE id=?", (vid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    return save_rating_from_text(vid, text, "pasted")


@app.route("/api/search")
@require_auth
def search_endpoint():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q parameter required"}), 400
    try:
        max_results = max(1, min(50, int(request.args.get("max", 20))))
    except ValueError:
        max_results = 20

    try:
        raw = yt_search(q, max_results)
    except Exception as e:
        return jsonify({"error": f"search failed: {e}"}), 502
    results = [yt_normalize(e) for e in raw if e]

    with closing(get_db()) as db:
        existing = {r["id"] for r in db.execute("SELECT id FROM videos").fetchall()}
    for r in results:
        r["in_collection"] = r["id"] in existing
    return jsonify({"query": q, "count": len(results), "results": results})


@app.route("/api/stats")
@require_auth
def stats():
    today = date.today().isoformat()
    with closing(get_db()) as db:
        today_row = db.execute("SELECT seconds FROM daily_watch WHERE date=?", (today,)).fetchone()
        total_row = db.execute("SELECT COALESCE(SUM(watched_seconds),0) AS s FROM videos").fetchone()
        goal_row = db.execute("SELECT value FROM settings WHERE key='daily_goal_minutes'").fetchone()
    return jsonify({
        "today_seconds": today_row["seconds"] if today_row else 0,
        "total_seconds": total_row["s"],
        "daily_goal_minutes": int(goal_row["value"]) if goal_row else DEFAULT_GOAL_MINUTES,
    })


@app.route("/api/goal", methods=["POST"])
@require_auth
def set_goal():
    data = request.get_json(silent=True) or {}
    try:
        mins = int(data.get("minutes", DEFAULT_GOAL_MINUTES))
    except (TypeError, ValueError):
        return jsonify({"error": "minutes must be an integer"}), 400
    if mins < 1:
        return jsonify({"error": "minutes must be >= 1"}), 400
    with closing(get_db()) as db:
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_goal_minutes', ?)",
            (str(mins),),
        )
        db.commit()
    return jsonify({"daily_goal_minutes": mins})


@app.route("/api/reset", methods=["POST"])
@require_auth
def reset_watch_time():
    with closing(get_db()) as db:
        db.execute("UPDATE videos SET watched_seconds=0, last_position_seconds=NULL")
        db.execute("DELETE FROM daily_watch")
        db.execute("DELETE FROM watch_sessions")
        db.commit()
    return jsonify({"ok": True})


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Open http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
