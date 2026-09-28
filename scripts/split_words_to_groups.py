#!/usr/bin/env python3
"""وزّع سردًا متصلًا على نوافذ المجموعات بالتناسب مع طول النافذة.

المشكلة: نصٌّ متصل (سرد) ونوافذُ مجموعاتٍ مختلفة الأطوال. التوزيع الجشع يملأ
المجموعات الأولى ويفرّغ الأخيرة، فيصير الإلقاء سريعًا في الأول وصامتًا في الآخر.
الحل: حدّد لكل مجموعة حصّتها من الحروف (طول النافذة × سرعة الصوت المقاسة)، ثم
أسنِد كل جملة إلى المجموعة التي تقع منتصفاتُ حروفها داخل حصّتها. هذا يحفظ الترتيب
ويوزّع الفائض والنقص على كل المجموعات بدل حصرِه في الطرفين.

    python scripts/split_words_to_groups.py work/<slug>/words-source-eg.json work/<slug>/groups.json \
        --cps 12.09 --start 0 --end 10 --out work/<slug>/words-merged-0-9.json
"""
from __future__ import annotations

import argparse, json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sentences", type=Path)
    ap.add_argument("groups", type=Path)
    ap.add_argument("--cps", type=float, required=True, help="سرعة الصوت المقاسة (حرف/ث مع المسافات)")
    ap.add_argument("--auto-cps", action="store_true",
                    help="استخدم السرعة الفعلية للنصّ (حروفه ÷ زمنه) بدل سرعة الصوت: "
                         "توزيع متساوٍ حتى لو كان النصّ أقصر/أطول من الفيلم، بدل أن يُفرّغ الطرف الأخير")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10**9)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    sents = json.loads(a.sentences.read_text(encoding="utf-8"))["sentences"]
    doc = json.loads(a.groups.read_text(encoding="utf-8"))
    groups = [g for g in doc["groups"] if a.start <= int(g["i"]) < a.end]

    # حدود الحروف: نهاية المجموعة k = مجموع حصص المجموعات 0..k
    cps = a.cps
    if a.auto_cps:
        total_time = sum(float(g["window"]) for g in groups)
        text_total = sum(len(x) for x in sents)
        cps = text_total / total_time
        print(f"  السرعة الفعلية للنصّ: {cps:.2f} حرف/ث (الصوت {a.cps:.2f}) "
              f"→ الإلقاء كله بسرعة {cps / a.cps:.2f}× بلا فراغات")
    edges, acc = [0.0], 0.0
    for g in groups:
        acc += float(g["window"]) * cps
        edges.append(acc)

    buckets = {int(g["i"]): [] for g in groups}
    pos = 0.0
    for s in sents:
        mid = pos + len(s) / 2
        k = next((i for i in range(len(groups)) if edges[i] <= mid < edges[i + 1]), len(groups) - 1)
        buckets[int(groups[k]["i"])].append(s)
        pos += len(s)

    rows, tempos = {}, []
    for g in groups:
        i, w = int(g["i"]), float(g["window"])
        text = " ".join(buckets[i])
        rows[str(i)] = text
        tempos.append(len(text) / a.cps / w)

    for g, t in zip(groups, tempos):
        print(f"  g{int(g['i']):03d}  نافذة {float(g['window']):5.1f} ث  حروف {len(rows[str(int(g['i']))]):4d}  سرعة {t:4.2f}×")
    mean = sum(tempos) / len(tempos)
    print(f"  متوسط السرعة {mean:.2f}× · الأبطأ {min(tempos):.2f}× · الأسرع {max(tempos):.2f}×")
    a.out.write_text(json.dumps({"source": str(a.sentences), "cps": a.cps,
                                 "mean_tempo": round(mean, 3), "groups": rows},
                                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  → {a.out}")


if __name__ == "__main__":
    main()
