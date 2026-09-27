#!/usr/bin/env python
"""Build a shelf of voice samples, so the choice is made by ear.

Each candidate is the same sentence, shaped to the narrator the same way: pitch
matched to his, then the long-term cepstral envelope moved to his. They differ in
the voice underneath, which is the thing being chosen. The narrator's own line
opens the file, so the ear has the target immediately before the candidates.

The numbers in the order file are what to answer with.
"""
from __future__ import annotations

import json
import subprocess
import sys
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
AUD = WORK / "auditions"
OUT = Path("previews/clone-candidates.mp3")
GAP = 0.85
TARGET = WORK / "stems" / "vocal-00.mp3"
RAW = "work/into-the-wild/auditions/ident-03-voice07.mp3"


def speak(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    return x * min(4.0, 0.062 / rms)


def narrator_line() -> np.ndarray:
    groups = json.loads((WORK / "groups.json").read_text(encoding="utf-8"))["groups"]
    words = json.loads((WORK / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    g = groups[59]
    want = ["وخلينا", "نرجع", "للحاضر", "والمحطه", "الاخيره", "لاليكس", "قبل", "ما", "يوصل", "الاسراسكا"]
    span = [w for w in words if g["t0"] - 1 <= w["t0"] <= g["t1"] + 1]
    start = next(w["t0"] for w in span if w["w"] == "وخلينا")
    seq = span[[i for i, w in enumerate(span) if w["t0"] >= start][0]:][: len(want)]
    a, b = start - 0.15, seq[-1]["t1"] + 0.25
    k = int(a // SEG)
    x = sf.read(str(WORK / "stems" / f"vocal-{k:02d}.mp3"), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return speak(x[int((a - k * SEG) * SR): int((b - k * SEG) * SR)].copy())


def read(path: Path) -> np.ndarray:
    x = sf.read(str(path), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return speak(x)


def convert(src: Path, dst: Path) -> None:
    subprocess.run([sys.executable, str(HERE / "voice_convert.py"),
                    "--target", str(TARGET), "--source", str(src), "--out", str(dst),
                    "--strength", "1.0"], check=True, capture_output=True)


def main() -> None:
    items: list[tuple[str, np.ndarray]] = [("00 الراوي الحقيقي (الهدف)", narrator_line())]
    items.append(("01 صوت 07 بلا أي تشكيل", read(Path(RAW))))

    voices = [("05", AUD / "ident-01-voice05.mp3"),
              ("06", AUD / "ident-02-voice06.mp3"),
              ("07", AUD / "ident-03-voice07.mp3"),
              ("08", AUD / "ident-04-voice08.mp3"),
              ("10", AUD / "ident-06-voice10.mp3"),
              ("13", AUD / "ident-07-voice13.mp3"),
              ("14", AUD / "ident-08-voice14.mp3")]
    cache = AUD / "candidates"
    cache.mkdir(exist_ok=True)
    for n, (name, src) in enumerate(voices, start=2):
        dst = cache / f"{n:02d}-voice{name}.wav"
        if not dst.exists():
            convert(src, dst)
        items.append((f"{n:02d} صوت {name} مشكَّل على الراوي", read(dst)))
        print(f"  {n:02d}  صوت {name}  ({sf.info(str(dst)).duration:.2f} ث)")

    gap = np.zeros(int(GAP * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])
    tmp = Path("/tmp/candidates.wav")
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)

    (AUD / "candidates.txt").write_text(
        "لوحة العينات — نفس الجملة، والأرقام هي ما يُجيب به:\n"
        + "".join(f"  {label}\n" for label, _ in items)
        + f"\nالجملة: وخلينا نرجع للحاضر والمحطه الاخيره لاليكس قبل ما يوصل الاسراسكا\n"
        + f"الملف: {OUT}  ({out.size / SR:.1f} ثانية)\n"
        + "كل مرشَّح مشكَّل على الراوي: النبرة مضبوطة على نبرته، والطابع الصوتي منقول إليه.\n",
        encoding="utf-8")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية · {len(items)} عيّنة")


if __name__ == "__main__":
    main()
