#!/usr/bin/env python3
"""انسخ أدلّة البناء من مجلد `build/` إلى `qa/` قبل أن تضيع.

السبب: `build/` مستثنى في .gitignore (لأنه يحوي الماستر الصوتي بحجم جيغا)،
وكل مساحة للبيئة تُرجع المستودع من git — فملفات الموضع وتقارير الماستر التي
كُتبت هناك تختفي (حصل فعلًا مع بناء 0→50: فُقد ملف الموضع واضطررنا لإعادة البناء
للحصول عليه). النسخة الموجودة في `qa/` متعقَّبة فتبقى.

    python scripts/archive_build_evidence.py work/into-the-wild
    python scripts/archive_build_evidence.py work/hajj-dream-2108415 --dry
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('work', type=Path, help='مجلد العمل مثل work/into-the-wild')
    ap.add_argument('--dry', action='store_true', help='اطبع فقط بلا نسخ')
    a = ap.parse_args()

    build, qa = a.work / 'build', a.work / 'qa'
    if not build.is_dir():
        print(f'لا مجلد build في {a.work}')
        return 1
    qa.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(build.glob('*.json')):
        name = src.name
        m = re.match(r'.*-placement-(\d+)-(\d+)\.json$', name)
        if m:
            name = f'placement-{m.group(1)}-{m.group(2)}.json'
        elif name.startswith('master-report') or 'master-report' in name:
            pass                                  # يُنسخ باسمه
        elif name.endswith('-selection.json') or 'selection' in name:
            pass
        else:
            continue                              # غير أدلّة (ملفات عمل)
        dst = qa / name
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            print(f'  = موجود ومحدَّث {dst}')
            continue
        print(f'  → {dst}')
        if not a.dry:
            shutil.copy2(src, dst)
        copied += 1
    print(f'نُسخ {copied} ملفًا إلى {qa} (المصدر build/ مستثنى من git)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
