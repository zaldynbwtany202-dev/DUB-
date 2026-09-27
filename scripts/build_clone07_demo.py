#!/usr/bin/env python
"""The clone the user asked for: my voice, shaped to the narrator's.

Three takes of the same words, in one file:

  1. the narrator himself, from the film        -- the target
  2. the generated voice (07) as it comes out   -- what the engine produced
  3. the same generated take after the clone    -- pitch matched per take and the
     cepstral envelope moved to the narrator's, 86% of the measured timbre gap

Nothing is taken from his recording except the description of his voice: the
long-term envelope and the pitch he speaks at. The words are the engine's.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imageio_ffmpeg  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
SEG = 280.0
TAKE = "g004"                      # a group whose text is a clean run of speech
SECONDS = 15.0
OUT = Path("previews/clone-voice07-demo.mp3")
WORK = Path("work/into-the-wild")


def head(path: Path, seconds: float) -> np.ndarray:
    x = sf.read(str(path), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x[: int(seconds * SR)].copy()
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    return x * min(4.0, 0.06 / rms)


def gap(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype="float32")


def main() -> None:
    groups = json.loads((WORK / "groups.json").read_text(encoding="utf-8"))["groups"]
    g = next(g for g in groups if f"g{g['i']:03d}" == TAKE)
    words = json.loads((WORK / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    spoken = [w for w in words if g["t0"] <= w["t0"] <= g["t1"] + 0.2]
    end_i = next(i for i, w in enumerate(spoken) if w["t1"] - g["t0"] >= SECONDS)
    a, b = g["t0"] - 0.10, spoken[end_i]["t1"] + 0.13

    k = int(a // SEG)
    stem = sf.read(str(WORK / "stems" / f"vocal-{k:02d}.mp3"), dtype="float32",
                   always_2d=False)[0]
    if stem.ndim > 1:
        stem = stem.mean(axis=1)
    real = stem[int((a - k * SEG) * SR): int((b - k * SEG) * SR)].copy()
    rms = float(np.sqrt(np.mean(real ** 2)) + 1e-9)
    real = real * (0.06 / rms)

    raw = head(WORK / "takes-voice07" / f"{TAKE}.mp3", SECONDS)
    cloned = head(WORK / "takes-clone07" / f"{TAKE}.wav", SECONDS)

    print(f"  الجملة: {' '.join(w['w'] for w in spoken[:end_i + 1])}")
    print(f"  ١. الراوي الحقيقي      {real.size / SR:5.2f} ثانية")
    print(f"  ٢. صوت 07 خام          {raw.size / SR:5.2f} ثانية")
    print(f"  ٣. صوت 07 مستنسخ       {cloned.size / SR:5.2f} ثانية")

    out = np.concatenate([real, gap(0.9), raw, gap(0.9), cloned])
    tmp = Path("/tmp/clone07.wav")
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية")
    print("    الترتيب: الراوي الحقيقي · صوت 07 خام · صوت 07 مستنسخ")


if __name__ == "__main__":
    main()
