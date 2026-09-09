#!/usr/bin/env python3
"""Write the per-phrase budget a re-dub needs so that its breaths land where the original's do.

Placement cannot make a dub phase-true when the source barely stops: measured on the vocals,
the original leaves only ~20 s of silence in 676 s (`vocal-pauses.json`), ~125 pauses with a mean
of 0.16 s. A dub that breathes 250 times over that material is audible as a dropout whatever
the placement, and every pause the planner is given has to be spent somewhere in the same window.
So the fix is structural: one dub phrase per source speech segment, as many words as that
segment lasts, and no other pauses. Then chunk count equals anchor count, the mapping is 1:1,
and the phase follows from the durations instead of from a projection.

Rows are the source's own segments; `needed_dub_words` uses the dub voice's measured pace, not
the original's, because that is what decides how long the recorded phrase will be.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def words_per(text: str) -> int:
    return len(re.findall(r"\w+", text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pauses", type=Path, required=True,
                        help="timing-repair/source-analysis/vocal-pauses.json")
    parser.add_argument("--words", type=Path, required=True,
                        help="timing-repair/source-analysis/words.json")
    parser.add_argument("--script", type=Path, required=True,
                        help="the dub script, used only for its part windows")
    parser.add_argument("--rate", type=float, help="dub words per second; measured from the takes "
                                                   "if omitted")
    parser.add_argument("--min-segment", type=float, default=1.2,
                        help="segments shorter than this are folded into the previous one")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    pause_report = json.loads(args.pauses.read_text(encoding="utf-8"))
    spans = [list(map(float, span)) for span in pause_report["spans"]]
    merged = []
    for span in spans:
        if merged and span[1] - span[0] < args.min_segment:
            merged[-1][1] = span[1]
        else:
            merged.append(list(span))
    words = json.loads(args.words.read_text(encoding="utf-8"))["words"]
    takes = json.loads(args.script.read_text(encoding="utf-8"))["takes"]
    if args.rate:
        rate = args.rate
    else:
        speech = sum(float(take["duration"]) for take in takes if take.get("duration"))
        count = sum(words_per(str(take["text"])) for take in takes)
        rate = count / max(0.001, speech)
    parts = []
    for take in sorted(takes, key=lambda item: int(item["index"])):
        start, stop = float(take["start"]), float(take["end"])
        mine = [span for span in merged if span[0] >= start - 0.5 and span[1] <= stop + 0.5]
        if not mine:
            continue
        rows = []
        for index, (low, high) in enumerate(mine):
            inside = [row for row in words if low - 0.2 <= float(row["start"]) < high + 0.2]
            seconds = high - low
            rows.append({"segment": index, "start": round(low, 3), "end": round(high, 3),
                         "seconds": round(seconds, 3), "source_words": len(inside),
                         "source_text": " ".join(str(row["word"]) for row in inside),
                         "needed_dub_words": max(2, round(seconds * rate)),
                         "next_pause_seconds": (round(mine[index + 1][0] - high, 3)
                                                if index + 1 < len(mine) else None)})
        needed = sum(row["needed_dub_words"] for row in rows)
        parts.append({"part": int(take["index"]), "window_start": start, "window_end": stop,
                      "segments": len(rows), "source_speech_seconds": round(
                          sum(row["seconds"] for row in rows), 3),
                      "source_words": sum(row["source_words"] for row in rows),
                      "needed_dub_words": needed,
                      "words_per_segment": round(needed / max(1, len(rows)), 1),
                      "rows": rows})
    document = {"rule": ("one dub phrase per source speech segment, as many words as the segment "
                         f"lasts at {round(rate, 3)} words per second, and no other pauses; "
                         f"segments under {args.min_segment} s are folded so no phrase is asked "
                         "to carry less than a breath"),
                "dub_words_per_second": round(rate, 3),
                "source_segments_total": sum(part["segments"] for part in parts),
                "needed_dub_words_total": sum(part["needed_dub_words"] for part in parts),
                "source_speech_seconds": round(sum(part["source_speech_seconds"] for part in parts), 3),
                "source_pause_seconds": pause_report["pause_seconds_total"],
                "parts": parts}
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    print(f"{'part':>4} {'window':>13} {'segs':>5} {'speech':>7} {'srcW':>5} {'needW':>6} {'w/seg':>6}")
    for part in parts:
        print(f"{part['part']:>4} {part['window_start']:>6.1f}-{part['window_end']:<6.1f} "
              f"{part['segments']:>5} {part['source_speech_seconds']:>7.1f} "
              f"{part['source_words']:>5} {part['needed_dub_words']:>6} "
              f"{part['words_per_segment']:>6}")
    print(json.dumps({key: value for key, value in document.items() if key != "parts"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
