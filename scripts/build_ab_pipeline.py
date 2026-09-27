#!/usr/bin/env python
"""The same words, four ways -- so the ear can name the step that breaks them.

Everything else in this project has been measured to death; this is the one
question only the ear can answer. One take of the chosen voice (13), the words
the film needs, then:

    00  the take exactly as the engine produced it      (no processing at all)
    01  stretched to fit the 20 s window with atempo    (no pitch change)
    02  stretched with rubberband                       (formant preserved)
    03  cleaned then stretched, as the film shipped     (the assembler's own path)

All four are loudness-matched, so a level difference cannot be mistaken for a
quality difference, and none of them is EQ'd or compressed.
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
TAKE = Path("work/into-the-wild/takes-voice13/g000.mp3")
OUT = Path("previews/ab-pipeline.mp3")
SECONDS = 12.0
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


def match(x: np.ndarray, target: float = 0.06) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    return x * (target / rms)


def main() -> None:
    tmp = Path("/tmp/abpipe")
    tmp.mkdir(exist_ok=True)
    raw = match(read(TAKE)[: int(SECONDS * SR)])
    sf.write(str(tmp / "raw.wav"), raw, SR, subtype="PCM_16")

    atempo = match(via(raw, STRETCHERS["atempo"].format(t=TEMPO), tmp))
    rb = match(via(raw, STRETCHERS["rubberband"].format(t=TEMPO), tmp))

    # The assembler's own cleanup, then the same stretch -- what shipped.
    tight_wav = tmp / "tight.wav"
    try:
        tighten(tmp / "raw.wav", tight_wav, tmp, pause_keep=0.25)
        tightened = read(tight_wav)
    except SystemExit:
        tightened = raw
    old_path = match(via(tightened, STRETCHERS["atempo"].format(t=TEMPO), tmp))

    items = [("00 كما خرج من المحرك — بلا أي معالجة", raw),
             ("01 مضغوط للوقت 1.4× (atempo)", atempo),
             ("02 مضغوط للوقت 1.4× (rubberband)", rb),
             ("03 كما كان الفيلم يخرجه (قصّ صمت + ضغط)", old_path)]

    gap = np.zeros(int(0.75 * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])
    sf.write(str(tmp / "all.wav"), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp / "all.wav"), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(OUT)], check=True)

    ref, _ = long_term_envelope(raw, SR)
    print("  نفس الكلمات، أربع طرق (كلها بمستوى واحد):")
    for label, x in items:
        env, _ = long_term_envelope(x, SR)
        d = float(np.mean(np.abs(env[1:] - ref[1:])))
        print(f"  {label:<44} {len(x) / SR:5.2f} ث · تغيّر عن الأصل {d:5.3f} dB/بن")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية")
    print("  الترتيب: كما خرج · atempo · rubberband · طريقة الفيلم السابقة")


if __name__ == "__main__":
    main()
