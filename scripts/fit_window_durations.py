#!/usr/bin/env python3
"""اضبط حدود نوافذ المجموعات حتى تسع كل أخذة ضمن حدود السرعة المقفلة.

كل نافذة مبنية من توقيت كلمات الأصل (rewindow_from_timing.py). تبقى حالة واحدة
لا تكفيها النافذة: أخذة أقصر من أن تملأ نافذتها ولو بأدنى سرعة مسموحة (مثال
into-the-wild g021: أخذته 25.59 ث ونافذته 28.43 ث ⇒ تحتاج 0.900 بالضبط، وأي خطأ
في الجدول يُنتج فراغًا). الحل جدولة لا صوت: تُزاح الحدود المشتركة قليلًا
(≤ --max-shift ثانية) حتى تدخل كل نسبة داخل [min_tempo, max_tempo]، والتوقيت
المشتق من الكلمات يبقى المرجع. النصوص والأخذات لا تُمسّ.

    python scripts/fit_window_durations.py work/into-the-wild \
        --takes work/into-the-wild/takes-voice25 --min-tempo 0.90 --max-tempo 1.78
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import soundfile as sf


def take_seconds(takes: Path, i: int) -> float | None:
    for name in (f"g{i:03d}.mp3", f"g{i:03d}.wav"):
        f = takes / name
        if f.exists():
            info = sf.info(str(f))
            return float(info.frames) / float(info.samplerate)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--takes", type=Path, required=True)
    ap.add_argument("--groups", default="groups.json")
    ap.add_argument("--backup", default="groups.before-windowfit.json")
    ap.add_argument("--min-tempo", type=float, default=0.90)
    ap.add_argument("--max-tempo", type=float, default=1.78)
    ap.add_argument("--max-shift", type=float, default=1.2,
                    help="أقصى إزاحة لكل حد عن التوقيت المشتق من الكلمات")
    a = ap.parse_args()

    path = a.work / a.groups
    doc = json.loads(path.read_text(encoding="utf-8"))
    groups = doc["groups"]
    n = len(groups)
    plan = [float(g["t0"]) for g in groups] + [float(groups[-1]["t1"])]
    nat = [take_seconds(a.takes, int(g["i"])) for g in groups]

    b = list(plan)
    pin0, pinN = plan[0], plan[-1]
    for _ in range(300):
        for k in range(n):                      # تقدّم
            if nat[k]:
                b[k + 1] = min(max(b[k + 1], b[k] + nat[k] / a.max_tempo),
                               b[k] + nat[k] / a.min_tempo)
            b[k + 1] = min(max(b[k + 1], plan[k + 1] - a.max_shift),
                           plan[k + 1] + a.max_shift)
        for k in range(n - 1, -1, -1):          # تراجع
            if nat[k]:
                b[k] = max(min(b[k], b[k + 1] - nat[k] / a.max_tempo),
                           b[k + 1] - nat[k] / a.min_tempo)
            b[k] = min(max(b[k], plan[k] - a.max_shift), plan[k] + a.max_shift)
        b[0], b[-1] = pin0, pinN
    b[0], b[-1] = pin0, pinN

    moved, bad = [], []
    for k, g in enumerate(groups):
        old0, old1 = float(g["t0"]), float(g["t1"])
        g["t0"], g["t1"] = round(b[k], 3), round(b[k + 1], 3)
        g["window"] = round(g["t1"] - g["t0"], 3)
        r = (nat[k] / g["window"]) if (nat[k] and g["window"] > 0) else None
        if r and (r < a.min_tempo - 1e-6 or r > a.max_tempo + 1e-6):
            bad.append((int(g["i"]), round(r, 3), nat[k], round(g["window"], 2)))
        if abs(g["t0"] - old0) > 0.02 or abs(g["t1"] - old1) > 0.02:
            moved.append((int(g["i"]), old0, g["t0"], old1, g["t1"]))

    if moved:
        shutil.copy(path, a.work / a.backup)
        print(f"احتياطي قبل الضبط → {a.work / a.backup}")
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"تحرّك {len(moved)} حدًا من {n} مجموعة · حُفظ → {path}")
    for i, o0, n0, o1, n1 in moved[:14]:
        print(f"  g{i:03d}  [{o0:8.2f} → {n0:8.2f}]  [{o1:8.2f} → {n1:8.2f}]  "
              f"({n1 - n0:.2f} ث)")
    if bad:
        print("ما زال خارج المدى (يُذكر في التقرير ولا يُخفى):")
        for i, r, ntk, win in bad:
            print(f"  g{i:03d} نسبة {r} · أخذة {ntk:.1f} ث · نافذة {win:.2f} ث")
    else:
        print("كل النسب داخل المدى ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
