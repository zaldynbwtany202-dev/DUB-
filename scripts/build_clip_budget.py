#!/usr/bin/env python3
"""Write the per-clause duration budget that a re-dub has to respect.

The remaining timing error of a dub is not a placement bug: it is the difference between
how long the original narrator spent on a piece of content and how long the dub's matching
clause takes. This tool turns that difference into a table a writer can work from - one row
per clause of the original, with its window, its words, and the number of words the dub
needs to fill it at our own measured pace.

Nothing here synthesizes or edits audio; it only reads the word timings produced by
``analyse_source_for_sync.py`` and the plan produced by ``sync_takes_to_source_timeline.py``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.sentence_anchor import source_units  # noqa: E402

RATE = 2.75  # words per second, measured on the original narration


def build(plan: dict, words: list[dict], *, rate: float, max_clauses: int) -> dict:
    parts = []
    for part in plan["parts"]:
        inside = [row for row in words
                  if part["window_start"] <= float(row["start"]) < part["window_end"]]
        count = min(max_clauses, len(inside))
        if count == 0:
            continue
        # unit boundaries follow the original's own pauses, exactly as the planner sees them
        units = source_units(inside, count, part["window_start"], part["window_end"])
        cues = [row for row in plan["timeline"] if row["part"] == part["index"]]
        rows = []
        for index, unit in enumerate(units):
            spanned = [row for row in inside if unit["start"] <= float(row["start"]) < unit["next"]]
            duration = round(unit["next"] - unit["start"], 3)
            nearby = min(cues, key=lambda row: abs(float(row["start"]) - unit["start"])) if cues else None
            rows.append({"index": index, "start": unit["start"], "end": round(unit["next"], 3),
                         "seconds": duration, "source_words": len(spanned),
                         "source_text": " ".join(str(row["word"]) for row in spanned),
                         "needed_dub_words": max(1, round(duration * rate)),
                         "dub_clause_now": nearby["text"] if nearby and nearby.get("text") else ""})
        parts.append({"part": part["index"], "window_start": part["window_start"],
                      "window_end": part["window_end"], "clauses": len(rows),
                      "dub_speech_seconds": part["speech_seconds"],
                      "source_speech_seconds": round(sum(row["seconds"] for row in rows), 3),
                      "unreachable_anchors": part.get("anchor_conflicts", 0),
                      "budget_words": sum(row["needed_dub_words"] for row in rows),
                      "rows": rows})
    return {"words_per_second": rate, "source_word_count": len(words),
            "source_speech_seconds": plan["source_speech_seconds"],
            "dub_speech_seconds_total": plan["dub_speech_seconds_total"],
            "sentence_timing_error": plan["sentence_timing_error"],
            "rule": ("a dub clause has to be as long as the unit it answers; length comes from "
                     "the word count at the recorded pace, so the budget is written in words - "
                     "no time stretching and no padded silence may substitute for it"),
            "parts": parts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--words", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=RATE,
                        help="words per second the dub narrator sustains comfortably")
    parser.add_argument("--max-clauses", type=int, default=48)
    args = parser.parse_args()
    if not 0.5 <= args.rate <= 10:
        raise ValueError("--rate must be between 0.5 and 10 words per second")
    if not 4 <= args.max_clauses <= 200:
        raise ValueError("--max-clauses belongs in 4..200")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    words = json.loads(args.words.read_text(encoding="utf-8"))["words"]
    doc = build(plan, words, rate=args.rate, max_clauses=args.max_clauses)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"parts": len(doc["parts"]),
                      "clauses": sum(part["clauses"] for part in doc["parts"]),
                      "words_needed_for_a_dense_script": sum(part["budget_words"] for part in doc["parts"]),
                      "current_dub_words": sum(len(part.get("text", "").split())
                                               for part in plan["parts"]),
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
