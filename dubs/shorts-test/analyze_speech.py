#!/usr/bin/env python3
"""Map the speech islands of library/shorts-test/source.mp4 before any text exists.

The user supplies the transcript for this one, so no ASR runs here: the only thing
that can be measured without words is *when* the original speaks. That map is what
the transcript gets laid onto (youtube_auto_dub.align_text does the laying), and it
is also what tells the user how much text each window can hold.

Char budgets use the measured rate of the recording voice (voice-00: 11.79 chars/s
including spaces) and the pacing policy approved for matched dubs:
  tempo 1.00  -> the window must not be underfilled (no slowdown allowed)
  tempo ~1.30 -> the median actually observed across the Das takes
  tempo 1.60  -> the approved ceiling for matched pacing

Writes dubs/shorts-test/speech-windows.json; prints a table.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.island_sync import speech_islands  # noqa: E402

SOURCE = ROOT / "library/shorts-test/source.mp4"
OUT = ROOT / "dubs/shorts-test/speech-windows.json"
CACHE_WAV = ROOT / ".cache/shorts-test/source-mono.wav"

CHARS_PER_S = 11.79     # voice-00, measured on 24 Das takes
TEMPO_FLOOR = 1.00
TEMPO_TYPICAL = 1.30
TEMPO_CEILING = 1.60
SILENCE_BREAK = 2.0     # a gap at least this long is a real pause, never filled


def decode_mono(path: Path, sr: int) -> tuple[np.ndarray, int]:
    CACHE_WAV.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE_WAV.exists():
        subprocess.run(
            [ffmpeg_exe(), "-y", "-v", "error", "-i", str(path),
             "-ac", "1", "-ar", str(sr), str(CACHE_WAV)],
            check=True, capture_output=True)
    r = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(CACHE_WAV),
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy(), sr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor-db", type=float, default=-40.0)
    ap.add_argument("--merge-gap", type=float, default=0.18)
    ap.add_argument("--min-dur", type=float, default=0.12)
    args = ap.parse_args()

    audio, sr = decode_mono(SOURCE, 44100)
    total = len(audio) / sr
    islands = speech_islands(audio, sr, floor_db=args.floor_db,
                             merge_gap=args.merge_gap, min_dur=args.min_dur)

    rows = []
    prev_end = 0.0
    for k, (a, b) in enumerate(islands):
        t0, t1 = a / sr, b / sr
        dur = t1 - t0
        rows.append({
            "i": k, "t0": round(t0, 3), "t1": round(t1, 3), "window": round(dur, 3),
            "gap_before": round(t0 - prev_end, 3),
            "chars_min": int(round(dur * CHARS_PER_S * TEMPO_FLOOR)),
            "chars_typical": int(round(dur * CHARS_PER_S * TEMPO_TYPICAL)),
            "chars_max": int(round(dur * CHARS_PER_S * TEMPO_CEILING)),
        })
        prev_end = t1

    speech = sum(r["window"] for r in rows)
    real_pauses = [round(r["gap_before"], 2) for r in rows if r["gap_before"] >= SILENCE_BREAK]
    report = {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": json.loads((ROOT / "library/shorts-test/meta.json").read_text())["sha256"],
        "duration_s": round(total, 3), "sample_rate": sr,
        "detector": "youtube_auto_dub.island_sync.speech_islands",
        "detector_settings": {"floor_db": args.floor_db, "merge_gap": args.merge_gap,
                              "min_dur": args.min_dur},
        "islands": len(rows), "speech_s": round(speech, 3),
        "silence_s": round(total - speech, 3),
        "coverage_ratio": round(speech / total, 4),
        "real_pauses_s": real_pauses,
        "voice": {"id": "voice-00", "chars_per_s": CHARS_PER_S,
                  "tempo_floor": TEMPO_FLOOR, "tempo_typical": TEMPO_TYPICAL,
                  "tempo_ceiling": TEMPO_CEILING},
        "windows": rows,
        "asr_used": False,
        "words_from": "user-supplied transcript (pending)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"المدة {total:.2f} ث · كلام {speech:.2f} ث ({speech/total:.1%}) · صمت {total-speech:.2f} ث")
    print(f"جزر الكلام: {len(rows)} · وقفات حقيقية (>={SILENCE_BREAK} ث): {real_pauses or 'لا شيء'}")
    print(f"\n{'#':>3} {'من':>7} {'إلى':>7} {'النافذة':>8} {'فجوة قبل':>9} "
          f"{'أدنى حروف':>10} {'مريح':>6} {'أقصى':>6}")
    for r in rows:
        print(f"{r['i']:>3} {r['t0']:>7.2f} {r['t1']:>7.2f} {r['window']:>8.2f} "
              f"{r['gap_before']:>9.2f} {r['chars_min']:>10} {r['chars_typical']:>6} {r['chars_max']:>6}")
    print(f"\nالمجموع: أدنى {sum(r['chars_min'] for r in rows)} حرف · "
          f"مريح {sum(r['chars_typical'] for r in rows)} · أقصى {sum(r['chars_max'] for r in rows)}")
    print(f"كُتب: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
