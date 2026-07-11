"""
Score the readability of Japanese text using the jreadability model.

Lower score = harder. Bands from Lee & Hasebe (2018):
    0.5 ≤ s < 1.5  Upper-Advanced     (上級後半)
    1.5 ≤ s < 2.5  Lower-Advanced     (上級前半)
    2.5 ≤ s < 3.5  Upper-Intermediate (中級後半)
    3.5 ≤ s < 4.5  Lower-Intermediate (中級前半)
    4.5 ≤ s < 5.5  Upper-Elementary   (初級後半)
    5.5 ≤ s ≤ 6.5  Lower-Elementary   (初級前半)

Install:  pip install jreadability yt-dlp
Usage:    python jp_readability.py qzzweIQoIOU             # fetch subs + score
          python jp_readability.py --file transcript.txt   # score a text file
          echo "今日はいい天気です" | python jp_readability.py --stdin
"""

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jreadability import compute_readability  # noqa: E402

BANDS = [
    (1.5, "Upper-Advanced",     "上級後半"),
    (2.5, "Lower-Advanced",     "上級前半"),
    (3.5, "Upper-Intermediate", "中級後半"),
    (4.5, "Lower-Intermediate", "中級前半"),
    (5.5, "Upper-Elementary",   "初級後半"),
    (6.6, "Lower-Elementary",   "初級前半"),
]


def band_for(score: float) -> tuple[str, str]:
    for upper, en, jp in BANDS:
        if score < upper:
            return en, jp
    return BANDS[-1][1], BANDS[-1][2]


def text_from_video(video: str, lang: str | None) -> str:
    """Reuse youtube_subtitles' fetch+clean pipeline."""
    from youtube_subtitles import get_info, pick_track, fetch, vtt_to_text
    info = get_info(video)
    pick = pick_track(info, lang)
    if not pick:
        sys.stderr.write("No subtitles or auto-captions available for this video.\n")
        sys.exit(2)
    kind, picked_lang, url = pick
    sys.stderr.write(f"Video:   {info.get('title')}\n")
    sys.stderr.write(f"Source:  {kind} captions, lang={picked_lang}\n")
    return vtt_to_text(fetch(url))


def main() -> None:
    p = argparse.ArgumentParser(description="Score Japanese text readability.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("video", nargs="?", help="YouTube URL or 11-char video ID to fetch subs from.")
    src.add_argument("--file", help="Score the contents of this text file.")
    src.add_argument("--stdin", action="store_true", help="Read text from stdin.")
    p.add_argument("--lang", default=None, help="Override subtitle language (default: video's original).")
    p.add_argument("--show-text", action="store_true", help="Also print the scored text (first 500 chars).")
    args = p.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
        source_label = args.file
    elif args.stdin:
        text = sys.stdin.read()
        source_label = "<stdin>"
    elif args.video:
        text = text_from_video(args.video, args.lang)
        source_label = args.video
    else:
        p.error("Provide a video ID, --file, or --stdin.")

    text = text.strip()
    if not text:
        sys.stderr.write("Empty text — nothing to score.\n")
        sys.exit(2)

    score = compute_readability(text)
    en, jp = band_for(score)

    sys.stderr.write(f"Chars:   {len(text)}\n")
    print(f"Score:   {score:.3f}   →   {en}  ({jp})")
    if args.show_text:
        print("---")
        print(text[:500] + ("…" if len(text) > 500 else ""))


if __name__ == "__main__":
    main()
