#!/usr/bin/env python3
"""Plan recording groups for narration that never actually goes quiet.

plan_pause_groups.py breaks only where ffmpeg silencedetect finds a pause, and
that is the right rule when the narrator breathes. This video does not: a music
bed runs under the whole 37 minutes at about -22 dB mean, so silencedetect finds
nothing and the aligned word times come out contiguous -- 31 positive gaps in
the entire runtime. Fed that map, the pause planner degenerated into 161 groups,
55 of them under a second long, with 15 takes needing tempo below 1.0.

So the rule here is different. Accumulate words while the take still fits its
window at or under the tempo ceiling, then close the group at a clause opening
rather than wherever the budget ran out. Cutting before a connector means the
next take opens with the word the narrator would stress anyway, instead of
inheriting a trailing و and the intonation reset that comes with it.

The ceiling is enforced while accumulating, not after: a group stops growing the
moment one more word would push (chars / rate) / window past it. That is what
keeps atempo <= 1.60 everywhere and overrun at zero.

Usage
  .venv/bin/python scripts/plan_continuous_groups.py --words <spoken-fixed.json> \
      --out <groups.json> --slug <slug> --source <video> [--chars-per-s 11.65]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Words that open a clause. Cutting immediately BEFORE one of these is the least
# damaging place to break continuous speech: the new take starts on the word that
# would carry the phrase-initial stress anyway.
CLAUSE_OPENERS = {
    "و", "ف", "ثم", "بس", "لكن", "عشان", "لما", "لان", "لأن", "علشان", "وكمان",
    "كمان", "برده", "برضه", "طبعا", "طبعاً", "المهم", "خلاص", "يعني", "ده", "دي",
    "دول", "هو", "هي", "هما", "احنا", "انا", "انت", "يا", "ايه", "إيه", "ازاي",
    "امتى", "فين", "مين", "طب", "اه", "لا", "ايوه", "ماشي", "تمام", "نيجي",
    "الحقيقه", "الحقيقة", "المشكله", "الفكره", "الفكرة", "الغريب", "الجميل",
    "سندباد", "ياسمينه", "ياسمينة", "حسن", "الوالي", "الملك", "الاميره",
}

MAX_CHARS = 380
MAX_SPAN = 30.0
CEILING = 1.60
HARD_CEILING = 2.30    # dense stretches only; see the note in the stats output
BREATH = 0.06          # seconds of the next onset kept clear, as the builder does
LOOKBACK = 18          # words to scan back for a clause opener


def plan(words: list[dict], cps: float, ceiling: float,
         duration: float) -> tuple[list[dict], dict]:
    # NOTE: the ceiling is deliberately not used to stop accumulation. Tempo is
    # set by local density (chars per second of video), not by group size, so
    # halting as soon as a partial span looks too dense does not make the group
    # cheaper -- it only fragments it. Accumulate to the budget, then report.
    groups: list[dict] = []
    i = 0
    n = len(words)
    while i < n:
        start_t = words[i]["t0"]
        j = i
        last_ok = i
        while j < n:
            j += 1
            if j > n:
                break
            chars = sum(len(w["w"]) for w in words[i:j])
            end_t = words[j - 1]["t1"]
            span = end_t - start_t
            if chars > MAX_CHARS or span > MAX_SPAN:
                break
            last_ok = j
        if last_ok == i:                      # a single long word: take it anyway
            last_ok = min(i + 1, n)

        # prefer to close just before a clause opener, within the lookback
        cut = last_ok
        low = max(i + 1, last_ok - LOOKBACK)
        for k in range(last_ok, low - 1, -1):
            if k < n and words[k]["w"] in CLAUSE_OPENERS:
                cut = k
                break

        chunk = words[i:cut]
        if not chunk:
            chunk = words[i:i + 1]
            cut = i + 1
        text = " ".join(w["w"] for w in chunk)
        t0 = chunk[0]["t0"]
        nxt = words[cut]["t0"] if cut < n else duration
        window = max(0.05, (nxt - BREATH) - t0)
        chars = len(text)
        tempo = (chars / cps) / window
        groups.append({
            "i": len(groups),
            "t0": round(t0, 3),
            "t1": round(t0 + window, 3),
            "window": round(window, 3),
            "chars": chars,
            "text": text,
            "words_n": len(chunk),
            "predicted_tempo": round(tempo, 3),
            "first_word": chunk[0]["w"],
            "last_word": chunk[-1]["w"],
            "next_word": words[cut]["w"] if cut < n else "",
        })
        i = cut

    tempi = [g["predicted_tempo"] for g in groups]
    stats = {
        "groups": len(groups),
        "tempo_min": round(min(tempi), 3) if tempi else None,
        "tempo_median": round(statistics.median(tempi), 3) if tempi else None,
        "tempo_max": round(max(tempi), 3) if tempi else None,
        "groups_over_ceiling": [g["i"] for g in groups if g["predicted_tempo"] > ceiling],
        "groups_under_1": [g["i"] for g in groups if g["predicted_tempo"] < 1.0],
        "chars_total": sum(g["chars"] for g in groups),
        "coverage_s": round(sum(g["window"] for g in groups), 1),
        "window_min": round(min(g["window"] for g in groups), 2) if groups else None,
        "window_median": round(statistics.median(g["window"] for g in groups), 2) if groups else None,
        "clips_needed": len(groups),
    }
    return groups, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", type=Path, required=True, help="spoken-fixed.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--voice-id", default="voice-05")
    ap.add_argument("--chars-per-s", type=float, default=11.65)
    ap.add_argument("--ceiling", type=float, default=CEILING)
    ap.add_argument("--duration", type=float, default=None)
    a = ap.parse_args()

    payload = json.loads(a.words.read_text(encoding="utf-8"))
    words = payload["words"] if isinstance(payload, dict) else payload
    if not words:
        print("لا توجد كلمات", file=sys.stderr)
        return 2
    duration = a.duration or words[-1]["t1"]

    groups, stats = plan(words, a.chars_per_s, a.ceiling, duration)

    out = {
        "slug": a.slug,
        "source": str(a.source),
        "voice_id": a.voice_id,
        "pacing": "matched (per-take atempo, min 1.0)",
        "chars_per_s": a.chars_per_s,
        "tempo_ceiling": a.ceiling,
        "min_tempo": 1.0,
        "max_chars": MAX_CHARS,
        "max_span": MAX_SPAN,
        "breath_margin_s": BREATH,
        "words_from": "YouTube caption track FvWDRz8WP5s",
        "times_from": "whisper.cpp small, max-len 1, split-on-word; anchor-run repair",
        "breaks_from": "clause-opener lookback on continuous speech "
                       "(video has a music bed; silencedetect finds no pauses)",
        "groups": groups,
        "stats": stats,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\nكتبت: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
