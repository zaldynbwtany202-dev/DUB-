#!/usr/bin/env python
"""Isolate what made the voice sound broken: the mastering chain, not the voice.

Three pieces, same voice (13), same conversion, differing only in what was done
after it:

    01  the sample chosen by ear, exactly as it was heard
    02  a converted take through the old voice chain -- +2.5 dB at 3.2 kHz, de-esser,
        3:1 compression -- which is what turned it harsh and noisy
    03  the same take with nothing but loudness normalisation

The chain was written for the narrator's own recording, where a presence lift and
de-essing help. On a synthetic voice it exaggerates the upper band and pumps.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import imageio_ffmpeg  # noqa: E402
from master_dub import VOICE_CHAIN  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
WORK = Path("work/into-the-wild")
LIKED = WORK / "auditions" / "proposal" / "03-voice13-shap.shaped.wav"
TAKE = WORK / "takes-clone13p" / "g000.wav"
OUT = Path("previews/ab-mastering.mp3")


def norm_to(path: Path, dst: Path, extra: str = "") -> np.ndarray:
    """Loudness-normalise, with or without the old chain."""
    subprocess.run([FF, "-y", "-v", "error", "-i", str(path), "-af",
                    (extra + "," if extra else "") +
                    "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", str(SR), "-ac", "1", str(dst)], check=True)
    x = sf.read(str(dst), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x


def main() -> None:
    tmp = Path("/tmp/ab")
    tmp.mkdir(exist_ok=True)
    # The same sample in all three, so the only variable is what came after it.
    a = norm_to(LIKED, tmp / "a.wav")
    b = norm_to(LIKED, tmp / "b.wav", VOICE_CHAIN)
    c = norm_to(LIKED, tmp / "c.wav")

    dur = min(a.size, b.size, c.size, int(9.0 * SR))
    a, b, c = a[:dur], b[:dur], c[:dur]
    gap = np.zeros(int(0.8 * SR), dtype="float32")
    out = np.concatenate([a, gap, b, gap, c])
    sf.write(str(tmp / "ab.wav"), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp / "ab.wav"), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)

    print("  01 عيّنتك التي اخترتها — كما سمعتها (بلا سلسلة)")
    print("  02 عيّنتك نفسها بعد سلسلة الماستر القديمة — هذا هو الخربان")
    print("  03 عيّنتك نفسها بلا سلسلة — ضبط مستوى فقط")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية")


if __name__ == "__main__":
    main()
