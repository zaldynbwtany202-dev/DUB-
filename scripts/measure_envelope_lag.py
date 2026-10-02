#!/usr/bin/env python3
"""قِس فارق التزامن بين الدبلجة والفيلم الأصلي بلا الاعتماد على تفريغ آلي.

قياس الكلمات يقارن صوتنا بتوقيت استُخرج من تفريغ (whisper) — فإن كان ذلك
التفريغ نفسه متأخرًا أو متقدمًا عن الكلام الحقيقي، فالمقياس يوافق الخطأ ولا
يكشفه. هذه الأداة تقارن **بنية الكلام نفسها**: مغلّف الطاقة (خطوة 10 مللي،
نافذة 50 مللي) لصوتنا مقابل مغلّف طاقة صوت الفيلم الأصلي، ثم تبحث عن الإزاحة
التي تُعلي الارتباط (معامل بيرسون على المغلّفين). تُقاس إزاحة عامة وإزاحة لكل
مجموعة، وتُطبَّع بالإزاحة العامة كي يظهر الانحراف الموضعي وحده.

    python scripts/measure_envelope_lag.py --slug into-the-wild \
        --source library/into-the-wild/source.local.mp4 \
        --voice work/into-the-wild/build/into-the-wild-voice.wav \
        --offset 0.16 --range 739.34 --json work/into-the-wild/qa/envelope-lag.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def envelope(path: Path, sr: int = 100, lo: float = 200.0, hi: float = 4000.0,
             start: float = 0.0, dur: float | None = None) -> np.ndarray:
    """مغلّف طاقة الكلام (10 مللي) بعد ترشيح نطاق الكلام."""
    cmd = [ffmpeg(), "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
            "-af", f"highpass=f={lo:.0f},lowpass=f={hi:.0f}", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<f4")
    hop = 16000 // sr
    n = len(x) // hop
    x = x[: n * hop].reshape(n, hop)
    e = np.sqrt((x.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    # تنعيم 150 مللي لإبراز بنية الكلام لا تفاصيل النبرة
    k = max(1, int(0.15 * sr))
    ker = np.hanning(k + 2)[1:-1]
    ker /= ker.sum()
    return np.convolve(e, ker, mode="same")


def best_lag(a: np.ndarray, b: np.ndarray, sr: int = 100, max_lag: float = 2.0) -> tuple[float, float]:
    """الإزاحة التي تُعلي الارتباط: موجبة = صوتنا متأخر عن الفيلم."""
    a = a - a.mean()
    b = b - b.mean()
    L = int(max_lag * sr)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    lags = np.arange(-L, L + 1)
    best = (0.0, -2.0)
    for lag in lags:
        if lag >= 0:
            x, y = a[lag:], b[: n - lag]
        else:
            x, y = a[: n + lag], b[-lag:]
        if len(x) < sr:                       # أقل من ثانية: لا معنى
            continue
        r = float(x @ y) / denom
        if r > best[1]:
            best = (lag / sr, r)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--voice", type=Path, required=True, help="مسار الصوت المدبلج (بلا موسيقى)")
    ap.add_argument("--bed", type=Path, default=None, help="مسار الموسيقى المُدوَّرة (لفحص تسرّب الصوت)")
    ap.add_argument("--offset", type=float, default=0.0, help="أين يبدأ صوتنا في الفيلم")
    ap.add_argument("--range", type=float, required=True, help="طول المقطع المدبلج (ث)")
    ap.add_argument("--groups", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()

    print("أقرأ صوت الفيلم…")
    src = envelope(a.source, start=0.0, dur=a.range)
    print(f"  مغلّف الأصل: {len(src) / 100:.1f} ث")
    print("أقرأ صوت الدبلجة…")
    voi = envelope(a.voice)
    print(f"  مغلّف الدبلجة: {len(voi) / 100:.1f} ث")

    # صوتنا يبدأ عند offset في الفيلم → نُزيح مغلّفه ليقابل الأصل
    shift = int(round(a.offset * 100))
    if shift > 0:
        voi = np.concatenate([np.zeros(shift), voi])

    lag, r = best_lag(voi, src, max_lag=2.0)
    print(f"\nالإزاحة العامة: {lag:+.2f} ث (ارتباط {r:.3f})  — "
          f"{'صوتنا متأخر' if lag > 0.05 else ('صوتنا متقدم' if lag < -0.05 else 'متطابق تقريبًا')}")
    if lag > 0.05:
        print(f"  ⇒ لو أُزيح صوتنا {lag:.2f} ث إلى الأمام (أبكر) لأصبح التطابق أعلى")

    rows = []
    if a.groups and a.groups.exists():
        print("\nلكل مجموعة (بعد طرح الإزاحة العامة):")
        doc = json.loads(a.groups.read_text(encoding="utf-8"))
        for g in doc["groups"]:
            i = int(g["i"])
            t0, t1 = float(g["t0"]), float(g["t1"])
            if t1 > a.range:
                break
            s0, s1 = int(t0 * 100), int(t1 * 100)
            v0 = int(max(0.0, t0 + a.offset) * 100)
            v1 = int(max(0.0, t1 + a.offset) * 100)
            seg_src, seg_voi = src[s0:s1], voi[v0:v1]
            if len(seg_src) < 300 or len(seg_voi) < 300:
                continue
            n = min(len(seg_src), len(seg_voi))
            lg, rr = best_lag(seg_voi[:n], seg_src[:n], max_lag=1.5)
            rows.append({"group": i, "lag": round(lg, 3), "r": round(rr, 3)})
        lags = np.array([x["lag"] for x in rows])
        if len(lags):
            print(f"  وسيط الإزاحة الموضعية {np.median(lags):+.2f} ث · "
                  f"متوسط {lags.mean():+.2f} · مدى {lags.min():+.2f}…{lags.max():+.2f}")
            worst = sorted(rows, key=lambda z: -abs(z["lag"]))[:8]
            for w in worst:
                print(f"    g{w['group']:03d} {w['lag']:+.2f} ث (ارتباط {w['r']:.3f})")
            if np.median(lags) > 0.05:
                print("  ⇒ الإزاحة الموضعية موجبة: صوتنا متأخر داخل النوافذ أيضًا")

    if a.bed and a.bed.exists():
        print("\nفحص تسرّب الصوت في مسار الموسيقى (هل يُسمع الراوي الأصلي تحتنا؟):")
        bed = envelope(a.bed)
        n = min(len(bed), len(src))
        speech = src[:n] > np.percentile(src[:n], 75)
        quiet = src[:n] < np.percentile(src[:n], 25)
        r_speech = float(np.sqrt((bed[:n][speech] ** 2).mean()) + 1e-12)
        r_quiet = float(np.sqrt((bed[:n][quiet] ** 2).mean()) + 1e-12)
        print(f"  مستوى المغلّف في الكلام {20 * np.log10(r_speech):.1f} د.ب مقابل "
              f"{20 * np.log10(r_quiet):.1f} د.ب في الصمت · فارق {20 * np.log10(r_speech / r_quiet):+.1f} د.ب")
        lg, rr = best_lag(bed[:n], src[:n], max_lag=2.0)
        print(f"  ارتباط مسار الموسيقى بصوت الفيلم: إزاحة {lg:+.2f} ث · ارتباط {rr:.3f}"
              "  ← ارتباط قوي يعني أن صوت الراوي الأصلي ما زال في المسار")

    if a.json:
        a.json.write_text(json.dumps(
            {"global_lag": lag, "global_r": r, "per_group": rows},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n→ {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
