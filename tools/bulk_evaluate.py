"""Bulk-evaluate ~100 YouTube videos across a range of Japanese difficulty.

For each video we fetch metadata + Japanese subtitles, compute the JLPT kanji
breakdown, and (separately) count how many kanji belong to proper-noun tokens
according to UniDic POS tagging (pos1=名詞, pos2=固有名詞). Proper-noun counts
are reported as a diagnostic — scoring is unchanged.

Saves to tools/bulk_results.json incrementally, so re-running resumes.

Usage:
    python tools/bulk_evaluate.py
"""

import json
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from youtube_search import search as yt_search                       # noqa: E402
from youtube_subtitles import get_info, fetch as fetch_subs, vtt_to_text  # noqa: E402
from kanji_levels import compute_breakdown, required_level_label    # noqa: E402
from fugashi import Tagger                                          # noqa: E402

OUTPUT_PATH = os.path.join(HERE, "bulk_results.json")
KANJI_RE = re.compile(r"[一-鿿]")

# Queries chosen to span beginner → native difficulty. Multiple queries per
# rough target both broaden the channel mix and absorb the inevitable misses
# (English-only "learn japanese" videos, private videos, no-subs cases).
QUERIES = [
    ("N5-target", "easy japanese for beginners"),
    ("N5-target", "japanese basic conversation"),
    ("N5-target", "japanese for kids hiragana"),
    ("N4-target", "easy japanese conversation"),
    ("N4-target", "comprehensible input japanese"),
    ("N4-target", "japanese for travelers"),
    ("N3-target", "intermediate japanese podcast"),
    ("N3-target", "japanese N3 listening practice"),
    ("N3-target", "japanese vlog daily life"),
    ("N2-target", "japanese news easy"),
    ("N2-target", "japanese interview"),
    ("N2-target", "japanese documentary"),
    ("N1-target", "japanese drama scene"),
    ("N1-target", "japanese variety show"),
    ("N1-target", "japanese tv news"),
]


def find_ja_url(info):
    """Return (kind, url) for the best Japanese subtitle track, or None."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for tracks, kind in ((manual, "manual"), (auto, "auto")):
        for key, entries in tracks.items():
            if key.split("-")[0] == "ja":
                for fmt in ("vtt", "srv3", "ttml"):
                    for e in entries:
                        if e.get("ext") == fmt:
                            return kind, e["url"]
                if entries:
                    return kind, entries[0].get("url")
    return None


def count_proper_noun_kanji(text, tagger):
    """Count kanji that appear inside 固有名詞 tokens."""
    occ = []
    surfaces = []
    seen_surfaces = set()
    for tok in tagger(text):
        f = tok.feature
        if f.pos1 == "名詞" and f.pos2 == "固有名詞":
            kanji_in = KANJI_RE.findall(tok.surface)
            if kanji_in:
                occ.extend(kanji_in)
                if tok.surface not in seen_surfaces and len(surfaces) < 15:
                    surfaces.append(tok.surface)
                    seen_surfaces.add(tok.surface)
    return {
        "total_occurrences": len(occ),
        "unique_kanji": len(set(occ)),
        "sample_surfaces": surfaces,
    }


def evaluate_one(vid, tagger):
    try:
        info = get_info(vid)
    except Exception as e:
        return {"error": f"info: {str(e)[:120]}"}
    pick = find_ja_url(info)
    if not pick:
        return {"error": "no japanese subs"}
    kind, url = pick
    try:
        text = vtt_to_text(fetch_subs(url))
    except Exception as e:
        return {"error": f"subs: {str(e)[:120]}"}
    if not text.strip():
        return {"error": "empty subs"}
    bd = compute_breakdown(text)
    if bd["total_kanji"] == 0:
        return {"error": "no kanji"}
    proper = count_proper_noun_kanji(text, tagger)
    return {
        "id": vid,
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "subtitle_kind": kind,
        "required_level": required_level_label(bd["required_level"]),
        "total_kanji": bd["total_kanji"],
        "unique_kanji": bd["unique_kanji"],
        "occurrence_counts": bd["occurrence_counts"],
        "unique_counts": bd["unique_counts"],
        "proper_noun_kanji": proper,
    }


def load_state():
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("results", {}), d.get("skipped", {})
        except Exception:
            pass
    return {}, {}


def save_state(results, skipped):
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"results": results, "skipped": skipped},
                  f, ensure_ascii=False, indent=2)


def summarize(results):
    if not results:
        print("No results to summarize.")
        return

    by_level = {}
    by_target_x_level = {}
    proper_occ_total = 0
    proper_unique_total = 0
    videos_with_proper = 0
    proper_share_samples = []

    for v in results.values():
        lv = v["required_level"] or "?"
        by_level[lv] = by_level.get(lv, 0) + 1
        tgt = v.get("query_target", "?")
        by_target_x_level.setdefault(tgt, {})
        by_target_x_level[tgt][lv] = by_target_x_level[tgt].get(lv, 0) + 1

        p = v["proper_noun_kanji"]
        proper_occ_total += p["total_occurrences"]
        proper_unique_total += p["unique_kanji"]
        if p["total_occurrences"] > 0:
            videos_with_proper += 1
        if v["total_kanji"]:
            proper_share_samples.append(p["total_occurrences"] / v["total_kanji"])

    n = len(results)
    print(f"\n=== Summary across {n} videos ===\n")

    print("Required-level distribution:")
    for lv in ["N5", "N4", "N3", "N2", "N1", "N1+"]:
        cnt = by_level.get(lv, 0)
        bar = "█" * cnt
        print(f"  {lv:<4} {cnt:>3}  {bar}")
    print()

    print("Query target vs inferred level:")
    print(f"  {'target':<14}{'N5':>5}{'N4':>5}{'N3':>5}{'N2':>5}{'N1':>5}{'N1+':>6}")
    for tgt in sorted(by_target_x_level):
        row = by_target_x_level[tgt]
        cells = "".join(f"{row.get(lv, 0):>5}" for lv in ['N5','N4','N3','N2','N1'])
        cells += f"{row.get('N1+', 0):>6}"
        print(f"  {tgt:<14}{cells}")
    print()

    avg_share = sum(proper_share_samples) / len(proper_share_samples) if proper_share_samples else 0
    print(f"Proper-noun kanji (固有名詞) — diagnostic only, scoring unchanged:")
    print(f"  videos with at least one proper-noun kanji:  {videos_with_proper}/{n}")
    print(f"  total proper-noun kanji occurrences:         {proper_occ_total}")
    print(f"  sum of unique proper-noun kanji per video:   {proper_unique_total}")
    print(f"  average share of kanji that are proper-noun: {avg_share:.1%}")


def main():
    results, skipped = load_state()
    print(f"Resumed: {len(results)} evaluated, {len(skipped)} skipped",
          file=sys.stderr)

    # Gather candidate IDs across all queries.
    candidates = []
    seen = set(results) | set(skipped)
    for label, query in QUERIES:
        print(f"search: {query}", file=sys.stderr)
        try:
            raw = yt_search(query, 12)
        except Exception as e:
            print(f"  search failed: {e}", file=sys.stderr)
            continue
        for r in raw or []:
            if not r:
                continue
            vid = r.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            candidates.append((label, vid, r.get("title", "")))

    print(f"\nUnique candidates: {len(candidates)}", file=sys.stderr)

    tagger = Tagger()
    target_n = 100
    done = len(results)
    for i, (label, vid, title) in enumerate(candidates):
        if done >= target_n:
            print(f"\nReached {target_n} videos; stopping.", file=sys.stderr)
            break
        print(f"[{done + 1}/{target_n}] {vid}  ({label})  {title[:50]}",
              file=sys.stderr)
        out = evaluate_one(vid, tagger)
        if "error" in out:
            skipped[vid] = {"target": label, "title": title, "error": out["error"]}
            print(f"  skip: {out['error']}", file=sys.stderr)
        else:
            out["query_target"] = label
            results[vid] = out
            done += 1
        # Save every few items
        if (i + 1) % 5 == 0:
            save_state(results, skipped)
        time.sleep(0.4)

    save_state(results, skipped)
    summarize(results)
    print(f"\nFull results: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
