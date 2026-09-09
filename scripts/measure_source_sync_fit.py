#!/usr/bin/env python3
"""Measure whether each approved take can cover original speech inside its window.

Speech activity for the source is taken from two independent views:

* word timings produced by ``analyse_source_for_sync.py`` (what was actually said)
* Silero VAD on the original audio (what a listener hears as speech)

For every take the script measures VAD speech spans and reports the fit against
the source span, so a planning decision can be made from numbers instead of
guesses. This tool never synthesizes speech and never changes media.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402

STEP = 0.01
SR = 16000


def load_16k(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def silero_mask(audio: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=threshold, min_speech_duration_ms=80,
                      min_silence_duration_ms=120, speech_pad_ms=40, max_speech_duration_s=30)
    mask = np.zeros(math.ceil(len(audio) / SR / STEP), dtype=bool)
    for seg in get_speech_timestamps(audio, opts, sampling_rate=SR):
        a = max(0, math.floor(seg["start"] / SR / STEP))
        b = min(len(mask), math.ceil(seg["end"] / SR / STEP))
        mask[a:b] = True
    return mask


def mask_spans(mask: np.ndarray, min_seconds: float = 0.25) -> list[dict]:
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return [{"start": round(a * STEP, 3), "end": round(b * STEP, 3), "duration": round((b - a) * STEP, 3)}
            for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
            if (b - a) * STEP >= min_seconds]


def merge_units(spans: list[dict], max_gap: float) -> list[dict]:
    units: list[dict] = []
    for span in spans:
        if units and span["start"] - units[-1]["end"] <= max_gap:
            units[-1]["end"] = max(units[-1]["end"], span["end"])
            units[-1]["duration"] = round(units[-1]["end"] - units[-1]["start"], 3)
        else:
            units.append(dict(span))
    return units


def total(mask: np.ndarray, start: float = 0.0, end: float | None = None) -> float:
    a = round(start / STEP)
    b = len(mask) if end is None else min(len(mask), round(end / STEP))
    return round(float(mask[a:b].sum()) * STEP, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True, help="timing repair script.json")
    parser.add_argument("--source", type=Path, required=True, help="source video or audio")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unit-gap", type=float, default=0.35)
    args = parser.parse_args()

    doc = json.loads(args.script.read_text(encoding="utf-8"))
    words_path = args.script.parent / doc.get("source_analysis", "source-analysis/words.json")
    words = json.loads(words_path.read_text(encoding="utf-8"))["words"] if words_path.is_file() else []
    word_spans = merge_units([{"start": w["start"], "end": w["end"], "duration": w["end"] - w["start"]}
                              for w in words], args.unit_gap)

    source_audio = load_16k(args.source)
    source_mask = silero_mask(source_audio)
    duration = len(source_audio) / SR
    source_vad_units = merge_units(mask_spans(source_mask), args.unit_gap)

    rows = []
    for take in sorted(doc["takes"], key=lambda t: t["index"]):
        start, end = float(take["start"]), float(take["end"])
        audio = args.script.parent / take["audio"]
        row = {"index": take["index"], "window_start": start, "window_end": end,
               "window_seconds": round(end - start, 3),
               "words": len([w for w in words if start <= w["start"] < end]),
               "source_word_units": len([u for u in word_spans if start <= u["start"] < end]),
               "source_vad_units": len([u for u in source_vad_units if start <= u["start"] < end]),
               "source_speech_seconds_vad": total(source_mask, start, end),
               "source_speech_seconds_words": round(sum(
                   u["duration"] for u in word_spans if start <= u["start"] < end), 3),
               "audio": str(audio), "audio_present": audio.is_file()}
        if audio.is_file():
            take_audio = load_16k(audio)
            take_mask = silero_mask(take_audio)
            take_spans = mask_spans(take_mask, 0.25)
            row.update({
                "take_duration": round(len(take_audio) / SR, 3),
                "take_speech_seconds": total(take_mask),
                "take_speech_units": len(take_spans),
                "take_speech_start": take_spans[0]["start"] if take_spans else None,
                "take_speech_end": take_spans[-1]["end"] if take_spans else None,
            })
            speech_span = row["take_speech_end"] - row["take_speech_start"]
            row["take_span_seconds"] = round(speech_span, 3)
            row["window_span_seconds"] = round(end - start, 3)
            row["fill_ratio"] = round(row["take_speech_seconds"] / max(row["source_speech_seconds_vad"], .001), 3)
            row["span_ratio"] = round(speech_span / max(row["window_span_seconds"], .001), 3)
            # Worst case: place the take so its speech starts with the first source unit.
            first_unit = min((u["start"] for u in source_vad_units if start <= u["start"] < end), default=start)
            row["needed_acceleration"] = round(max(1.0, (speech_span) / max(end - first_unit, .001)), 4)
            row["status"] = "ok" if row["needed_acceleration"] <= 1.06 else (
                "tight" if row["needed_acceleration"] <= 1.12 else "overpacked")
        else:
            row["status"] = "audio_missing"
        rows.append(row)

    uncovered = total(source_mask)  # full-video source speech for reference
    report = {"method": "Silero VAD on original and takes, 10 ms masks; word spans from ASR checkpoints",
              "source_duration": round(duration, 3), "source_speech_seconds_total": round(uncovered, 3),
              "unit_gap_seconds": args.unit_gap, "takes": rows,
              "note": "span_ratio>1 means the recording is longer than its window even before pauses are used"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({"source_speech_seconds_total": round(uncovered, 3),
                      "words": len(words), "word_units": len(word_spans)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
