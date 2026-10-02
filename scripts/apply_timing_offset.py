#!/usr/bin/env python3
"""أزِح خطة التوقيت كلها (النوافذ + توقيت الكلمات) بمقدار مقيس.

السبب المقيس: كل توقيت كلمات هذا الفيلم مشتقّ من تفريغ **الجذع الصوتي**
(`full-asr-vocal.json`)، وقياسان مستقلان يقولان إنه متأخر عن صوت الفيلم نفسه:
  · مقارنة كلمة-كلمة مع تفريغ صوت الفيلم مباشرة: وسيط الإزاحة +0.230 ث (317 زوجًا)
  · مقارنة ببداية الطاقة الفعلية في نطاق الكلام: الجذع −0.584 ث مقابل
    تفريغ الفيلم −0.382 ث ⇒ فرق 0.20 ث لصالح تفريغ الفيلم
فالدبلجة كانت تبدأ كلامها متأخرة نحو ربع ثانية عن الصورة — وهو ما تسمعه الأذن
«مزامنة سيئة» ولا يكشفه مقياس الكلمات لأنه يقيس بالمرجع نفسه.

هنا تُزاح الحدود المشتركة وتوقيت الكلمات بالمقدار نفسه (‎--delta‎ سالب = أبكر)،
مع حجز بداية الفيلم عند حدّها الأدنى. النصوص والأخذات لا تُمسّ.

    python scripts/apply_timing_offset.py work/into-the-wild \
        --timing full-timed-v2.json --groups groups.json --delta -0.25 \
        --out-timing full-timed-v2s.json --out-groups groups-shifted.json \
        --min-start 0.12
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--timing", required=True)
    ap.add_argument("--groups", required=True)
    ap.add_argument("--delta", type=float, required=True, help="بالثواني (سالب = أبكر)")
    ap.add_argument("--out-timing", required=True)
    ap.add_argument("--out-groups", required=True)
    ap.add_argument("--min-start", type=float, default=0.0,
                    help="لا تنزل ببداية الفيلم تحت هذا الزمن")
    a = ap.parse_args()

    tdoc = json.loads((a.work / a.timing).read_text(encoding="utf-8"))
    words = tdoc["words"]
    for w in words:
        for k in ("t0", "t1"):
            if w.get(k) is not None:
                w[k] = round(float(w[k]) + a.delta, 3)
    (a.work / a.out_timing).write_text(json.dumps(tdoc, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"توقيت: {len(words)} كلمة أُزيحت {a.delta:+.2f} ث → {a.out_timing}")

    gdoc = json.loads((a.work / a.groups).read_text(encoding="utf-8"))
    groups = gdoc["groups"]
    bounds = [float(g["t0"]) + a.delta for g in groups] + [float(groups[-1]["t1"]) + a.delta]
    bounds[0] = max(bounds[0], a.min_start)                 # بداية الفيلم
    for k in range(1, len(bounds)):                          # رتّب تصاعديًا بلا انقلاب
        bounds[k] = max(bounds[k], bounds[k - 1] + 0.5)
    for g, b0, b1 in zip(groups, bounds, bounds[1:]):
        g["t0"], g["t1"] = round(b0, 3), round(b1, 3)
        g["window"] = round(b1 - b0, 3)
    (a.work / a.out_groups).write_text(json.dumps(gdoc, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    g0, g1 = groups[0], groups[-1]
    print(f"نوافذ: {len(groups)} مجموعة أُزيحت {a.delta:+.2f} ث → {a.out_groups}")
    print(f"  أول نافذة {g0['t0']} → {g0['t1']} · آخر نافذة {g1['t0']} → {g1['t1']}")
    print("  (النوافذ متصلة · النصوص والأخذات لم تُمسّ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
