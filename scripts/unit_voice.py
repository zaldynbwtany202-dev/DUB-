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


# Connectors that may stand as a one-letter piece at the start of a word.
LEAD = {"و", "ف", "ب", "ل", "ال", "وال", "فال", "بال"}


def decompose(word: str, idx: dict, max_parts: int = 3) -> list[str] | None:
    """Split a word he never said into words he did.

    "وتعيش" becomes "و" + "تعيش" when both are in his vocabulary, so the clone can
    say a word the film does not contain -- stitched from its own pieces rather
    than invented. Fewest pieces wins; up to `max_parts` are allowed.
    """
    n = len(word)
    best: dict[int, list[str]] = {0: []}
    for i in range(n):
        if i not in best:
            continue
        if len(best[i]) >= max_parts:
            continue
        for j in range(i + 1, min(n, i + 12) + 1):
            piece = word[i:j]
            if piece not in idx:
                continue
            if j - i < 2 and not (i == 0 and piece in LEAD):
                continue                      # a bare letter is not a word
            cand = best[i] + [piece]
            if j not in best or len(cand) < len(best[j]):
                best[j] = cand
    return best.get(n)


def sequence(slug: str) -> list[str]:
    """The film's words in order, normalised -- the substrate for run matching."""
    words = json.loads((Path("work") / slug / "full-timed-vocal.json")
                       .read_text(encoding="utf-8"))["words"]
    return [norm(w["w"]) for w in words]


def positions(seq: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for i, w in enumerate(seq):
        out[w].append(i)
    return out


def longest_run(target: list[str], i: int, seq: list[str],
                pos: dict[str, list[int]], cap: int = 14) -> tuple[int, int]:
    """Longest run of target[i:] that appears contiguously in the film."""
    if i >= len(target):
        return 0, -1
    best_len, best_at = 1, -1
    for at in pos.get(target[i], ())[:600]:
        n = 1
        while (n < cap and i + n < len(target) and at + n < len(seq)
               and seq[at + n] == target[i + n]):
            n += 1
        if n > best_len:
            best_len, best_at = n, at
            if n == cap:
                break
    return best_len, best_at


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


def synth(text: str, idx: dict[str, list[dict]], stem_cache: dict, allow_split=True,
          words_meta: list[dict] | None = None, seq: list[str] | None = None,
          pos: dict[str, list[int]] | None = None, spans: bool = True):
    """Assemble the text from the narrator's own recording.

    With `spans`, the longest run he said in one breath is taken whole; only the
    seams between runs are joins. Without it every word is its own join, which is
    what made the first version sound chopped.
    """
    pieces, missing, runs = [], [], 0
    target = [norm(w) for w in text.split()]
    if spans and words_meta and seq is not None and pos is not None:
        i = 0
        while i < len(target):
            n, at = longest_run(target, i, seq, pos)
            if n >= 2:
                first, last = words_meta[at], words_meta[at + n - 1]
                pieces.append(cut(stem_cache, first["t0"] - 0.05, last["t1"] + 0.08))
                runs += 1
                i += n
                continue
            w = target[i]
            if w in idx:
                u = choose(idx[w])
                pieces.append(cut(stem_cache, u["t0"], u["t1"]))
            else:
                missing.append(text.split()[i])
            i += 1
        return _join(pieces), missing, len(pieces), runs

    for raw in text.split():
        key = norm(raw)
        found = idx.get(key)
        if not found:                      # try without a leading conjunction/article
            for drop in ("و", "ال", "ف", "ب"):
                if key.startswith(drop) and key[len(drop):] in idx:
                    found, key = idx[key[len(drop):]], key[len(drop):]
                    break
        if not found:
            split = decompose(key, idx) if allow_split else None
            if not split:
                missing.append(raw)
                continue
            # Inside a word the join must be tighter than between words, or the
            # split is audible as two separate words.
            tiny = np.zeros(int(0.012 * SR), dtype="float32")
            word_part = None
            for piece in split:
                seg = cut(stem_cache, choose(idx[piece])["t0"], choose(idx[piece])["t1"])
                word_part = seg if word_part is None else np.concatenate([word_part, tiny, seg])
            pieces.append(word_part)
            continue
        u = choose(found)
        pieces.append(cut(stem_cache, u["t0"], u["t1"]))
    return _join(pieces), missing, len(pieces), 0


def _join(pieces: list[np.ndarray]) -> np.ndarray:
    if not pieces:
        return np.zeros(0, dtype="float32")
    # A run-to-run seam is a real break in the recording; keeping a short gap there
    # is what stops two runs reading as one slurred sentence.
    gap = np.zeros(int(GAP * SR), dtype="float32")
    out = pieces[0]
    for p in pieces[1:]:
        out = np.concatenate([out, gap, p])
    return out


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
    direct = [w for w in words if norm(w) in idx]
    split_ok = [w for w in words if norm(w) not in idx and decompose(norm(w), idx)]
    miss = [w for w in words if norm(w) not in idx and not decompose(norm(w), idx)]
    cover = len(direct) + len(split_ok)
    print(f"  التغطية: {cover}/{len(words)} كلمة ({100 * cover / len(words):.0f}%)"
          f" — {len(direct)} بكلمة كاملة و{len(split_ok)} مقسَّمة")
    if split_ok:
        print("  مقسَّمة من كلماته: " + " · ".join(f"{w} = {'+'.join(decompose(norm(w), idx))}"
                                                  for w in split_ok[:8]))
    if miss:
        print(f"  لا تُنطق أصلًا: {' '.join(miss)}")

    if a.check:
        # Exit code 0 only when every word can be said -- the caller can gate a
        # build on it.
        sys.exit(0 if cover == len(words) else 1)

    meta = json.loads((Path("work") / a.slug / "full-timed-vocal.json")
                      .read_text(encoding="utf-8"))["words"]
    seq = [norm(w["w"]) for w in meta]
    pos = positions(seq)

    stem_cache: dict[int, np.ndarray] = {}
    out, missing, n, runs = synth(text, idx, stem_cache, words_meta=meta, seq=seq, pos=pos)
    print(f"  طريقة التركيب: {runs} مقطعًا متصلًا · {n - runs} كلمة مفردة · "
          f"{max(0, n - 1)} وصلة لصق")
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
