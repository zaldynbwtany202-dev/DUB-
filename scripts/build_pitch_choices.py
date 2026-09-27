#!/usr/bin/env python
"""Three ways to place the chosen voice against the narrator's real pitch.

The pitch everyone was working to -- 168.3 Hz -- was wrong by an octave. Measured
two ways (autocorrelation with an octave guard, and cepstral peak), the narrator's
median pitch is ~99 Hz: a deep voice. The old estimator locked onto the second
harmonic, so every "correction" pushed the synthetic voice up to ~165-180 Hz and
made it sound like a cartoon. That is the squirrel.

These three are the honest options, same sentence, same voice (13), only the pitch
treatment differs:

    00  the narrator himself, from the film      -- target, ~99 Hz
    01  voice 13 untouched                       -- ~136 Hz, no processing at all
    02  voice 13 down 2.5 semitones              -- ~117 Hz, half way, still natural
    03  voice 13 down 5.5 semitones              -- ~99 Hz, level with the narrator

Formants are preserved throughout: shifting formants with pitch is the other half
of the cartoon sound.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import imageio_ffmpeg  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
SEG = 280.0
WORK = Path("work/into-the-wild")
VOICE = WORK / "auditions" / "ident-07-voice13.mp3"
OUT = Path("previews/pitch-choices.mp3")
WANT = ["وخلينا", "نرجع", "للحاضر", "والمحطه", "الاخيره", "لاليكس", "قبل", "ما", "يوصل", "الاسراسكا"]


def speak(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    return x * min(4.0, 0.062 / rms)


def read(path: Path) -> np.ndarray:
    x = sf.read(str(path), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return speak(x)


def narrator() -> np.ndarray:
    groups = json.loads((WORK / "groups.json").read_text(encoding="utf-8"))["groups"]
    words = json.loads((WORK / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    g = groups[59]
    span = [w for w in words if g["t0"] - 1 <= w["t0"] <= g["t1"] + 1]
    start = next(w["t0"] for w in span if w["w"] == "وخلينا")
    seq = span[[i for i, w in enumerate(span) if w["t0"] >= start][0]:][: len(WANT)]
    a, b = start - 0.15, seq[-1]["t1"] + 0.25
    k = int(a // SEG)
    x = sf.read(str(WORK / "stems" / f"vocal-{k:02d}.mp3"), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return speak(x[int((a - k * SEG) * SR): int((b - k * SEG) * SR)].copy())


def shifted(x: np.ndarray, semitones: float) -> np.ndarray:
    """Pitch down, formants untouched, through a file because rubberband is a filter."""
    ratio = 2 ** (-semitones / 12.0)
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "in.wav", Path(d) / "out.wav"
        sf.write(str(a), x, SR, subtype="PCM_16")
        subprocess.run([FF, "-y", "-v", "error", "-i", str(a), "-af",
                        f"rubberband=pitch={ratio:.6f}:formant=preserved:pitchq=quality",
                        "-ar", str(SR), "-ac", "1", str(b)], check=True)
        y = sf.read(str(b), dtype="float32", always_2d=False)[0]
    if y.ndim > 1:
        y = y.mean(axis=1)
    return speak(y)


def main() -> None:
    from voice_convert import median_f0
    raw = read(VOICE)
    n1 = narrator()
    variants = [("01 صوت 13 كما هو", raw)] + [
        (f"{n:02d} صوت 13 نازل {d:.0f} نصف نغمة", shifted(raw, d))
        for n, d in ((2, 3.0), (3, 6.0), (4, 9.0), (5, 12.0))]
    # Labels carry the *measured* pitch of each piece, not the requested one: the
    # shifter under-delivers, and a label that says 99 Hz for a 120 Hz recording is
    # how the last round went wrong.
    items = [(f"00 الراوي الحقيقي (المقاس {median_f0(n1, SR):.0f} هرتز)", n1)]
    items += [(f"{label} (المقاس {median_f0(x, SR):.0f} هرتز)", x) for label, x in variants]
    gap = np.zeros(int(0.8 * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])

    tmp = Path("/tmp/pitch-choices.wav")
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)

    lines = ["القياس القديم كان مضاعفًا: الراوي الحقيقي ~92 هرتز لا 168 — ولهذا ظهر صوت السنجاب.",
             "القرار: أي نسخة أقرب؟ كلها نفس الجملة ونفس الصوت 13، والفرق نبرة فقط.", ""]
    for label, _ in items:
        lines.append("  " + label)
    (WORK / "auditions" / "pitch-choices.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(f"  {l}" for l, _ in items))
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية")


if __name__ == "__main__":
    main()
