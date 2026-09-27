#!/usr/bin/env python
"""The narrator's own voice, as a working bank: longest-phrase assembly.

The old unit voice pasted one word at a time (50 ms of context, 45 ms of silence
between words). The user's ear called that what it was -- a list of words. This
one matches the *longest run of consecutive words* the narrator actually spoke
and pastes whole runs, so a sentence that exists in the film comes back as
continuous speech, seam count and all reported.

  python3 scripts/human_voice_bank.py --coverage "نص" [--coverage "نص آخر" ...]
  python3 scripts/human_voice_bank.py --say "نص" --out previews/x.mp3 [--report r.json]

Nothing is generated and nothing is converted: every sample is cut from
work/into-the-wild/stems/vocal-*.mp3 at the timings in full-timed-vocal.json.
Gain is the only thing touched, so a run never jumps in level mid-sentence.
"""
from __future__ import annotations

import argparse
import json
import re
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
SEG = 280.0          # stem segment length, from stems/music.json
PAD = 0.03           # context kept around a run
FADE = 0.008         # so cuts do not click
GAP = 0.07           # silence between runs
MAXN = 12            # longest run length the matcher looks for
WORK = Path("work/into-the-wild")

_PUNCT = re.compile(r"[^\w\u0621-\u064A\u0660-\u0669 ]+", re.UNICODE)


def norm(w: str) -> str:
    w = re.sub(r"[\u064B-\u0652\u0640]", "", w)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"),
                 ("ئ", "ي"), ("ؤ", "و")):
        w = w.replace(a, b)
    return w.strip()


def tokens(text: str) -> list[str]:
    text = _PUNCT.sub(" ", text)
    return [t for t in (norm(t) for t in text.split()) if t]


def load_words() -> list[dict]:
    return json.loads((WORK / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]


def build_index(words: list[dict]) -> dict[tuple, tuple]:
    """Every run of up to MAXN words that exists in the film, first instance wins."""
    keys = [norm(w["w"]) for w in words]
    index: dict[tuple, tuple] = {}
    for i in range(len(words)):
        for n in range(1, MAXN + 1):
            if i + n > len(words):
                break
            run = tuple(keys[i:i + n])
            if any(not k for k in run):
                break
            index.setdefault(run, (i, i + n - 1))
    return index


def match(text: str, index: dict[tuple, tuple]) -> list[tuple]:
    """Greedy longest-run match. Returns (run, i0, i1) plus None-runs for gaps."""
    t = tokens(text)
    out: list[tuple] = []
    i = 0
    while i < len(t):
        for n in range(min(MAXN, len(t) - i), 0, -1):
            run = tuple(t[i:i + n])
            if run in index:
                i0, i1 = index[run]
                out.append((run, i0, i1))
                i += n
                break
        else:
            out.append(((t[i],), None, None))
            i += 1
    return out


def coverage(text: str, index: dict[tuple, tuple]) -> dict:
    m = match(text, index)
    total = sum(len(r) for r, _, _ in m)
    hit = sum(len(r) for r, i0, _ in m if i0 is not None)
    runs = [len(r) for r, i0, _ in m if i0 is not None]
    longest = max(runs) if runs else 0
    segs = sum(1 for _, i0, _ in m if i0 is not None)
    return {"text": text, "words": total, "covered": hit, "percent": round(100.0 * hit / max(1, total), 1),
            "runs": segs, "longest": longest,
            "missing": [r[0] for r, i0, _ in m if i0 is None]}


def stem(k: int) -> np.ndarray:
    """One 280 s stem, at SR, cached as wav: the mp3s are 44100 Hz stereo and a
    timing read off full-timed-vocal.json must be taken at the file's own rate."""
    cache = Path(f"/tmp/hv/stem-{k:02d}.wav")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FF, "-v", "error", "-y", "-i",
                        str(WORK / "stems" / f"vocal-{k:02d}.mp3"),
                        "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(cache)],
                       check=True)
    x = sf.read(str(cache), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x


def cut(a: float, b: float) -> np.ndarray:
    """Original audio between two times, across stem boundaries if need be."""
    pieces = []
    k = int(a // SEG)
    while k * SEG < b:
        x = stem(k)
        lo = max(0.0, a - k * SEG) * SR
        hi = min(SEG, b - k * SEG) * SR
        pieces.append(x[int(lo):int(hi)].copy())
        k += 1
    seg = np.concatenate(pieces) if pieces else np.zeros(1, dtype="float32")
    f = int(FADE * SR)
    if seg.size > 2 * f:
        seg[:f] *= np.linspace(0, 1, f)
        seg[-f:] *= np.linspace(1, 0, f)
    return seg


def assemble(text: str, words: list[dict], index: dict[tuple, tuple]) -> tuple[np.ndarray, dict]:
    m = match(text, index)
    spans, report = [], coverage(text, index)
    for run, i0, i1 in m:
        if i0 is None:
            miss = int(round(0.22 * max(1, len(run[0]) / 3.0) * SR))
            spans.append(("gap", np.zeros(miss, dtype="float32")))
            continue
        a = max(0.0, words[i0]["t0"] - PAD)
        b = min(words[i1]["t1"] + PAD, SEG * 7)
        spans.append(("run", cut(a, b)))
    live = [s for kind, s in spans if kind == "run"]
    ref = np.median([float(np.sqrt((s ** 2).mean())) for s in live]) if live else 0.05
    out, used = [], []
    for kind, s in spans:
        if kind == "run":
            rms = float(np.sqrt((s ** 2).mean())) + 1e-9
            g = min(6.0, max(0.25, ref / rms))         # gain only, never a compressor
            s = s * g
            used.append(round(20 * np.log10(g), 2))
        out.append(s)
        if kind == "run":
            out.append(np.zeros(int(GAP * SR), dtype="float32"))
    y = np.concatenate(out) if out else np.zeros(1, dtype="float32")
    peak = float(np.abs(y).max()) + 1e-9
    if peak > 0.72:
        y = y * (0.72 / peak)
    report.update({"gains_db": used, "seconds": round(y.size / SR, 2)})
    return y, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", action="append", default=[])
    ap.add_argument("--say", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    words = load_words()
    index = build_index(words)
    print(f"البنك: {len(words)} كلمة · {len(set(norm(w['w']) for w in words))} كلمة مختلفة "
          f"· {len(index)} تسلسلًا حتى {MAXN} كلمات")
    if a.coverage:
        for t in a.coverage:
            c = coverage(t, index)
            print(f"  {c['percent']:5.1f}%  مقطوعات={c['runs']:<3} أطول تسلسل={c['longest']:<3} "
                  f"كلمات={c['words']:<3} ناقص={'(لا شيء)' if not c['missing'] else ' '.join(c['missing'])}")
            print(f"          «{t}»")
    if a.say:
        y, rep = assemble(a.say, words, index)
        if not a.out:
            raise SystemExit("--say needs --out")
        tmp = Path("/tmp/hvb.wav")
        sf.write(str(tmp), y, SR, subtype="PCM_16")
        subprocess.run([FF, "-v", "error", "-y", "-i", str(tmp), "-c:a", "libmp3lame",
                        "-b:a", "160k", a.out], check=True)
        print(f"\n{a.out}  {rep['seconds']} ث · تغطية {rep['percent']}% · "
              f"مقطوعات {rep['runs']} · أطول تسلسل {rep['longest']} كلمة")
        if a.report:
            Path(a.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
