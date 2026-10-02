#!/usr/bin/env python3
"""اجمع نوافذ تفريغ الصوت في ملف كلمات واحد (asr-mix.json).

whisper-cli يعمل على نوافذ متداخلة (40 ث كل 30 ث، لأن النموذج يعالج 30 ثانية
داخلية). كل نافذة تُحفظ `seg-<البداية>.json` بمقاطع بطول token واحد (‎-ml 1‎)،
فنعيد تركيب الكلمات: قطعة تبدأ بمسافة تفتح كلمة جديدة، وما دونها يلتصق بها.

    python scripts/merge_asr_windows.py --dir /tmp/asr-dense-0-740 \
        --out work/into-the-wild/asr-mix.json --step 30 --take 34

الناتج: كل كلمة بتوقيت مطلق (ثانية) من الفيلم، بلا تكرار عند مناطق التداخل.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")


def merge_window(entries: list[dict]) -> list[dict]:
    """قطع whisper → كلمات، بتوقيت نسبي لبداية النافذة."""
    words: list[dict] = []
    cur: dict | None = None
    for e in entries:
        text = e.get("text", "")
        off = e.get("offsets") or {}
        t0 = (off.get("from") or 0) / 1000.0
        t1 = (off.get("to") or 0) / 1000.0
        if text.startswith(" ") or cur is None:
            if cur and cur["w"].strip():
                words.append(cur)
            cur = {"w": text.strip(), "t0": t0, "t1": t1}
        else:
            cur["w"] = cur["w"] + text.strip()
            cur["t1"] = t1
    if cur and cur["w"].strip():
        words.append(cur)
    return words


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--step", type=float, default=30.0, help="إزاحة بداية كل نافذة")
    ap.add_argument("--take", type=float, default=34.0,
                    help="كم ثانية نأخذ من كل نافذة (تجنّب حوافّ النافذة)")
    a = ap.parse_args()

    files = sorted(a.dir.glob("seg-*.json"),
                   key=lambda p: float(p.stem.split("-")[1].split(".")[0]))
    if not files:
        print(f"لا ملفات في {a.dir}")
        return 2
    all_words: list[dict] = []
    for f in files:
        start = float(f.stem.split("-")[1])
        doc = json.loads(f.read_text(encoding="utf-8"))
        entries = doc.get("transcription") or []
        ws = merge_window(entries)
        kept = 0
        for w in ws:
            t0, t1 = start + w["t0"], start + w["t1"]
            if t0 < start + 0.4 or t0 > start + a.take:
                continue                      # حوافّ النافذة معروفة الخطأ
            if all_words and t0 - all_words[-1]["t1"] < -0.05:
                continue                      # تداخل: الأسبق أولى
            if all_words and abs(t0 - all_words[-1]["t0"]) < 0.01 \
                    and DIACRITICS.sub("", w["w"]) == DIACRITICS.sub("", all_words[-1]["w"]):
                continue
            all_words.append({"w": w["w"], "t0": round(t0, 3), "t1": round(t1, 3)})
            kept += 1
        print(f"  نافذة {start:6.0f}: {len(ws):4d} كلمة مُركَّبة · أُخذ {kept}")

    out = {"slug": a.slug, "source": str(a.dir), "model": "whisper small",
           "how": "نوافذ 40 ث كل 30 ث على صوت الفيلم (mix) · كلمات مُركَّبة من القطع",
           "words": all_words}
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    span = (all_words[-1]["t1"] - all_words[0]["t0"]) if all_words else 0
    print(f"→ {a.out} · {len(all_words)} كلمة · {span:.0f} ث · "
          f"{len(all_words) / max(1e-9, span) * 60:.0f} كلمة/دقيقة")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
