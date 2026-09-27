#!/usr/bin/env python
"""The voices that were never auditioned, next to the one that was approved.

Fifteen voice ids exist; eight had been heard. These are the other seven -- 00, 01,
02, 03, 04, 11, 12 -- saying the same sentence as voice 13, which the user approved
and which therefore opens the set as the bar to beat. The narrator's own line
comes first of all, since he is the reference the whole project is aimed at.

Nothing is converted: no timbre transfer, no pitch move, no EQ. Loudness is matched
and nothing else, so this compares voices rather than processing.
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
from voice_convert import median_f0  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
SEG = 280.0
WORK = Path("work/into-the-wild")
AUD = WORK / "auditions"
OUT = Path("previews/new-voices.mp3")
NEW = [("00", AUD / "probe-00.mp3"), ("01", AUD / "probe-01.mp3"),
       ("02", AUD / "probe-02.mp3"), ("03", AUD / "probe-03.mp3"),
       ("04", AUD / "probe-04.mp3"), ("11", AUD / "probe-11.mp3"),
       ("12", AUD / "probe-12.mp3")]
WANT = ["وخلينا", "نرجع", "للحاضر", "والمحطه", "الاخيره", "لاليكس", "قبل", "ما", "يوصل", "الاسراسكا"]


def speak(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    return x * min(4.0, 0.062 / rms)


def read(p: Path) -> np.ndarray:
    x = sf.read(str(p), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return speak(x)


def narrator() -> np.ndarray:
    import json
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


def main() -> None:
    items = [("00 الراوي الحقيقي — المرجع الأعلى", narrator()),
             ("01 الصوت المعتمد الذي أجزه (13)", read(AUD / "ident-07-voice13.mp3"))]
    for n, (name, path) in enumerate(NEW, start=2):
        x = read(path)
        items.append((f"{n:02d} صوت {name} — جديد لم تسمعه", x))
        print(f"  {n:02d} صوت {name:<3} {x.size / SR:5.2f} ث · {median_f0(x, SR):6.1f} هرتز")

    gap = np.zeros(int(0.85 * SR), dtype="float32")
    out = items[0][1]
    for _, x in items[1:]:
        out = np.concatenate([out, gap, x])
    tmp = Path("/tmp/newvoices.wav")
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(OUT)], check=True)

    lines = ["الأصوات التي لم تُسمع بعد، ومعها المعتمد والراوي (نفس الجملة للجميع):", ""]
    lines += ["  " + label for label, _ in items]
    lines += ["", "بلا أي تحويل ولا EQ ولا ضغط — مستوى واحد للجميع.", f"الملف: {OUT}"]
    (AUD / "new-voices.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  ✓ {OUT} — {out.size / SR:.1f} ثانية · {len(items)} عيّنة")


if __name__ == "__main__":
    main()
