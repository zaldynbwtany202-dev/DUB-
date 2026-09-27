#!/usr/bin/env python
"""Before and after the steadiest-take choice: the same six groups, both versions.

Nothing is processed -- each sample is a raw take, level-matched to the others, so
the only thing that differs between the two halves is which take was chosen. The
first half is what the engine gave first (the takes that were in place), the second
half is the choice that stepped least. Counting tones announce the halves: one tone
= قبل، two = بعد.

    python3 scripts/build_pitch_ab.py --out previews/voice-steadiest-ab.mp3
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import imageio_ffmpeg  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
TAKES = Path("work/into-the-wild/takes-voice13")
CAND = TAKES / "candidates"
GROUPS = [6, 7, 10]                 # the groups whose chosen take actually changed
SR_OUT = 24000
SECONDS = 7.0


def beeps(n: int) -> np.ndarray:
    k = np.arange(int(0.16 * SR_OUT)) / SR_OUT
    beep = 0.32 * np.sin(2 * np.pi * 660.0 * k) * np.hanning(int(0.16 * SR_OUT))
    out = []
    for _ in range(n):
        out += [beep, np.zeros(int(0.14 * SR_OUT), dtype="float32")]
    return np.concatenate(out).astype("float32")


def cut(path: Path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR_OUT:
        t = np.arange(len(x)) / sr
        x = np.interp(np.arange(0, t[-1], 1 / SR_OUT), t, x).astype("float32")
    x = x[: int(SECONDS * SR_OUT)]
    f = int(0.01 * SR_OUT)
    x = x.copy()
    x[:f] *= np.linspace(0, 1, f)
    x[-f:] *= np.linspace(1, 0, f)
    rms = float(np.sqrt((x ** 2).mean())) + 1e-9
    return x * min(6.0, 0.07 / rms)          # gain only


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="previews/voice-steadiest-ab.mp3")
    a = ap.parse_args()
    gap = np.zeros(int(0.55 * SR_OUT), dtype="float32")
    parts = [beeps(1)]
    for g in GROUPS:
        p = CAND / f"g{g:03d}.original.mp3"
        if not p.exists():                     # kept take: same file in both halves
            p = TAKES / f"g{g:03d}.mp3"
        parts += [cut(p), gap]
    parts += [beeps(2)]
    for g in GROUPS:
        parts += [cut(TAKES / f"g{g:03d}.mp3"), gap]
    y = np.concatenate(parts)
    peak = float(np.abs(y).max()) + 1e-9
    y = y * min(1.0, 0.72 / peak)
    tmp = Path("/tmp/pitch-ab.wav")
    sf.write(str(tmp), y, SR_OUT, subtype="PCM_16")
    subprocess.run([FF, "-v", "error", "-y", "-i", str(tmp), "-c:a", "libmp3lame",
                    "-b:a", "160k", a.out], check=True)
    print(f"{a.out}  {len(y) / SR_OUT:.1f} s  (المجموعات: {GROUPS})")


if __name__ == "__main__":
    main()
