"""
YouTube search via yt-dlp. No API key required.

Install:  pip install yt-dlp
Usage:    python youtube_search.py "spanish for beginners" --max 20
          python youtube_search.py "easy french" --max 50 --output results.json
          python youtube_search.py "german a1" --min-duration 120 --max-duration 1800
"""

import argparse
import json
import sys
from typing import Any

# Windows consoles default to cp1252 — force UTF-8 so non-Latin output
# (Japanese, Chinese, etc.) doesn't blow up with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.stderr.write("yt-dlp is not installed. Run:  pip install yt-dlp\n")
    sys.exit(1)


def search(query: str, max_results: int = 20) -> list[dict[str, Any]]:
    """Return search results for `query` using yt-dlp's ytsearch backend."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,        # don't resolve each video's full page (much faster)
        "skip_download": True,
        "default_search": "ytsearch",
        # See youtube_subtitles.get_info — android client avoids the bot-block rate-limit.
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    return info.get("entries", []) or []


def normalize(entry: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields we care about and shape them consistently."""
    vid = entry.get("id")
    return {
        "id": vid,
        "title": entry.get("title"),
        "url": entry.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
        "channel": entry.get("channel") or entry.get("uploader"),
        "channel_url": entry.get("channel_url") or entry.get("uploader_url"),
        "duration_seconds": entry.get("duration"),
        "view_count": entry.get("view_count"),
        "thumbnail": entry.get("thumbnails", [{}])[-1].get("url") if entry.get("thumbnails")
                     else (f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else None),
        "description": entry.get("description"),
        "live_status": entry.get("live_status"),
    }


def filter_by_duration(results: list[dict[str, Any]], min_s: int | None, max_s: int | None) -> list[dict[str, Any]]:
    if min_s is None and max_s is None:
        return results
    out = []
    for r in results:
        d = r.get("duration_seconds")
        if d is None:
            continue
        if min_s is not None and d < min_s:
            continue
        if max_s is not None and d > max_s:
            continue
        out.append(r)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Search YouTube via yt-dlp.")
    p.add_argument("query", help="Search query, e.g. 'easy spanish'")
    p.add_argument("--max", type=int, default=20, help="Max results to fetch (default 20).")
    p.add_argument("--min-duration", type=int, default=None, help="Filter: minimum duration in seconds.")
    p.add_argument("--max-duration", type=int, default=None, help="Filter: maximum duration in seconds.")
    p.add_argument("--output", "-o", default=None, help="Write JSON to this file instead of stdout.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON (indent=2).")
    args = p.parse_args()

    raw = search(args.query, args.max)
    results = [normalize(e) for e in raw if e]
    results = filter_by_duration(results, args.min_duration, args.max_duration)

    payload = {
        "query": args.query,
        "count": len(results),
        "results": results,
    }
    text = json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write(f"Wrote {len(results)} results to {args.output}\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
