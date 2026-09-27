#!/usr/bin/env python
"""Find the step that breaks the voice -- by running it on the user's own sample.

The sample the user approved is the one fixed point in this whole project: it is
what he heard and liked. Everything the film does to a voice can be applied to
that sample, one step at a time, and the step that ruins it will be audible.

    00  the approved sample, untouched            (the reference)
    01  + time compression 1.4x  (atempo)         the film must fit 20 s windows
    02  + time compression 1.4x  (rubberband)     the other stretcher
    03  + silence cut and rejoined (tighten)      the assembler's cleanup step
    04  + both, as the film actually ships it

No EQ, no compression, no pitch change anywhere: those were already ruled out.
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
from assemble_dub import STRETCHERS, tighten  # noqa: E402
from voice_convert import long_term_envelope, median_f0  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
LIKED = Path("work/into-the-wild/auditions/proposal/03-voice13-shap.shaped.wav")
OUT = Path("previews/diagnose-voice.mp3")
TEMPO = 1.4


def read(p: Path) -> np.ndarray:
    x = sf.read(str(p), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x


def via(x: np.ndarray, chain: str, tmp: Path) -> np.ndarray:
    a, b = tmp / "i.wav", tmp / "o.wav"
    sf.write(str(a), x, SR, subtype="PCM_16")
    subprocess.run([FF, "-y", "-v", "error", "-i", str(a), "-af", chain,
                    "-ar", str(SR), "-ac", "1", str(b)], check=True)
    return read(b)


def main() -> None:
    tmp = Path("/tmp/diagnose")
    tmp.mkdir(exist_ok=True)
    approved = read(LIKED)
    base_env, _ = long_term_envelope(approved, SR)

    stretched = via(approved, STRETCHERS["atempo"].format(t=TEMPO), tmp)
    rb = via(approved, STRETCHERS["rubberband"].format(t=TEMPO), tmp)

    tight = tmp / "tight.wav"
    try:
        tighten(tmp / "i.wav", tight, tmp, pause_keep=0.25)
        tightened = read(tight)
    except SystemExit:
        tightened = approved.copy()

    both = via(tightened if tightened.size else approved,
               STRETCHERS["atempo"].format(t=TEMPO), tmp)

    items = [("00 عيّنتك التي اخترتها — كما هي", approved),
             ("01 عيّنتها + ضغط زمني 1.4× (atempo)", stretched),
             ("02 عيّنتها + ضغط زمني 1.4× (rubberband)", rb),
             ("03 عيّنتها بعد قصّ الصمت وإعادة وصله", tightened),
             ("04 عيّنتها + الاثنان كما يخرج الفيلم", both)]

    gap = np.zeros(int(0.8 * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])
    sf.write(str(tmp / "all.wav"), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp / "all.wav"), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(OUT)], check=True)

    print("  القياس على عيّنتك نفسها:")
    for label, x in items:
        if x.size < 512:
            print(f"  {label:<44} (فارغ)")
            continue
        env, _ = long_term_envelope(x, SR)
        d = float(np.mean(np.abs(env[1:] - base_env[1:])))
        print(f"  {label:<44} {x.size / SR:5.2f} ث · "
              f"تغيّر الطابع {d:5.3f} dB/بن · نبرة {median_f0(x, SR):5.1f}")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية")


if __name__ == "__main__":
    main()
