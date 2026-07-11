"""Quick diagnostic: print jreadability's input features for one or more videos."""

import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fugashi import Tagger
from jreadability import compute_readability
from youtube_subtitles import get_info, fetch as fetch_subs, vtt_to_text

_SENT_RE = re.compile(r"[。！？!?]")
_KANJI = re.compile(r"[一-鿿]")
_HIRA = re.compile(r"[぀-ゟ]")
_KATA = re.compile(r"[゠-ヿ]")


def normalize(text):
    if _SENT_RE.search(text):
        return text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "。".join(lines) + "。" if lines else text


def fetch_jp(vid):
    info = get_info(vid)
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for d in (manual, auto):
        for k, entries in d.items():
            if k.split("-")[0] == "ja":
                for fmt in ("vtt", "srv3", "ttml"):
                    for e in entries:
                        if e.get("ext") == fmt:
                            return d is manual, vtt_to_text(fetch_subs(e["url"]))
                return d is manual, vtt_to_text(fetch_subs(entries[0]["url"]))
    raise RuntimeError("no Japanese subs")


def breakdown(vid, tagger):
    manual_p, raw = fetch_jp(vid)
    text = normalize(raw)
    tokens = list(tagger(text))

    # Sentence count (using the same punctuation jreadability cares about)
    sentences = [s for s in re.split(r"[。！？]", text) if s.strip()]
    n_sent = len(sentences) or 1

    n_kanji = len(_KANJI.findall(text))
    n_hira = len(_HIRA.findall(text))
    n_kata = len(_KATA.findall(text))
    n_jp = n_kanji + n_hira + n_kata

    pos_counts = {}
    for tok in tokens:
        pos = tok.feature.pos1
        pos_counts[pos] = pos_counts.get(pos, 0) + 1
    n_verb = pos_counts.get("動詞", 0)
    n_aux = pos_counts.get("助動詞", 0)

    score = compute_readability(text)

    print(f"\n=== {vid} ===")
    print(f"  subs source: {'manual' if manual_p else 'auto'}")
    print(f"  chars total: {len(text)}    JP chars: {n_jp}")
    print(f"  sentences:   {n_sent}")
    print(f"  morphemes:   {len(tokens)}   avg/sentence: {len(tokens) / n_sent:.1f}")
    print(f"  kanji ratio:    {n_kanji / n_jp:.2%}   ({n_kanji} chars)")
    print(f"  hiragana ratio: {n_hira / n_jp:.2%}   ({n_hira} chars)")
    print(f"  katakana ratio: {n_kata / n_jp:.2%}   ({n_kata} chars)")
    print(f"  verb ratio:     {n_verb / len(tokens):.2%}   ({n_verb})")
    print(f"  aux-verb ratio: {n_aux / len(tokens):.2%}   ({n_aux})")
    print(f"  → score: {score:.2f}")


if __name__ == "__main__":
    tagger = Tagger()
    for vid in sys.argv[1:]:
        breakdown(vid, tagger)
