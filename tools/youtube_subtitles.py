"""
Fetch subtitles for a YouTube video as plain text. No API key required.

Strategy: try manual subs in the video's original language first, then
auto-generated in that language, then any manual track, then any auto track.

Install:  pip install yt-dlp
Usage:    python youtube_subtitles.py "https://youtube.com/watch?v=..."
          python youtube_subtitles.py VIDEO_ID --lang ja
          python youtube_subtitles.py VIDEO_ID --output transcript.txt
          python youtube_subtitles.py VIDEO_ID --list   # show available tracks
"""

import argparse
import re
import sys
import urllib.request
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.stderr.write("yt-dlp is not installed. Run:  pip install yt-dlp\n")
    sys.exit(1)


def get_info(video: str) -> dict[str, Any]:
    """Resolve a video URL or bare ID to full yt-dlp metadata (no download)."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        video = f"https://www.youtube.com/watch?v={video}"
    # The android player client avoids YouTube's "Sign in to confirm you're not
    # a bot" rate-limit that the default web client triggers after a few
    # requests from the same IP.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(video, download=False)


def pick_track(info: dict[str, Any], requested_lang: str | None) -> tuple[str, str, str] | None:
    """
    Return (kind, lang, url) for the best subtitle track, or None.
    kind is "manual" or "auto". Tries:
      1. manual in requested/original lang
      2. auto in requested/original lang
      3. manual in any lang
      4. auto in any lang
    Within a track we prefer VTT, then SRV3, then whatever is first.
    """
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    original = requested_lang or info.get("language") or "en"

    def best_url(entries: list[dict[str, Any]]) -> str | None:
        if not entries:
            return None
        for fmt in ("vtt", "srv3", "ttml"):
            for e in entries:
                if e.get("ext") == fmt:
                    return e.get("url")
        return entries[0].get("url")

    def find(track_dict: dict[str, list], lang: str) -> str | None:
        # Try exact match, then any key that starts with the lang prefix (e.g. "ja-orig")
        if lang in track_dict:
            return best_url(track_dict[lang])
        for key, entries in track_dict.items():
            if key.split("-")[0] == lang:
                return best_url(entries)
        return None

    url = find(manual, original)
    if url: return ("manual", original, url)
    url = find(auto, original)
    if url: return ("auto", original, url)
    if manual:
        lang = next(iter(manual))
        return ("manual", lang, best_url(manual[lang]))
    if auto:
        lang = next(iter(auto))
        return ("auto", lang, best_url(auto[lang]))
    return None


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


# VTT/SRT cleanup: timestamp lines, cue IDs, headers, inline tags.
_TIMESTAMP_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")
_INLINE_TAG_RE = re.compile(r"<[^>]+>")               # <c>, <00:00:01.000>, etc.
_CUE_SETTINGS_RE = re.compile(r"\s+(align|position|size|line|vertical):\S+")


def vtt_to_text(raw: str) -> str:
    """Strip VTT/SRT down to deduplicated plain text."""
    out: list[str] = []
    last = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")):
            continue
        if _TIMESTAMP_RE.search(line):
            continue
        if line.isdigit():                            # SRT cue index
            continue
        line = _INLINE_TAG_RE.sub("", line)
        line = _CUE_SETTINGS_RE.sub("", line)
        line = line.strip()
        if not line or line == last:
            continue
        out.append(line)
        last = line
    return "\n".join(out)


def list_tracks(info: dict[str, Any]) -> None:
    manual = sorted((info.get("subtitles") or {}).keys())
    auto = sorted((info.get("automatic_captions") or {}).keys())
    print(f"Title:    {info.get('title')}")
    print(f"Language: {info.get('language') or '(unknown)'}")
    print(f"Manual subtitles ({len(manual)}):    {', '.join(manual) or '(none)'}")
    print(f"Auto captions ({len(auto)}):         {', '.join(auto[:20])}{' …' if len(auto) > 20 else ''}")


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch YouTube subtitles as plain text.")
    p.add_argument("video", help="YouTube URL or 11-char video ID.")
    p.add_argument("--lang", default=None, help="Preferred language code (e.g. ja, en). Default: video's original.")
    p.add_argument("--output", "-o", default=None, help="Write to this file instead of stdout.")
    p.add_argument("--list", action="store_true", help="List available subtitle tracks and exit.")
    args = p.parse_args()

    info = get_info(args.video)

    if args.list:
        list_tracks(info)
        return

    pick = pick_track(info, args.lang)
    if not pick:
        sys.stderr.write("No subtitles or auto-captions available for this video.\n")
        sys.exit(2)

    kind, lang, url = pick
    sys.stderr.write(f"Using {kind} captions, language={lang}\n")
    text = vtt_to_text(fetch(url))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write(f"Wrote {len(text)} chars to {args.output}\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
