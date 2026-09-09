#!/usr/bin/env python3
"""Score how well a dub's delivery tracks the original's, segment by segment.

The affect map says what the original does in each of its speech stretches. This reads the dub
master, aligns its pieces to those same stretches through the plan, and measures three things
that carry "the feeling" in narration:

* ``level_delta_db``  - is the dub as loud (relative to itself) where the original lifted?
* ``spread_ratio``    - semitone pitch movement of the dub divided by the original's; below ~0.7
  is a flat read whatever the timing says;
* ``rate_ratio``      - does the dub quicken where the original quickens?

Plus two curve-level numbers: Pearson correlation of the loudness curves and of the pitch-movement
curves over the matched segments. Correlation near 0 with a good mean level means a monotone read;
that is the failure the eye hears first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_takes_to_source_timeline import SR, decode  # noqa: E402
from youtube_auto_dub.affect import frame_level, f0_track, semitone_spread  # noqa: E402


def summary(audio: np.ndarray) -> dict[str, float]:
    if audio.size < int(0.25 * SR):
        return {"level_db": None, "spread_st": None, "seconds": round(audio.size / SR, 3)}
    levels = frame_level(audio, SR)
    track = f0_track(audio, SR)
    live = track[np.isfinite(track)]
    # same rule as the source side: ignore the quietest fifth of the frames, so a piece's
    # trailing silence cannot be read as a drop in delivery
    voiced_levels = levels[levels > np.percentile(levels, 20)] if levels.size else np.array([0.0])
    return {"level_db": round(float(np.mean(voiced_levels)), 2),
            "spread_st": round(semitone_spread(track), 2) if live.size >= 6 else None,
            "seconds": round(audio.size / SR, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--narration", type=Path, required=True, help="dry dub master (wav)")
    parser.add_argument("--plan", type=Path, required=True, help="timing-repair/pause-plan.json")
    parser.add_argument("--affect-map", type=Path, required=True)
    parser.add_argument("--flat-below", type=float, default=0.7,
                        help="spread ratio under this counts as a flat stretch")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    audio = decode(args.narration, SR)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    mapping = json.loads(args.affect_map.read_text(encoding="utf-8"))
    rows = []
    for part in mapping["parts"]:
        pieces = [row for row in plan["timeline"] if row["part"] == part["part"]]
        for segment in part["rows"]:
            live = [row for row in pieces
                    if segment["start"] - 0.35 <= float(row["start"]) < segment["end"] - 0.1]
            if not live:
                rows.append({"part": part["part"], "segment": segment["segment"],
                             "start": segment["start"], "register": segment["register"],
                             "dub_pieces": 0, "verdict": "uncovered"})
                continue
            low = min(float(row["start"]) for row in live)
            high = max(float(row.get("end", row["start"])) for row in live)
            dub = summary(audio[int(low * SR):int(high * SR)])
            spread_ratio = (round(dub["spread_st"] / segment["f0_spread"], 3)
                            if dub.get("spread_st") and segment.get("f0_spread") else None)
            rows.append({"part": part["part"], "segment": segment["segment"],
                         "start": segment["start"], "seconds": segment["seconds"],
                         "register": segment["register"],
                         "dub_pieces": len(live),
                         "source_level_db": segment["level_db"], "dub_level_db": dub.get("level_db"),
                         "level_delta_db": (round(dub["level_db"] - segment["level_db"], 2)
                                            if dub.get("level_db") is not None else None),
                         "source_spread_st": segment["f0_spread"], "dub_spread_st": dub.get("spread_st"),
                         "spread_ratio": spread_ratio,
                         "source_rate": segment["rate"],
                         "dub_rate": (round(sum(len(str(row["text"]).split()) for row in live)
                                            / max(0.5, high - low), 3) if dub.get("seconds") else None),
                         "verdict": ("flat" if spread_ratio is not None and spread_ratio < args.flat_below
                                     else "uncovered" if not live else "tracks")})
    matched = [row for row in rows if row.get("spread_ratio") is not None]
    levelled = [row for row in rows if row.get("level_delta_db") is not None]
    def corr(a: list[float], b: list[float]) -> float:
        if len(a) < 6 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
            return 0.0
        return round(float(np.corrcoef(a, b)[0, 1]), 3)
    curves = {
        "level_correlation": corr([row["source_level_db"] for row in levelled],
                                 [row["dub_level_db"] for row in levelled]),
        "spread_correlation": corr([row["source_spread_st"] for row in matched],
                                  [row["dub_spread_st"] for row in matched]),
        "rate_correlation": corr([row["source_rate"] for row in matched if row.get("dub_rate")],
                                [row["dub_rate"] for row in matched if row.get("dub_rate")]),
    }
    counts = {"flat": sum(1 for row in rows if row["verdict"] == "flat"),
              "tracks": sum(1 for row in rows if row["verdict"] == "tracks"),
              "uncovered": sum(1 for row in rows if row["verdict"] == "uncovered")}
    verdict = ("flat delivery" if counts["flat"] > counts["tracks"] else
               "delivery tracks the original" if counts["uncovered"] == 0 else
               "delivery mostly tracks; some segments have no dub speech")
    document = {"rule": (f"a stretch is flat when the dub's semitone pitch movement is under "
                         f"{args.flat_below} of the original's; the curve correlations decide "
                         "whether the reading moves with the video at all"),
                "matched_segments": len(matched), "verdict_counts": counts, "curves": curves,
                "mean_spread_ratio": (round(float(np.mean([row["spread_ratio"] for row in matched])), 3)
                                      if matched else None),
                "mean_level_delta_db": (round(float(np.mean([abs(row["level_delta_db"])
                                                             for row in levelled])), 2)
                                       if levelled else None),
                "mean_rate_ratio": (round(float(np.mean([row["dub_rate"] / row["source_rate"]
                                                          for row in matched if row.get("dub_rate")])), 3)
                                    if matched else None),
                "verdict": verdict, "rows": rows}
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(json.dumps({key: value for key, value in document.items() if key != "rows"},
                     ensure_ascii=False, indent=2))
    worst = sorted((row for row in rows if row.get("spread_ratio") is not None),
                   key=lambda row: row["spread_ratio"])[:12]
    print("flattest stretches (dub pitch movement vs the original's):")
    for row in worst:
        print(f"  part {row['part']} seg {row['segment']:>2} at {row['start']:>7.1f}s "
              f"{row['dub_spread_st']:>5.2f} vs {row['source_spread_st']:>5.2f} st "
              f"= {row['spread_ratio']:>5.2f} ({row['register']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
