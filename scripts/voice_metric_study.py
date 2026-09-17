#!/usr/bin/env python3
"""voice_metric_study.py -- which acoustic feature can actually tell these voices apart?

verify_voice.py shipped a nearest-neighbour gate on MFCC mean+spread, calibrated
on 8 bank takes and 6 negatives, and it looked clean: same voice 20.1 at worst,
different voice 33.2 at best. Held-out data destroyed it -- 3 wrong verdicts out
of 7, including a das-full take at 10.4 from the bank (closer than the bank's own
outlier) and one of the film's own takes at 47.6 (rejected). The calibration set
was too small to show that content, not voice, dominates those features.

This study measures every candidate feature against all the audio the repo has:

  same voice      49 takes, doctor-lecture (voice-01 of that session)
  other voice A   32 takes, das-full (voice-00, documented masculine)
  other voice B    4 takes, 0911-1 locked narrator (voice-33)

For each feature it reports the whole within-voice distribution against the whole
between-voice distribution, the equal-error threshold and the errors at it. A
feature is usable only if between-voice distances start above where same-voice
distances end; the ratio says how much room there is. Nothing here is a gate --
it is the evidence a gate has to be built on.

usage: voice_metric_study.py [--max-files N]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_voice import (N_MELS, N_MFCC, ROOT, _mel_filters, _voiced,  # noqa: E402
                          f0_median, pcm)

SR = 16000
SETS = {
    "film": sorted(p for p in (ROOT / "dubs/doctor-lecture/takes-pause").glob("seg-????.mp3")),
    "v00": sorted((ROOT / "dubs/das-full/takes-v2").glob("seg-????.mp3")),
    "v33": sorted((ROOT / "library/voices/locked-narrator").glob("seg-????.mp3")),
}


def features(x: np.ndarray) -> dict[str, np.ndarray]:
    """Every candidate description of one recording, keyed by feature name."""
    frames = _voiced(x)
    if len(frames) < 20:
        return {}
    sig = np.array([f * np.hanning(len(f)) for f in frames])
    spec = np.abs(np.fft.rfft(sig, n=1024)) ** 2
    power = spec.mean(axis=0)
    freqs = np.linspace(0, SR / 2, spec.shape[1])

    logmel = np.log(np.maximum(spec @ _mel_filters(1024).T, 1e-10))
    n = logmel.shape[1]
    k = np.arange(N_MFCC + 1)[:, None]
    nn = np.arange(n)[None, :]
    mfcc = logmel @ np.cos(np.pi * k * (2 * nn + 1) / (2 * n))[1:].T

    # long-term average spectrum over mel bands, per file standardised: this is
    # the classic speaker feature, and standardising removes the level and the
    # overall tilt that content and microphone gain put on it
    ltas = np.log(np.maximum(power @ _mel_filters(1024).T, 1e-10))
    ltas_n = (ltas - ltas.mean()) / (ltas.std() + 1e-9)

    cum = np.cumsum(power)
    rolloff = freqs[np.searchsorted(cum, 0.85 * cum[-1])]
    centroid = float((power * freqs).sum() / max(power.sum(), 1e-9))
    band = float(np.sqrt((power * (freqs - centroid) ** 2).sum() / max(power.sum(), 1e-9)))
    slope = float(np.polyfit(np.log(freqs[1:]), np.log(power[1:] + 1e-12), 1)[0])
    hf = float(power[freqs > 4000].sum() / max(power.sum(), 1e-9))
    f0 = f0_median(frames)
    f0s = np.array([f for f in (_f0_frame(fr) for fr in frames) if f])

    return {
        "A mfcc mean+std+centroid": np.array(list(mfcc.mean(0)) + list(mfcc.std(0)) + [centroid]),
        "B mfcc std+centroid+f0": np.array(list(mfcc.std(0)) + [centroid, f0 or 0.0]),
        "C mfcc mean only": mfcc.mean(0),
        "D ltas mel standardised": ltas_n,
        "E ltas mel raw": ltas,
        "F spectral shape (7)": np.array([centroid, rolloff, band, slope, hf,
                                          f0 or 0.0, float(np.std(f0s)) if len(f0s) else 0.0]),
        "G ltas std + mfcc std": np.array(list(ltas_n) + list(mfcc.std(0))),
    }


def _f0_frame(f: np.ndarray) -> float | None:
    lo, hi = int(SR / 350), int(SR / 70)
    f = f - f.mean()
    if float(np.dot(f, f)) <= 0:
        return None
    ac = np.correlate(f, f, mode="full")[len(f) - 1:][lo:hi]
    if len(ac) == 0:
        return None
    ac = ac / (ac[0] if ac[0] > 0 else 1.0)
    best = int(np.argmax(ac))
    if float(ac[best]) < 0.3:
        return None
    earlier = np.flatnonzero(ac[:best + 1] >= 0.85 * float(ac[best]))
    return SR / ((int(earlier[0]) if len(earlier) else best) + lo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-files", type=int, default=0, help="cap per set, 0 = all")
    a = ap.parse_args()
    sets = {k: (v[: a.max_files] if a.max_files else v) for k, v in SETS.items()}
    for k, v in sets.items():
        print(f"{k}: {len(v)} ملف")

    print("\nفك الترميز وقياس الميزات…", flush=True)
    feats: dict[str, dict[str, list[np.ndarray]]] = {}
    for name, files in sets.items():
        for p in files:
            got = features(pcm(p))
            for fk, fv in got.items():
                feats.setdefault(fk, {}).setdefault(name, []).append(fv)

    print(f"\n{'الميزة':30} {'أقصي نفس الصوت':>14} {'أدني صوت آخر':>13} "
          f"{'الهامش':>7} {'عتبة EER':>9} {'أخطاء':>12}")
    rows = []
    for fk, by in feats.items():
        film = np.array(by.get("film", []))
        if len(film) < 2:
            continue
        # standardise every dimension on the same-voice set so no single loud
        # coefficient drives the Euclidean distance
        mu, sd = film.mean(0), film.std(0) + 1e-9
        z = lambda arr: (arr - mu) / sd                       # noqa: E731
        fz = z(film)
        within = [float(np.linalg.norm(p - q))
                  for p, q in itertools.combinations(fz, 2)]
        between = []
        for other in ("v00", "v33"):
            arr = np.array(by.get(other, []))
            if not len(arr):
                continue
            oz = z(arr)
            between += [float(np.linalg.norm(p - q)) for p in fz for q in oz]
        if not within or not between:
            continue
        w, b = np.array(within), np.array(between)
        # equal-error rate: the threshold where false accepts == false rejects
        grid = np.unique(np.concatenate([w, b]))
        lo, hi = grid.min(), grid.max()
        best = min(((float(np.mean(w > t)) + float(np.mean(b <= t))) / 2, t)
                   for t in np.linspace(lo, hi, 400))
        eer, thr = best
        margin = float(b.min() / max(w.max(), 1e-9))
        rows.append((fk, float(w.max()), float(b.min()), margin, float(thr), eer,
                     int((w > thr).sum()), int((b <= thr).sum()), len(w), len(b)))
        print(f"{fk:30} {w.max():14.2f} {b.min():13.2f} {margin:7.2f}× {thr:9.2f} "
              f"{eer * 100:5.1f}٪ ({int((w > thr).sum())}/{len(w)} رفض خطأ، "
              f"{int((b <= thr).sum())}/{len(b)} قبول خطأ)")

    best = max(rows, key=lambda r: r[3])
    print(f"\nالأفضل فصلًا: {best[0]} — هامش {best[3]:.2f}×، خطأ متساوي {best[5] * 100:.1f}٪")
    verdict = ("صالح لبوابة آلية" if best[3] > 1.5 and best[5] < 0.02
               else "غير صالح لبوابة آلية — الاعتماد على الأذن والبنك المرجعي")
    print(f"الحكم: {verdict}")
    (ROOT / "dubs/doctor-lecture/voice-metric-study.json").write_text(
        json.dumps([{"feature": r[0], "same_voice_max": round(r[1], 3),
                     "other_voice_min": round(r[2], 3), "margin": round(r[3], 3),
                     "eer_threshold": round(r[4], 3), "eer": round(r[5], 4),
                     "false_rejects": r[6], "false_accepts": r[7],
                     "pairs_same": r[8], "pairs_other": r[9]} for r in rows],
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("التفاصيل: dubs/doctor-lecture/voice-metric-study.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
