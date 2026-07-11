"""JLPT kanji-level breakdown for Japanese text.

For a given text, returns:
  - per-band occurrence counts (how the actual text decomposes)
  - per-band unique-kanji counts (vocabulary breadth)
  - "required level": the JLPT level at which cumulative unique-kanji
    coverage reaches 80% (counted easiest-first, N5 → N1 → above-N1).

Bands: N5, N4, N3, N2, N1, above_n1.
A kanji outside the 2,211-char JLPT spec lands in "above_n1" — for prototype
purposes that includes proper nouns we'd ideally filter out with NER.

CLI:
    python kanji_levels.py "テキストをここに"
    python kanji_levels.py --file transcript.txt
    python kanji_levels.py --stdin
"""

import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jlpt_kanji.json")
_KANJI_RE = re.compile(r"[一-鿿]")
_BANDS = ("N5", "N4", "N3", "N2", "N1", "above_n1")

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _LEVELS = json.load(_f)   # kanji -> 1..5  (5 = N5 = easiest)

# Difficulty model: weighted average of per-band kanji counts, where each
# JLPT level gets a linear weight (easier → smaller weight). We blend the
# unique-weighted score with the occurrence-weighted score so that vocabulary
# breadth dominates but raw encounter frequency still matters.
_LEVEL_WEIGHTS = {"N5": 1.0, "N4": 2.0, "N3": 3.0, "N2": 4.0, "N1": 5.0, "above_n1": 6.0}

# Cutoffs calibrated on a 91-video corpus (see tools/bulk_results.json):
# the median real-world video lands around 2.6, easiest at ~1.9, hardest at ~3.4.
_BAND_CUTOFFS = (
    (2.20, "N5"),
    (2.50, "N4"),
    (2.85, "N3"),
    (3.05, "N2"),
    (3.30, "N1"),
)
_DEFAULT_BAND = "N1+"


def difficulty_score(occurrence_counts, unique_counts, unique_weight=0.6):
    """Return (score: float, band: str) for the given per-band count dicts.
    Returns (None, None) when there are no kanji at all."""
    total_occ = sum(occurrence_counts.values())
    total_uniq = sum(unique_counts.values())
    if not total_occ or not total_uniq:
        return None, None
    s_occ = sum(_LEVEL_WEIGHTS[k] * occurrence_counts[k] for k in _BANDS) / total_occ
    s_uniq = sum(_LEVEL_WEIGHTS[k] * unique_counts[k] for k in _BANDS) / total_uniq
    score = unique_weight * s_uniq + (1.0 - unique_weight) * s_occ
    band = _DEFAULT_BAND
    for cutoff, name in _BAND_CUTOFFS:
        if score < cutoff:
            band = name
            break
    return score, band


def _empty_counts():
    return {b: 0 for b in _BANDS}


def _band_of(kanji):
    lv = _LEVELS.get(kanji)
    return f"N{lv}" if lv else "above_n1"


def compute_breakdown(text, coverage_threshold=0.8):
    """Analyze the kanji in `text` and return a structured breakdown.

    `required_level` is computed from *unique* kanji (vocabulary breadth):
    accumulate easiest-first; first band where cumulative share ≥ threshold
    is the level a learner needs to comfortably read the text.

    Returns None for required_level if the text contains no kanji.
    """
    chars = _KANJI_RE.findall(text or "")
    if not chars:
        return {
            "occurrence_counts": _empty_counts(),
            "unique_counts": _empty_counts(),
            "total_kanji": 0,
            "unique_kanji": 0,
            "required_level": None,
            "difficulty_score": None,
            "difficulty_band": None,
        }

    occ = _empty_counts()
    for c in chars:
        occ[_band_of(c)] += 1

    uniq_set = set(chars)
    uniq = _empty_counts()
    for c in uniq_set:
        uniq[_band_of(c)] += 1

    cumulative = 0
    required = "above_n1"
    n_unique = len(uniq_set)
    for band in _BANDS:
        cumulative += uniq[band]
        if cumulative / n_unique >= coverage_threshold:
            required = band
            break

    score, band = difficulty_score(occ, uniq)
    return {
        "occurrence_counts": occ,
        "unique_counts": uniq,
        "total_kanji": len(chars),
        "unique_kanji": n_unique,
        "required_level": required,        # legacy: JLPT band at 80% unique coverage
        "difficulty_score": score,         # blended weighted average, range ~1.0 – 6.0
        "difficulty_band": band,           # N5 … N1+ mapped from difficulty_score
    }


def required_level_score(required):
    """Map band label to a numeric where higher = easier (consistent with jreadability)."""
    return {"N5": 5, "N4": 4, "N3": 3, "N2": 2, "N1": 1, "above_n1": 0}.get(required, None)


def required_level_label(required):
    """Friendly label, e.g. 'N1+' for above_n1."""
    if required is None:
        return None
    return "N1+" if required == "above_n1" else required


def main():
    p = argparse.ArgumentParser(description="JLPT kanji breakdown of Japanese text.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("text", nargs="?", help="Text to analyze.")
    src.add_argument("--file", help="Read text from this file.")
    src.add_argument("--stdin", action="store_true", help="Read text from stdin.")
    p.add_argument("--threshold", type=float, default=0.8,
                   help="Unique-kanji coverage threshold for required level (default 0.8).")
    p.add_argument("--json", action="store_true", help="Emit raw JSON.")
    args = p.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.stdin:
        text = sys.stdin.read()
    else:
        text = args.text

    b = compute_breakdown(text, args.threshold)
    if args.json:
        print(json.dumps(b, ensure_ascii=False, indent=2))
        return

    print(f"Total kanji:   {b['total_kanji']}")
    print(f"Unique kanji:  {b['unique_kanji']}")
    print(f"Difficulty:    {b['difficulty_band'] or '(no kanji)'}"
          + (f"  (score {b['difficulty_score']:.2f})" if b['difficulty_score'] is not None else ""))
    print(f"Required(80%): {required_level_label(b['required_level']) or '(no kanji)'}  (legacy)")
    print()
    print(f"{'Band':<10}{'Occurrences':>14}{'Unique':>10}")
    for band in _BANDS:
        print(f"{band:<10}{b['occurrence_counts'][band]:>14}{b['unique_counts'][band]:>10}")


if __name__ == "__main__":
    main()
