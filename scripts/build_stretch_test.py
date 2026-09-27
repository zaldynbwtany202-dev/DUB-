#!/usr/bin/env python
"""Why the film's voice does not sound like the sample that was chosen.

The sample the user picked is on disk (auditions/proposal/03-voice13-shap.shaped.wav)
and it measures as voice 13 almost unchanged -- 163.9 Hz against 165.8 Hz raw. So
what he approved is the engine's own voice, with only a hair of shaping on it.

The film cannot use it as-is: the narrator speaks in 20-second windows and the
synthetic take is ~1.3x longer, so every take is time-compressed before it is
placed. That stretch is the one processing step the approved sample never went
through, and it is the prime suspect for "خربان ومشوش".

This builds the comparison: the approved sample untouched, then the same sample
through three kinds of 1.3x compression.
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
from assemble_dub import STRETCHERS  # noqa: E402
from voice_convert import median_f0  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
WORK = Path("work/into-the-wild")
LIKED = WORK / "auditions" / "proposal" / "03-voice13-shap.shaped.wav"
TEMPO = 1.306            # what group 0 actually needs, measured by the assembler
OUT = Path("previews/stretch-test.mp3")
APPROVED = Path("previews/approved-voice.mp3")


def read(path: Path) -> np.ndarray:
    x = sf.read(str(path), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x


def process(src: np.ndarray, chain: str, tmp: Path) -> np.ndarray:
    a, b = tmp / "in.wav", tmp / "out.wav"
    sf.write(str(a), src, SR, subtype="PCM_16")
    subprocess.run([FF, "-y", "-v", "error", "-i", str(a), "-af", chain.format(t=TEMPO),
                    "-ar", str(SR), "-ac", "1", str(b)], check=True)
    return read(b)


def main() -> None:
    tmp = Path("/tmp/stretch")
    tmp.mkdir(exist_ok=True)
    base = read(LIKED)
    print(f"  ملفك المعتمد: {base.size / SR:.2f} ثانية · {median_f0(base, SR):.1f} هرتز")

    full = process(base, STRETCHERS["rubberband"], tmp)          # what the film does now
    plain = process(base, "rubberband=tempo={t}:formant=preserved:pitchq=quality", tmp)
    atempo = process(base, STRETCHERS["atempo"], tmp)
    gentle = process(base, "rubberband=tempo={t}:formant=preserved", tmp)

    items = [("00 الصوت الذي اخترته — بلا أي تسريع (كما سمعته)", base),
             ("01 تسريع 1.3× بسلسلة الفيلم الحالية (crisp+soft+long)", full),
             ("02 تسريع 1.3× بروبرباند مبسّط (جودة عالية فقط)", plain),
             ("03 تسريع 1.3× بـatempo (بلا حفظ فورمانت كامل)", atempo),
             ("04 تسريع 1.3× بروبرباند مبسّط جدًا", gentle)]
    gap = np.zeros(int(0.7 * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])
    sf.write(str(tmp / "all.wav"), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp / "all.wav"), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(OUT)], check=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(LIKED), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(APPROVED)], check=True)

    for label, x in items:
        print(f"  {label:<52} {len(x) / SR:5.2f} ث · {median_f0(x, SR):6.1f} هرتز")
    lines = ["الصوت الذي اخترته، ثم أنواع التسريع التي يحتاجها الفيلم (1.3×):", ""]
    lines += ["  " + label for label, _ in items]
    (WORK / "auditions" / "stretch-test.txt").write_text("\n".join(lines) + "\n",
                                                         encoding="utf-8")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية"
          f"\n  ✓ {APPROVED} — ملفك المعتمد وحده")


if __name__ == "__main__":
    main()
