#!/usr/bin/env python
"""Speak text in the narrator's own voice, assembled from his own recordings.

This is the clone that needs nothing from outside: the film contains 29 minutes of
the narrator saying 5,701 words, each with a measured start and end. Cut those
words out, keep the best instance of each, and a sentence built from them is his
real voice -- not a synthetic voice wearing his timbre, and not a model of him.

What it can and cannot do, stated plainly, because the difference matters:

  can   say any text whose words he actually spoke in the film  (--check reports
        the percentage before anything is built)
  cannot invent a word he never said, and it has no intonation model, so a
        sentence comes out flat: word, word, word. It sounds like him and reads
        like a list. The film's own lines are continuous speech; this is not.

    python scripts/unit_voice.py --check "نص للتجربة"
    python scripts/unit_voice.py --text "نص" --out previews/clone-original-voice.mp3

The unit index is cached in work/<slug>/units.json, and the cache is built from
the timings in work/<slug>/full-timed-vocal.json -- never from a guess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imageio_ffmpeg  # noqa: E402

SR = 22050
SEG = 280.0                      # stem segment length, from stems/music.json
PAD = 0.05                       # seconds of context kept around each word
FADE = 0.008                     # seconds, so cuts do not click
GAP = 0.045                      # silence between assembled words


def norm(w: str) -> str:
    """Fold spelling variants so a word can be found however it was written."""
    w = re.sub(r"[\u064B-\u0652\u0640]", "", w)      # diacritics, tatweel
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"),
                 ("ئ", "ي"), ("ؤ", "و")):
        w = w.replace(a, b)
    return w.strip()


def load_index(slug: str) -> dict[str, list[dict]]:
    work = Path("work") / slug
    cache = work / "units.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items()}
    words = json.loads((work / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    idx: dict[str, list[dict]] = defaultdict(list)
    for i, w in enumerate(words):
        key = norm(w["w"])
        if not key:
            continue
        # Context on either side is what keeps the word from sounding chopped, so
        # a unit claims a little of the silence around it, never a neighbour word.
        prev_end = words[i - 1]["t1"] if i else 0.0
        next_start = words[i + 1]["t0"] if i + 1 < len(words) else w["t1"]
        a = max(prev_end, w["t0"] - PAD)
        b = min(next_start, w["t1"] + PAD)
        idx[key].append({"t0": round(a, 3), "t1": round(b, 3),
                         "measured": bool(w.get("measured"))})
    out = {k: v for k, v in idx.items()}
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def choose(instances: list[dict]) -> dict:
    """The instance closest to the word's median length, preferring measured ones."""
    dur = sorted(i["t1"] - i["t0"] for i in instances)
    mid = np.median(dur)
    measured = [i for i in instances if i["measured"]] or instances
    return min(measured, key=lambda i: abs((i["t1"] - i["t0"]) - mid))


def cut(stem_cache: dict, t0: float, t1: float) -> np.ndarray:
    k = int(t0 // SEG)
    if k not in stem_cache:
        src = Path(f"work/into-the-wild/stems/vocal-{k:02d}.mp3")
        if not src.exists():
            raise SystemExit(f"  ✗ لا يوجد مقطع صوت: {src}")
        stem_cache[k] = sf.read(str(src), dtype="float32", always_2d=False)[0]
        if stem_cache[k].ndim > 1:            # a stereo stem would break the cuts
            stem_cache[k] = stem_cache[k].mean(axis=1)
    x = stem_cache[k]
    a = max(0, int((t0 - k * SEG) * SR))
    b = min(len(x), int((t1 - k * SEG) * SR))
    seg = x[a:b].copy()
    n = int(FADE * SR)
    if len(seg) > 2 * n:
        seg[:n] *= np.linspace(0, 1, n, dtype="float32")
        seg[-n:] *= np.linspace(1, 0, n, dtype="float32")
    # Level each word before joining: recording level drifts across the film, and
    # a sentence must not step up and down word by word.
    rms = float(np.sqrt(np.mean(seg ** 2)) + 1e-9)
    return seg * min(4.0, 0.06 / rms)


def synth(text: str, idx: dict[str, list[dict]], stem_cache: dict):
    pieces, missing = [], []
    for raw in text.split():
        key = norm(raw)
        found = idx.get(key)
        if not found:                      # try without a leading conjunction/article
            for drop in ("و", "ال", "ف", "ب"):
                if key.startswith(drop) and key[len(drop):] in idx:
                    found, key = idx[key[len(drop):]], key[len(drop):]
                    break
        if not found:
            missing.append(raw)
            continue
        u = choose(found)
        pieces.append(cut(stem_cache, u["t0"], u["t1"]))
    if not pieces:
        return np.zeros(0, dtype="float32"), missing, 0
    gap = np.zeros(int(GAP * SR), dtype="float32")
    out = pieces[0]
    for p in pieces[1:]:
        out = np.concatenate([out, gap, p])
    return out, missing, len(pieces)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--text", default=None)
    ap.add_argument("--check", default=None, help="report coverage only, build nothing")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    idx = load_index(a.slug)
    print(f"  مفردات صوته: {len(idx)} كلمة مختلفة · "
          f"{sum(len(v) for v in idx.values())} موضعًا")

    text = a.text or a.check
    if not text:
        raise SystemExit("  ✗ اكتب نصًا بـ--text أو --check")

    words = text.split()
    found = [w for w in words if norm(w) in idx]
    print(f"  التغطية: {len(found)}/{len(words)} كلمة ({100 * len(found) / len(words):.0f}%)")
    miss = [w for w in words if norm(w) not in idx]
    if miss:
        print(f"  لم يقلها في الفيلم: {' '.join(miss)}")

    if a.check:
        # Exit code 0 only when every word is in his vocabulary -- the caller can
        # gate a build on it.
        sys.exit(0 if len(found) == len(words) else 1)

    stem_cache: dict[int, np.ndarray] = {}
    out, missing, n = synth(text, idx, stem_cache)
    if out.size == 0:
        raise SystemExit("  ✗ لم تُبنَ أي كلمة")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / "clone.wav"
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-v", "error", "-i", str(tmp), "-ac", "1",
                    "-codec:a", "libmp3lame", "-b:a", "160k", str(a.out)], check=True)
    print(f"  ✓ {a.out} — {out.size / SR:.2f} ثانية · {n} كلمة من صوته"
          + (f" · ناقص: {' '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
