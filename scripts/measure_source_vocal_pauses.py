#!/usr/bin/env python3
"""Measure where the original narrator actually stops, by listening to the vocals only.

The source carries a continuous music bed, so an energy VAD on the mix never finds silence, and
the ASR word timings are DTW envelopes that bridge real breaths (the shipped file has two
gaps >= 0.35 s in eleven minutes, which is not how people speak). Both hide the thing the
planner most needs: the moments the original left the air.

The neural background estimate already in the repository is subtracted from the source audio,
which leaves an a-cappella residual whose silence is the narrator's own. The pauses found in it
are the anchor candidates; every number the planner invents from word gaps is a guess.

Writes a json report and prints the histogram; nothing is modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sync_takes_to_source_timeline import SR, decode, _merge  # noqa: E402


def residual(source: Path, background: Path, *, fit: bool = True):
    """Source minus the bed estimate; the scale is fitted so a gain mismatch does not leave music.

    Without the fit the subtraction leaves most of the backing track in the residual, and its
    energy hides every breath the narrator took, which reads as "no pauses" and is a lie.
    """
    voice = decode(source, SR)
    bed = decode(background, SR)
    count = min(len(voice), len(bed))
    voice, bed = voice[:count], bed[:count]
    scale = float(np.dot(voice, bed) / np.dot(bed, bed)) if fit and np.dot(bed, bed) > 0 else 1.0
    return voice - scale * bed, scale


def speech_spans(audio: np.ndarray, *, floor_db: float, frame_ms: float = 10.0) -> list[list[float]]:
    """Frames above the measured noise floor, so the threshold follows the recording."""
    frame = max(1, int(round(SR * frame_ms / 1000.0)))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    peak = float(np.max(rms)) or 1.0
    floor = float(np.percentile(rms, 10))
    threshold = max(floor * 2.0, peak * 10 ** (floor_db / 20.0))
    loud = rms > threshold
    edges = np.diff(np.r_[False, loud, False].astype(np.int8))
    return [[float(low * frame / SR), float(high * frame / SR)]
            for low, high in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
            if high > low]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True,
                        help="restored neural background wav (same rate and start as the source)")
    parser.add_argument("--words", type=Path, help="optional word timing file for comparison")
    parser.add_argument("--floor-db", type=float, default=-30.0,
                        help="speech threshold in dB below the loudest frame of the residual")
    parser.add_argument("--min-speech", type=float, default=0.25)
    parser.add_argument("--no-fit", action="store_true", help="subtract the bed at unity gain")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    audio, scale = residual(args.source, args.background, fit=not args.no_fit)
    bed = decode(args.background, SR)[:len(audio)]
    energy = float(np.mean(audio ** 2)) / max(1e-12, float(np.mean(bed ** 2)))
    print(f"bed scale fitted to {scale:.4f}; residual power is {energy:.1%} of the bed's")
    spans = _merge(speech_spans(audio, floor_db=args.floor_db), 0.12)
    spans = [span for span in spans if span[1] - span[0] >= args.min_speech]
    if not spans:
        raise SystemExit("no speech found - lower --floor-db or check the background file")
    pauses = [{"start": round(spans[index][1], 3), "end": round(spans[index + 1][0], 3),
               "seconds": round(spans[index + 1][0] - spans[index][1], 3)}
              for index in range(len(spans) - 1)]
    durations = [round(span[1] - span[0], 3) for span in spans]
    lengths = [pause["seconds"] for pause in pauses]
    stats = {key: sum(1 for value in lengths if value >= key) for key in (0.15, 0.25, 0.35, 0.5, 0.7, 1.0)}
    report = {"method": f"energy VAD on (source - neural background), frames 10 ms, "
                        f"{args.floor_db} dB below the loudest frame, floor = 2x the 10th percentile",
              "sample_rate": SR, "duration": round(len(audio) / SR, 3),
              "bed_scale_fitted": round(scale, 5), "residual_power_over_bed_power": round(energy, 5),
              "speech_seconds": round(sum(durations), 3), "segments": len(spans),
              "pauses": len(pauses), "pause_seconds_total": round(sum(lengths), 3),
              "pause_count_at_least": stats,
              "pause_mean_seconds": round(float(np.mean(lengths)), 3) if lengths else 0.0,
              "pause_p90_seconds": round(float(np.percentile(lengths, 90)), 3) if lengths else 0.0,
              "pause_max_seconds": round(max(lengths), 3) if lengths else 0.0,
              "segment_mean_seconds": round(float(np.mean(durations)), 3),
              "segment_p90_seconds": round(float(np.percentile(durations, 90)), 3),
              "segment_max_seconds": round(max(durations), 3),
              "spans": [[round(span[0], 3), round(span[1], 3)] for span in spans],
              "pause_list": pauses}
    if args.words and args.words.is_file():
        words = json.loads(args.words.read_text(encoding="utf-8"))["words"]
        gaps = [words[index + 1]["start"] - words[index]["end"] for index in range(len(words) - 1)]
        gaps = [max(0.0, gap) for gap in gaps]
        report["word_gaps_for_comparison"] = {
            "count": len(gaps),
            "at_least": {key: sum(1 for value in gaps if value >= key) for key in (0.15, 0.25, 0.35, 0.5)},
            "max_seconds": round(max(gaps), 3) if gaps else 0.0,
            "note": "ASR token envelopes; if these are far smaller than the pause numbers above, "
                    "the word timings bridge real breaths and cannot be the anchor source",
        }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("spans", "pause_list")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
