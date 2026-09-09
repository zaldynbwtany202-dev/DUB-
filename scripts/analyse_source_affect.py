#!/usr/bin/env python3
"""Turn the video's own dynamics into a per-segment delivery plan for the narrator.

Flat narration over an exciting scene is a dubbing failure no timing check can see. This tool
measures what the original does - loudness, pitch movement, pace, picture motion, cut density -
for every stretch of the original's speech, converts those into a named register with its
evidence, and pairs each row with the word budget from the phrase budget so the writer sees the
content length and the delivery in the same place.

It writes a json map and prints a table; it never touches audio.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_takes_to_source_timeline import SR, decode  # noqa: E402
from youtube_auto_dub.affect import (WRITING_RULES, classify, frame_level, f0_track,  # noqa: E402
                                     motion_stats, picture_energy, semitone_spread, zscores)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--pauses", type=Path, required=True,
                        help="source-analysis/vocal-pauses.json: the original's own speech stretches")
    parser.add_argument("--words", type=Path, required=True, help="source-analysis/words.json")
    parser.add_argument("--script", type=Path, required=True, help="dub script, for the part windows")
    parser.add_argument("--phrase-budget", type=Path, default=None,
                        help="timing-repair/phrase-budget.json, to put the word budget on each row")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    audio = decode(args.source, SR)
    spans = [list(map(float, span)) for span in
             json.loads(args.pauses.read_text(encoding="utf-8"))["spans"]]
    words = json.loads(args.words.read_text(encoding="utf-8"))["words"]
    takes = json.loads(args.script.read_text(encoding="utf-8"))["takes"]
    budgets: dict[tuple[int, int], dict] = {}
    if args.phrase_budget and args.phrase_budget.is_file():
        document = json.loads(args.phrase_budget.read_text(encoding="utf-8"))
        for part in document["parts"]:
            for row in part["rows"]:
                budgets[(int(part["part"]), int(row["segment"]))] = row
    motion, cuts = picture_energy(args.source, fps=args.fps)
    times = (np.arange(motion.size) + 1) / args.fps

    rows: list[dict] = []
    for take in sorted(takes, key=lambda item: int(item["index"])):
        part = int(take["index"])
        start, stop = float(take["start"]), float(take["end"])
        mine = [span for span in spans if span[0] >= start - 0.5 and span[1] <= stop + 0.5]
        for index, (low, high) in enumerate(mine):
            piece = audio[int(low * SR):int(high * SR)]
            if piece.size < int(0.2 * SR):
                continue
            levels = frame_level(piece, SR)
            inside = [row for row in words if low - 0.2 <= float(row["start"]) < high + 0.2]
            stats = motion_stats(motion, times, low, high, cuts)
            seconds = high - low
            row = {"part": part, "segment": index, "start": round(low, 3), "end": round(high, 3),
                   "seconds": round(seconds, 3), "words": len(inside),
                   "rate": round(len(inside) / max(0.5, seconds), 3),
                   "level_db": round(float(np.mean(levels[levels > np.percentile(levels, 20)])), 2),
                   "loud_p90_db": round(float(np.percentile(levels, 90)), 2),
                   **f0_summary(piece), **stats,
                   "crowd": round(min(1.0, sum(1 for _ in inside) * 0.34 / max(0.5, seconds)), 3)}
            budget = budgets.get((part, index))
            if budget:
                row["needed_dub_words"] = budget["needed_dub_words"]
                row["source_text"] = budget["source_text"]
            rows.append(row)

    for key, label in (("level_db", "level_z"), ("f0_mean", "f0_mean_z"), ("f0_spread", "f0_spread_z"),
                       ("rate", "rate_z"), ("motion", "motion_z"), ("cut_rate_per_min", "cut_z")):
        scores = zscores([row[key] for row in rows])
        for row, value in zip(rows, scores):
            row[label] = round(float(value), 3)
    for row in rows:
        register, reasons = classify(level_z=row["level_z"], f0_mean_z=row["f0_mean_z"],
                                    f0_spread_z=row["f0_spread_z"], rate_z=row["rate_z"],
                                    motion_z=row["motion_z"], cut_z=row["cut_z"],
                                    crowd=row["crowd"])
        row["suggested_register"] = register
        row["register"] = register
        row["why"] = reasons
        rules = WRITING_RULES[register]
        row["delivery"] = {"rate_multiplier": rules["rate"], "punch": rules["punch"],
                           "sentences": rules["sentences"], "rule": rules["note"]}

    parts = []
    for take in sorted(takes, key=lambda item: int(item["index"])):
        mine = [row for row in rows if row["part"] == int(take["index"])]
        if not mine:
            continue
        parts.append({"part": int(take["index"]), "window_start": float(take["start"]),
                      "window_end": float(take["end"]), "segments": len(mine),
                      "registers": dict(Counter(row["register"] for row in mine)),
                      "mean_rate": round(float(np.mean([row["rate"] for row in mine])), 3),
                      "mean_loud_db": round(float(np.mean([row["level_db"] for row in mine])), 2),
                      "mean_spread": round(float(np.mean([row["f0_spread"] for row in mine])), 2),
                      "rows": mine})
    document = {"method": ("energy frame 50 ms; F0 by autocorrelation (65-400 Hz) reported as "
                           "median and semitone spread; picture at 4 fps, 64x36 grayscale, "
                           "motion = mean frame difference, cut = spike over 6x the local median"),
                "registers": list(WRITING_RULES),
                "segments_total": len(rows),
                "registers_total": dict(Counter(row["register"] for row in rows)),
                "spread_range_st": [round(min(row["f0_spread"] for row in rows), 2),
                                    round(max(row["f0_spread"] for row in rows), 2)] if rows else [],
                "parts": parts}
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(f"{'part':>4} {'seg':>3} {'start':>7} {'dur':>5} {'rate':>5} {'dB':>6} {'st':>5} "
          f"{'mot':>6} {'cuts':>4}  register")
    for part in parts:
        for row in part["rows"]:
            print(f"{row['part']:>4} {row['segment']:>3} {row['start']:>7.1f} {row['seconds']:>5.1f} "
                  f"{row['rate']:>5.2f} {row['level_db']:>6.1f} {row['f0_spread']:>5.2f} "
                  f"{row['motion']:>6.2f} {row['cuts']:>4}  {row['register']}"
                  f"{' (+' + str(row.get('needed_dub_words', 0)) + ' و)' if 'needed_dub_words' in row else ''}")
    print(json.dumps({key: value for key, value in document.items() if key != "parts"},
                     ensure_ascii=False, indent=2))
    return 0


def f0_summary(piece: np.ndarray) -> dict[str, float]:
    """Median pitch and its semitone movement over one stretch."""
    track = f0_track(piece, SR)
    live = track[np.isfinite(track)]
    if live.size < 6:
        return {"f0_mean": 0.0, "f0_spread": 0.0, "voiced_frames": int(live.size)}
    return {"f0_mean": round(float(np.median(live)), 1),
            "f0_spread": round(semitone_spread(track), 2), "voiced_frames": int(live.size)}


if __name__ == "__main__":
    raise SystemExit(main())
