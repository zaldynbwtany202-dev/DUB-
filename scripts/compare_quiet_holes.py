#!/usr/bin/env python3
"""Compare two narration masters on one measure: audible silence during original speech.

A dub that leaves the original talking over silence reads as a gap, and a dub that only
fills the *window* while its clauses sit out of phase reads as "the sync is bad". Both are
measured here from samples - the peak of 10 ms blocks below -55 dBFS - intersected with the
speech spans of the original, so no speech detector setting can flatter one render and
penalise another.

The masters are the dry narration files the mixers consumed; the delivered mix is not used
because the background bed hides onsets and lifts the noise floor.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SR = 44100


def _verifier():
    path = ROOT / "scripts" / "verify_source_sync_render.py"
    spec = importlib.util.spec_from_file_location("_verify_for_holes", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def quiet_holes(master: Path, source_spans: list[list[float]], *, floor_db: float = -55.0,
                min_hole: float = 0.05) -> dict:
    audio, rate = sf.read(master, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, int(0.01 * rate))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    loud = np.max(np.abs(padded.reshape(-1, frame)), axis=1) > 10 ** (floor_db / 20)
    edges = np.diff(np.r_[False, loud, False].astype(np.int8))
    starts, stops = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    runs: list[list[float]] = []
    if len(starts) and starts[0] > 0:
        runs.append([0.0, starts[0] * frame / rate])
    runs.extend([[stop * frame / rate, nxt * frame / rate] for stop, nxt in zip(stops, starts[1:])])
    if len(stops) and stops[-1] < len(loud):
        runs.append([stops[-1] * frame / rate, len(loud) * frame / rate])
    holes: list[dict[str, float]] = []
    for low, high in runs:
        for source_start, source_end in source_spans:
            overlap = min(high, source_end) - max(low, source_start)
            if overlap > 1e-6:
                holes.append({"start": round(max(low, source_start), 3),
                              "end": round(min(high, source_end), 3), "duration": round(overlap, 3)})
                break
    kept = [row for row in holes if row["duration"] > min_hole]
    durations = np.array([row["duration"] for row in kept]) if kept else np.array([0.0])
    return {"method": f"sample peak below {floor_db} dBFS over {frame / rate * 1000:.0f} ms blocks, "
                      f"holes longer than {min_hole} s kept",
            "speech_seconds": round(len(audio) / rate, 3),
            "count": len(kept), "total_seconds": round(float(durations.sum()), 3),
            "max_seconds": round(float(durations.max()), 3),
            "mean_seconds": round(float(durations.mean()), 3),
            "p90_seconds": round(float(np.percentile(durations, 90)), 3),
            "over_0_7_seconds": int((durations > 0.7).sum()),
            "longest": sorted(kept, key=lambda row: -row["duration"])[:8]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--master", type=str, required=True, action="append",
                        help="label=path, repeat once per render to compare")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    verify = _verifier()
    source_audio = verify.decode(args.source, SR)
    spans = verify.vad(source_audio, SR)
    rows = {}
    for item in args.master:  # str, not Path: the label and the file share one argument
        label, _, path = item.partition("=")
        if not path:
            raise ValueError("--master takes label=path")
        rows[label] = quiet_holes(Path(path), spans)
    document = {"source": str(args.source), "source_speech_spans": len(spans),
                "note": "lower total, shorter max hole and fewer holes over 0.7 s are all better",
                "renders": rows}
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(json.dumps({label: {key: value for key, value in row.items() if key != "longest"}
                      for label, row in rows.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
