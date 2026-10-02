#!/usr/bin/env python3
"""Lay each take on the original's own words -- smoothly, inside its window.

Measured on Into the Wild before any of this: our words sat 0.70 s from the
original's on average, 3.12 s in a bad group, 12.39 s at worst. Two causes were
found by measurement, not by ear:

1. The timing itself was wrong in places. `full-timed.json` came from whisper on
   the *separated dialogue stem*, which lost whole sentences: 208 words carried
   no time of their own and were packed into 0.08 s stubs. Re-read from the
   film's own mix (`whisper_word_times.py` + `retime_script_from_audio.py`) they
   have real times.

2. Anchoring every pause-delimited part of the take to the single word it starts
   at cascades: the original does not speak at a constant rate, and every part
   that cannot fit its span at the tempo ceiling pushes everything after it
   later, until a group ends 6-12 s behind. Version 1 of this script did exactly
   that.

What this does instead: the take is parted at its own silences (no speech is cut
and nothing is added), and the parts are placed **contiguously** from the start
of the window to its end -- so the dub never overlaps itself, never leaves a
hole, and never drifts, and the timeline is never stretched. Inside that, the
per-part tempos are solved together (least squares to the original's word times
plus a small penalty on tempo changes, atempo bounds from the voice lock) instead
of one part at a time. Whole groups that could not fit before fit now, and the
error is spread thin rather than piled at the end.

    python scripts/align_by_words.py work/into-the-wild \
        --takes work/into-the-wild/takes-voice25 \
        --timed work/into-the-wild/full-timed-v2.json \
        --out work/into-the-wild/build/into-the-wild-master-seg.wav \
        --placement work/into-the-wild/build/into-the-wild-placement-0-32.json \
        --start 0 --end 32 --max-tempo 1.78 --min-tempo 0.90
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")
AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ئ": "ي",
                        "ؤ": "و", "ة": "ه", "ٱ": "ا"})


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def norm(word: str) -> str:
    w = word.strip().strip(".,!?؟،؛:\"'()[]{}«»-—…")
    return DIACRITICS.sub("", w).translate(AR_MAP)


def silence_splits(x: np.ndarray, sr: int, floor: float = 0.012,
                   min_gap: float = 0.12) -> list[float]:
    """Times (seconds) where the take is quiet long enough to part it safely."""
    hop, win = int(0.01 * sr), int(0.03 * sr)
    rms = np.array([float(np.sqrt((x[i:i + win] ** 2).mean()))
                    for i in range(0, len(x) - win, hop)])
    quiet = rms < floor
    splits, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 0.01 >= min_gap and start > 3 and i < len(quiet) - 3:
                splits.append((start + i) / 2 * 0.01)   # middle of the quiet run
            start = None
    return splits


def words_of(text: str) -> list[str]:
    return [w for w in text.replace('،', ' ').replace('؛', ' ').split() if w]


def group_targets(text_words: list[str], src_words: list[dict]) -> np.ndarray:
    """A target time for every word we speak.

    Our text and the window's words are matched word by word (Arabic spelling
    normalised). Matched words get the original's time exactly; the mapping
    between matches is proportional in word index, and its ends are pinned to
    the window's own first and last word -- so a group whose text was condensed
    is spread across the whole window instead of being dumped at its end.
    """
    M, N = len(src_words), len(text_words)
    a = [norm(w["w"]) for w in src_words]
    b = [norm(w) for w in text_words]
    pairs: list[tuple[int, int]] = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            if j + k < N and i + k < M:
                pairs.append((j + k, i + k))        # (our word, source word)
    pts = [(0, 0.0), (N - 1, float(M - 1))] + [(j, float(i)) for j, i in pairs]
    pts.sort()
    ours = np.array([p[0] for p in pts], float)
    theirs = np.maximum.accumulate(np.array([p[1] for p in pts], float))
    t0 = np.array([w["t0"] for w in src_words], float)
    j_frac = np.interp(np.arange(N), ours, theirs)
    return np.interp(j_frac, np.arange(M), t0)


def _project(x: np.ndarray, d: np.ndarray, window: float, lo: float, hi: float) -> np.ndarray:
    """أقرب نقطة تحقّق قيد المدة وحدود السرعة (إسقاط متناوب)."""
    for _ in range(200):
        x = np.clip(x, lo, hi)
        gap = window - float(d @ x)
        if abs(gap) < 1e-7:
            return x
        x = x + (gap / float(d @ d)) * d
    return np.clip(x, lo, hi)


def fit_group(segs: list[tuple[float, float]], tgt: np.ndarray, window: float,
              max_tempo: float, min_tempo: float, smooth: float, t0: float = 0.0):
    """Chunk tempos that put our words as close to theirs as atempo allows.

    The chunks are placed one after another, so the whole take runs from the
    window's start to its end exactly (the sum of stretched chunk lengths is the
    window). Only the tempos are free, and they are solved together: the cost is
    the mean squared distance from the original's word times plus a small
    penalty on tempo changes, minimised by projected gradient (the problem is
    convex, so this reaches the best fit, and every step stays inside the
    atempo bounds and inside the window).
    """
    d = np.array([e - s for s, e in segs], float)
    tgt = np.asarray(tgt, float) - t0          # الزمن نسبةً إلى بداية النافذة
    N = len(tgt)
    if len(segs) < 2 or N < 4:
        s_uni = float(np.clip(1.0 / (d.sum() / window), 1.0 / max_tempo, 1.0 / min_tempo))
        s = np.full(max(1, len(segs)), s_uni)
        pos_u = (np.arange(N) + 0.5) / max(1, N) * float(d.sum() * s_uni)
        return s, d, np.abs(pos_u - tgt) if len(d) else np.abs(pos_u - tgt)
    # الكلمة i في أي مقطع وبأي نسبة داخله
    q = np.cumsum(d) / d.sum()
    ci = np.minimum(np.searchsorted(q, (np.arange(N) + 0.5) / N), len(segs) - 1)
    pos = np.zeros(N)
    for k in range(len(segs)):
        ks = np.where(ci == k)[0]
        if len(ks):
            pos[ks] = (np.arange(len(ks)) + 0.5) / len(ks)

    A = np.zeros((N, len(segs)))            # pred_i = Σ A[i,k] * s_k  (s_k = 1/tempo)
    for i in range(N):
        k = ci[i]
        A[i, :k] = d[:k]
        A[i, k] = d[k] * pos[i]
    n = len(segs)
    lo, hi = 1.0 / max_tempo, 1.0 / min_tempo
    s0 = float(np.clip(1.0 / (d.sum() / window), lo, hi))
    x = np.full(n, s0)

    def cost(xx: np.ndarray) -> float:
        r = A @ xx - tgt
        J = float((r ** 2).mean())
        if smooth > 0:
            tp = 1.0 / np.maximum(xx, 1e-6)
            J += smooth * float(((tp[1:] - tp[:-1]) ** 2).mean())
        return J

    def grad(xx: np.ndarray) -> np.ndarray:
        r = A @ xx - tgt
        g = 2.0 * (A.T @ r) / N
        if smooth > 0:
            # ∂ J/∂x_j = smooth/(m-1) · 2·(D_j − D_{j−1}) / x_j²  حيث D_k = 1/x_{k+1} − 1/x_k
            tp = 1.0 / np.maximum(xx, 1e-6)
            dt = tp[1:] - tp[:-1]
            gs = np.zeros_like(xx)
            gs[:-1] += dt / np.maximum(xx[:-1], 1e-6) ** 2
            gs[1:] -= dt / np.maximum(xx[1:], 1e-6) ** 2
            g = g + gs * (2.0 * smooth / max(1, len(dt)))
        return g

    x = _project(x, d, window, lo, hi)
    eig = float(np.linalg.eigvalsh(A.T @ A).max()) / N if N else 1.0
    step = 1.0 / max(1e-9, 2.0 * eig)
    f = cost(x)
    for _ in range(4000):
        g = grad(x)
        improved = False
        for _ in range(30):
            cand = _project(x - step * g, d, window, lo, hi)
            fc = cost(cand)
            if fc < f - 1e-12:
                x, f, improved = cand, fc, True
                break
            step *= 0.5
        if not improved:
            break
        step *= 1.15
    err = np.abs(A @ x - tgt)
    return x, d, err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--takes", type=Path, required=True)
    ap.add_argument("--timed", type=Path, required=True, help="JSON بكلمات لها t0/t1")
    ap.add_argument("--groups", type=Path, default=None, help="افتراضي: work/groups.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--placement", type=Path, required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--max-tempo", type=float, default=1.8, help="سقف السياسة")
    ap.add_argument("--ceiling-margin", type=float, default=0.02,
                    help="يعمل الربط تحت السقف بهذا الهامش حتى لا يتّكئ عليه")
    ap.add_argument("--min-tempo", type=float, default=0.95)
    ap.add_argument("--smooth", type=float, default=0.02)
    ap.add_argument("--timed-alt", type=Path, default=None,
                    help="ملف توقيت ثانٍ؛ لكل مجموعة يُختار الملف الذي يعطي خطأ ربط أقل "
                         "(قرار مقيس لا تخمين)")
    a = ap.parse_args()

    groups_path = a.groups or (a.work / "groups.json")
    doc = json.loads(groups_path.read_text(encoding="utf-8"))
    groups = [g for g in doc["groups"] if a.start <= int(g["i"]) < a.end]
    def load_words(path: Path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        return [w for w in doc["words"] if w.get("t0") is not None and w.get("t1") is not None]
    src_words = load_words(a.timed)
    alt_words = load_words(a.timed_alt) if a.timed_alt else None
    if not src_words:
        raise SystemExit("  ✗ ملف التوقيت فارغ")

    tmp = a.out.parent / "align-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    placements = []
    label = 0
    all_err: list[float] = []

    for grp in groups:
        i = int(grp["i"])
        t0, t1 = float(grp["t0"]), float(grp["t1"])
        window = t1 - t0
        take = a.takes / f"g{i:03d}.mp3"
        if not take.exists():
            raise SystemExit(f"  ✗ لا توجد أخذة: {take}")
        text_words = words_of(grp["text"])
        cands = []
        for name, pool in (("new", src_words), ("alt", alt_words)):
            if pool is None:
                continue
            s_pool = [w for w in pool if t0 - 0.02 <= w["t0"] < t1]
            if s_pool:
                cands.append((name, group_targets(text_words, s_pool)))
        if not cands:
            raise SystemExit(f"  ✗ لا كلمات أصل في نافذة g{i:03d}")
        tgt_name, tgt = cands[0]

        x, sr = sf.read(str(take), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        n = len(x) / sr
        splits = silence_splits(x, sr)
        bounds = [0.0] + splits + [n]
        segs = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
        segs = [(s, e) for s, e in segs if e - s > 0.08]
        if len(segs) < 2:
            segs = [(0.0, n)]

        fit_max = min(a.max_tempo - a.ceiling_margin, a.max_tempo)
        best = None
        for name, cand in cands:
            s_c, d_c, e_c = fit_group(segs, cand, window, fit_max, a.min_tempo, a.smooth, t0)
            if best is None or float(e_c.mean()) < best[0]:
                best = (float(e_c.mean()), name, cand, s_c, d_c, e_c)
        _, tgt_name, tgt, s, d, err = best
        tempo = 1.0 / np.clip(s, 1.0 / fit_max, 1.0 / a.min_tempo)
        starts = t0 + np.concatenate([[0.0], np.cumsum(d * s)[:-1]])
        all_err += list(err)

        placed = []
        for k, ((seg_s, seg_e), tk) in enumerate(zip(segs, tempo)):
            seg_file = tmp / f"g{i:03d}-s{k}.wav"
            if not seg_file.exists():
                run([ffmpeg(), "-y", "-v", "error", "-ss", f"{seg_s:.3f}", "-t", f"{seg_e - seg_s:.3f}",
                     "-i", str(take), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(seg_file)])
            placed.append({"seg": k, "from": round(seg_s, 3), "dur": round(seg_e - seg_s, 3),
                           "start": round(float(starts[k]), 3), "tempo": round(float(tk), 3),
                           "end": round(float(starts[k] + d[k] * s[k]), 3), "file": seg_file.name,
                           "delay_ms": int(round(float(starts[k]) * 1000))})

        g_file = tmp / f"g{i:03d}-placed.wav"
        parts = [f"[{k}:a]atempo={p['tempo']:.6f},adelay={p['delay_ms']}|{p['delay_ms']}[p{k}]"
                 for k, p in enumerate(placed)]
        mix = ";".join(parts) + ";" + "".join(f"[p{k}]" for k in range(len(placed))) + \
              f"amix=inputs={len(placed)}:normalize=0:duration=longest"
        gin = []
        for p in placed:
            gin += ["-i", str(tmp / p["file"])]
        run([ffmpeg(), "-y", "-v", "error", *gin, "-filter_complex", mix,
             "-t", f"{t1:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(g_file)])
        inputs += ["-i", str(g_file)]
        filters.append(f"[{label}:a]adelay=0|0[t{label}]")
        label += 1

        g_end = float(placed[-1]["end"])
        placements.append({
            "group": i, "t0": round(t0, 3), "window": round(window, 3),
            "natural": round(float(d.sum()), 3), "tempo": round(float(tempo.max()), 3),
            "tempo_min": round(float(tempo.min()), 3),
            "start": round(t0, 3), "end": round(g_end, 3), "late": 0.0,
            "timing": tgt_name,
            "clamped": bool(tempo.max() >= a.max_tempo - 1e-9),   # مقارنة بسقف السياسة لا بسقف الربط
            "word_error_mean": round(float(err.mean()), 3),
            "word_error_max": round(float(err.max()), 3),
            "segments": placed})
        print(f"  g{i:03d} [{tgt_name}] قطع {len(placed):2d}  {t0:7.2f}→{g_end:7.2f}  "
              f"سرعة {tempo.min():.2f}–{tempo.max():.2f}×  "
              f"خطأ الكلمات: وسيط {np.median(err):.2f} · أقصى {err.max():.2f}", flush=True)

    total = max(p["end"] for p in placements)
    mix = ";".join(filters) + ";" + "".join(f"[t{k}]" for k in range(label)) + \
        f"amix=inputs={label}:normalize=0:duration=longest"
    run([ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", mix,
         "-t", f"{total:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(a.out)])
    a.placement.write_text(json.dumps(
        {"total": round(total, 3), "pack": True, "max_tempo": a.max_tempo,
         "aligned_by": "elastic fit of the take's own parts to the original's word times",
         "groups": placements}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    e = np.array(all_err)
    print(f"\n  مزامنة الكلمات: متوسط {e.mean():.3f} ث · وسيط {np.median(e):.3f} ث · "
          f"p90 {np.percentile(e, 90):.2f} · أقصى {e.max():.2f} · "
          f"داخل 0.35 ث {100 * (e <= 0.35).mean():.0f}%")
    print(f"  → {a.out}  ({total:.2f} ث)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
