#!/usr/bin/env python3
"""Report, chunk by chunk, whether a take's speech fits the time the original allotted to it.

Anchoring can only move silence. If a chunk of the dub is longer than the stretch of the
original that covers the same content, its successor's moment has already passed and no
placement can fix it - the wording has to change. This tool measures exactly that, per
chunk (the granularity the planner itself places at), and prints the word adjustment a
re-record needs, so the rewrite is driven by the audio instead of by how long the sentence
looks on paper. `fillable_ratio` is the part-level verdict: speech divided by the time the
original allotted. Below 0.95 the part leaves the screen waiting; above 1.02 it cannot be
placed in phase at all.

Read-only: it never writes audio, only a json report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_takes_to_source_timeline import (SR, decode, measure,  # noqa: E402
                                                    silero_spans, _merge)
from youtube_auto_dub.pause_sync import sentence_indices  # noqa: E402
from youtube_auto_dub.sentence_anchor import source_units, split_sentences  # noqa: E402

OVER = 1.06   # a chunk this much longer than its allotment must be shortened
UNDER = 0.80  # and a chunk this much shorter leaves the screen waiting for the dub


def analyse(script: Path, words: list[dict], *, min_gap: float, min_speech: float,
            edge_pad: float, tolerance: float) -> dict:
    doc = json.loads(script.read_text(encoding="utf-8"))
    parts = []
    for take in sorted(doc["takes"], key=lambda item: item["index"]):
        audio = decode((script.parent / take["audio"]).resolve(), SR)
        chunks, pauses = measure(audio, SR, min_gap=min_gap, max_pad=edge_pad, min_speech=min_speech)
        durations = [chunk["duration"] for chunk in chunks]
        clauses = take.get("clauses") or split_sentences(str(take["text"]))
        # Judge at the planner's own granularity: a chunk of the take against the stretch of
        # the original allotted to it. Mapping sentences onto chunks is degenerate when a part
        # has more clauses than chunks (sentence_indices collapses every start to 0), so the
        # words shown for a chunk are those its clauses carry, split proportionally.
        units = source_units(words, len(durations), float(take["start"]), float(take["end"]),
                             min_gap=min_gap)
        rate = sum(len(cl.split()) for cl in clauses) / max(0.001, sum(durations))
        rows = []
        for chunk, duration in enumerate(durations):
            first = chunk * len(clauses) // len(durations)
            last = (chunk + 1) * len(clauses) // len(durations)
            mine = clauses[first:last] or [clauses[min(first, len(clauses) - 1)]]
            allotted = max(0.05, units[chunk]["end"] - units[chunk]["start"])
            ratio = duration / allotted
            words_now = sum(len(cl.split()) for cl in mine)
            recommended = max(1, round(allotted * rate))
            rows.append({"chunk": chunk, "clause": first, "clauses": [cl.strip() for cl in mine],
                         "start": round(units[chunk]["start"], 3),
                         "allotted": round(allotted, 3), "speech": round(duration, 3),
                         "ratio": round(ratio, 3), "words": words_now,
                         "words_recommended": recommended, "words_delta": recommended - words_now,
                         "verdict": ("shorten" if ratio > OVER + tolerance else
                                     "lengthen" if ratio < UNDER - tolerance else "fits")})
        bad = [row for row in rows if row["verdict"] != "fits"]
        parts.append({"part": int(take["index"]), "window_start": float(take["start"]),
                      "window_end": float(take["end"]), "chunks": len(durations),
                      "clauses": len(clauses), "units": len(units),
                      "speech_seconds": round(sum(durations), 3),
                      "window_seconds": round(float(take["end"]) - float(take["start"]), 3),
                      "words_per_second": round(sum(len(cl.split()) for cl in clauses)
                                                / max(0.001, sum(durations)), 3),
                      "fillable_ratio": round(sum(r["speech"] for r in rows)
                                              / max(0.001, sum(r["allotted"] for r in rows)), 3),
                      "rows": rows, "mismatched": len(bad),
                      "shorten_by_words": sum(max(0, -r["words_delta"]) for r in bad),
                      "lengthen_by_words": sum(max(0, r["words_delta"]) for r in bad)})
    return {"rule": f"a chunk may not exceed the original's allotment (>{OVER}x shorten, "
                    f"<{UNDER}x lengthen); speech lengths, not placement, decide the phase",
            "parts": parts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--words", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--min-gap", type=float, default=0.12)
    parser.add_argument("--min-speech", type=float, default=0.3)
    parser.add_argument("--edge-pad", type=float, default=0.1)
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="extra slack in the ratio before a chunk is called mismatched")
    args = parser.parse_args()
    if not 0.02 <= args.min_gap <= 1.0 or not 0.0 <= args.min_speech <= 2.0:
        raise ValueError("--min-gap belongs in 0.02..1.0 and --min-speech in 0..2.0")
    words = json.loads(args.words.read_text(encoding="utf-8"))["words"]
    report = analyse(args.script, words, min_gap=args.min_gap, min_speech=args.min_speech,
                     edge_pad=args.edge_pad, tolerance=args.tolerance)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    header = (f"{'part':>4} {'chunks':>6} {'clauses':>7} {'speech':>7} {'window':>7} "
              f"{'w/s':>5} {'fill':>5} {'mismatch':>8} {'+words':>7} {'-words':>7}")
    print(header)
    for part in report["parts"]:
        print(f"{part['part']:>4} {part['chunks']:>6} {part['clauses']:>7} "
              f"{part['speech_seconds']:>7.1f} {part['window_seconds']:>7.1f} "
              f"{part['words_per_second']:>5.2f} {part['fillable_ratio']:>5.2f} "
              f"{part['mismatched']:>8} {part['lengthen_by_words']:>7} {part['shorten_by_words']:>7}")
    worst = sorted(((part["part"], row) for part in report["parts"] for row in part["rows"]),
                   key=lambda pair: -abs(pair[1]["ratio"] - 1))[:12]
    print("\nworst chunks (speech vs the time the original allotted, with the word change needed):")
    for part_index, row in worst:
        print(f"  part {part_index} chunk {row['chunk']:>2} clause {row['clause']:>2}: "
              f"{row['speech']:.2f}s in {row['allotted']:.2f}s = {row['ratio']:.2f}x "
              f"{row['verdict']} ({row['words']}→{row['words_recommended']} words)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
