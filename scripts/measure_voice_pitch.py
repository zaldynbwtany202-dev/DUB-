#!/usr/bin/env python
"""How steady is the voice, take by take? Measured, not guessed.

The complaint was that the tone sometimes changes. The engine generates every
group as an independent take, so the baseline pitch of a take can sit lower or
higher than the next one's -- and on a film that reads as "the narrator keeps
changing". This reports, for every take: the voiced pitch, how far it sits from
the session's target (in hertz and in semitones), and how much it drifts inside
the take itself.

  python3 scripts/measure_voice_pitch.py --takes work/into-the-wild/takes-voice13
  python3 scripts/measure_voice_pitch.py --audio work/into-the-wild/build/into-the-wild-voice.wav --windows
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def frame_pitch(x: np.ndarray, sr: int, fmin: float = 70.0, fmax: float = 300.0,
                frame: float = 0.04, hop: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """Autocorrelation pitch per frame; returns (times, f0) for voiced frames only."""
    n, h = int(frame * sr), int(hop * sr)
    lo, hi = int(sr / fmax), int(sr / fmin)
    ts, fs = [], []
    for a in range(0, len(x) - n, h):
        f = x[a:a + n]
        rms = float(np.sqrt((f ** 2).mean()))
        if rms < 0.01:
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, "full")[n - 1:]
        ac = ac / (ac[0] + 1e-12)
        seg = ac[lo:hi]
        if seg.size == 0:
            continue
        k = int(np.argmax(seg))
        if seg[k] > 0.32:
            ts.append(a / sr)
            fs.append(sr / (lo + k))
    return np.array(ts), np.array(fs)


def report(name: str, x: np.ndarray, sr: int, target: float | None, windows: bool) -> dict:
    ts, f0 = frame_pitch(x, sr)
    if f0.size == 0:
        return {"name": name, "voiced": 0.0, "median": 0.0, "semis": 0.0}
    # Autocorrelation occasionally locks onto double the period (an octave down).
    # Frames more than a third of an octave from the take's own median are those
    # mistakes, not the narrator moving: drop them before saying anything about
    # how steady the take is.
    rough = float(np.median(f0))
    keep = (f0 > rough / 2 ** (1 / 3)) & (f0 < rough * 2 ** (1 / 3))
    ts, f0 = ts[keep], f0[keep]
    if f0.size == 0:
        return {"name": name, "voiced": 0.0, "median": 0.0, "semis": 0.0}
    med = float(np.median(f0))
    semis = 12 * np.log2(med / target) if target else 0.0
    # drift inside the take: median of every 3 s window, then the spread of those
    drifts = []
    if windows and ts.size:
        for a in np.arange(ts[0], ts[-1] + 0.001, 3.0):
            w = f0[(ts >= a) & (ts < a + 3.0)]
            if w.size >= 30:
                drifts.append(float(np.median(w)))
    spread = (max(drifts) - min(drifts)) if len(drifts) > 1 else 0.0
    return {"name": name, "voiced": round(float(f0.size) / max(1, round(len(x) / sr) * 100), 3),
            "median": round(med, 1), "p10": round(float(np.percentile(f0, 10)), 1),
            "p90": round(float(np.percentile(f0, 90)), 1),
            "semis": round(float(semis), 2),
            "drift_hz": round(spread, 1),
            "drift_semis": round(float(12 * np.log2((max(drifts) + 1e-9) / (min(drifts) + 1e-9))), 2)
            if len(drifts) > 1 else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", default=None)
    ap.add_argument("--audio", default=None)
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--target", type=float, default=None)
    a = ap.parse_args()

    items = []
    if a.takes:
        for p in sorted(glob.glob(str(Path(a.takes) / "g*.mp3"))):
            x, sr = sf.read(p, dtype="float32", always_2d=False)
            if x.ndim > 1:
                x = x.mean(axis=1)
            items.append((Path(p).stem, x, sr))
    if a.audio:
        p = Path(a.audio)
        x, sr = sf.read(str(p), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        items.append((p.stem, x, sr))
    if not items:
        raise SystemExit("لا مدخلات")

    meds = []
    first = [report(n, x, sr, None, False) for n, x, sr in items]
    for r in first:
        if r.get("median"):
            meds.append(r["median"])
    target = a.target or float(np.median(meds))
    print(f"الهدف (وسيط المجموعة): {target:.1f} هرتز\n")
    print(f"{'التسجيل':<12}{'نبرته':>8}{'بعده':>9}{'مدى 10–90':>18}{'تغيّر داخلي':>13}")
    rows = []
    for (n, x, sr) in items:
        r = report(n, x, sr, target, a.windows)
        rows.append(r)
        rng = f"{r.get('p10', 0)}–{r.get('p90', 0)}"
        print(f"{n:<12}{r['median']:>8}{r['semis']:>+9.2f}{rng:>18}"
              f"{r.get('drift_semis', 0):>+13.2f}")
    if len(rows) > 1:
        s = np.array([r["semis"] for r in rows if r.get("median")])
        print(f"\nانتشار بين التسجيلات: {s.min():+.2f} … {s.max():+.2f} نصف نغمة "
              f"(المدى {s.max() - s.min():.2f} نصف نغمة)")
        dr = np.array([r.get("drift_semis", 0.0) for r in rows])
        print(f"متوسط التغيّر داخل التسجيل الواحد: {dr.mean():.2f} نصف نغمة")
    if a.json:
        Path(a.json).write_text(json.dumps({"target": target, "rows": rows},
                                           ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
