#!/usr/bin/env python
"""Build the clone demonstration: the narrator's voice, saying things he never said.

Three parts, in this order, so the difference is audible:

  1. his own continuous line from the film  -- the reference, unedited
  2. new sentences assembled from his words  -- the clone
  3. the same new sentence twice, to show it is repeatable

The voice is his throughout: every unit is cut from the film's own isolated vocal
stem at a measured timestamp. Nothing is synthesised and no timbre is transferred.
What is missing is intonation -- there is no model of how he would *say* a new
sentence, only how he said its words -- and the labels say so.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imageio_ffmpeg  # noqa: E402
import unit_voice  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = Path("previews/clone-original-voice.mp3")
LINES = [
    "انت عايز تعيش لوحدك",
    "احنا عايزين نهرب من التوقعات",
    "السعاده مش في الفلوس",
    "الطبيعه مش مكان الانسان",
    "الانسان محتاج الناس عشان يعيش",
]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * unit_voice.SR), dtype="float32")


def main() -> None:
    idx = unit_voice.load_index("into-the-wild")
    words = json.loads(Path("work/into-the-wild/full-timed-vocal.json")
                       .read_text(encoding="utf-8"))["words"]
    cache: dict[int, np.ndarray] = {}

    # 1. the reference: his first sentence in the film, taken whole
    end = next(i for i, w in enumerate(words) if unit_voice.norm(w["w"]) in ("نهرب", "بجد"))
    ref_a, ref_b = words[0]["t0"] - 0.12, words[end]["t1"] + 0.15
    k = int(ref_a // unit_voice.SEG)
    stem = sf.read(f"work/into-the-wild/stems/vocal-{k:02d}.mp3", dtype="float32",
                   always_2d=False)[0]
    if stem.ndim > 1:
        stem = stem.mean(axis=1)
    ref = stem[int((ref_a - k * unit_voice.SEG) * unit_voice.SR):
               int((ref_b - k * unit_voice.SEG) * unit_voice.SR)].copy()
    rms = float(np.sqrt(np.mean(ref ** 2)) + 1e-9)
    ref = ref * (0.06 / rms)
    print(f"  ١. جملته الحقيقية: {' '.join(w['w'] for w in words[:end + 1])}"
          f"  ({len(ref) / unit_voice.SR:.2f} ث)")

    parts = [ref, silence(0.9)]
    for n, line in enumerate(LINES, start=2):
        built, missing, used = unit_voice.synth(line, idx, cache)
        if missing:
            raise SystemExit(f"  ✗ «{line}» ناقص: {missing}")
        print(f"  {n}. مستنسخ: «{line}»  ({built.size / unit_voice.SR:.2f} ث · {used} كلمة)")
        parts += [built, silence(0.9)]

    out = np.concatenate(parts)
    tmp = Path(tempfile.mkdtemp()) / "demo.wav"
    sf.write(str(tmp), out, unit_voice.SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)
    print(f"\n  ✓ {OUT} — {out.size / unit_voice.SR:.2f} ثانية")


if __name__ == "__main__":
    main()
