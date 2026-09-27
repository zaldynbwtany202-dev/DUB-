#!/usr/bin/env python
"""A/B: the same sentence built word by word, then built from a continuous run.

The word-by-word version is what sounded bad -- every word is cut from a different
place in the film, so the intonation breaks at every join and the level steps.
When the sentence is one he actually said in one breath, the whole run can be
taken, and then it is not an assembly at all: it is his recording, trimmed.
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
import unit_voice as uv  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = Path("work/into-the-wild/auditions/clone-ab-spans.mp3")
LINE = "احنا عايزين نهرب من التوقعات"
LINE2 = "الدنيا بتمطر وانا حاسس بالوحده انا خايف"


def main() -> None:
    idx = uv.load_index("into-the-wild")
    meta = json.loads(Path("work/into-the-wild/full-timed-vocal.json")
                      .read_text(encoding="utf-8"))["words"]
    seq = [uv.norm(w["w"]) for w in meta]
    pos = uv.positions(seq)
    cache: dict[int, np.ndarray] = {}
    gap = uv.silence if hasattr(uv, "silence") else (lambda s: np.zeros(int(s * uv.SR), "float32"))

    old, _, used_old, _ = uv.synth(LINE, idx, cache, spans=False)
    new, _, used_new, runs = uv.synth(LINE, idx, cache, words_meta=meta, seq=seq, pos=pos)
    new2, _, used2, runs2 = uv.synth(LINE2, idx, cache, words_meta=meta, seq=seq, pos=pos)

    print(f"  أ) كلمة كلمة: {used_old} كلمة · {used_old - 1} وصلة لصق")
    print(f"  ب) مقطع متصل: {runs} مقطعًا · {max(0, used_new - 1)} وصلة"
          f"  ({old.size / uv.SR:.2f} ث → {new.size / uv.SR:.2f} ث)")
    print(f"  ج) جملة ثانية بمقطع متصل: {runs2} مقطعًا · {max(0, used2 - 1)} وصلة")

    out = np.concatenate([old, gap(0.8), new, gap(0.8), new2])
    tmp = Path("/tmp/clone-ab.wav")
    sf.write(str(tmp), out, uv.SR, subtype="PCM_16")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)
    print(f"\n  ✓ {OUT} — {out.size / uv.SR:.2f} ثانية"
          f"\n    الترتيب: كلمة كلمة · ثم مقطع متصل · ثم جملة ثانية بمقطع متصل")


if __name__ == "__main__":
    main()
