#!/usr/bin/env python3
"""حارس المتجانسات: كلمات عربية تُكتب بلا تشكيل ولها قراءتان مختلفتان في المعنى.

وُلد هذا الحارس من عيب حقيقي: كلمة «قوتهم» (الطعام) نطقها المحرّك بمعنى القوّة
(قُوَّتُهم)، وكلمة «الرزق» بوجه غير الذي قصده النص. الكلمتان مكتوبتان بلا تشكيل،
فاختار المحرّك قراءة أخرى. العلاج البيّن: كشف هذه الكلمات **قبل التوليد** وعرض
البديل الذي لا يحتمل وجهين.

    python scripts/guard_homonyms.py work/into-the-wild/groups.json
    python scripts/guard_homonyms.py work/hajj-dream-2108415/groups.json --json qa/homonyms.json
    python scripts/guard_homonyms.py work/*/groups.json --all      # كل الأفلام

القائمة قابلة للتحرير في config/homonym-hazards.json: كل مدخل {كلمة، السبب، بديل
مقترح}. لا يمنع الحارس البناء بنفسه — هو تقرير يُقرأ قبل التوليد (وقاعدة في
SYSTEM.md §5.3).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAZARDS = ROOT / 'config/homonym-hazards.json'
AR_DIACRITICS = re.compile(r'[\u064b-\u065f\u0670\u0640]')


def norm(w: str) -> str:
    return AR_DIACRITICS.sub('', w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('groups', nargs='+', type=Path)
    ap.add_argument('--json', type=Path, default=None)
    a = ap.parse_args()

    listdoc = json.loads(HAZARDS.read_text(encoding='utf-8'))
    entries = listdoc['hazards']
    # كلمات مفردة (المفتاح) + الصيغ المسندة داخل النص تُلتقط بالمطابقة الجزئية
    total = 0
    out = []
    for path in a.groups:
        if not path.exists():
            print(f'  ✗ لا وجود لـ{path}')
            continue
        doc = json.loads(path.read_text(encoding='utf-8'))
        groups = doc['groups'] if isinstance(doc, dict) else doc
        hits = []
        for g in groups:
            text = norm(g.get('text', ''))
            for h in entries:
                # حدود صارمة: لا تُلتقط الكلمة داخل كلمة أخرى (فيجد/يعلم/المتعلم)
                pat = r'(?<![\u0600-\u06ff])' + h['match'] + r'(?![\u0600-\u06ff])'
                for m in re.finditer(pat, text):
                    a0, b0 = max(0, m.start() - 35), min(len(text), m.end() + 35)
                    hits.append({'group': int(g['i']), 'word': m.group(),
                                 'why': h['why'], 'alternatives': h.get('alternatives', []),
                                 'context': text[a0:b0]})
        total += len(hits)
        print(f"\n=== {path} — {len(hits)} موضعًا يحتاج قرار نطق ===")
        if not hits:
            print('  ✓ لا متجانسات خطِرة — النص نظيف')
        seen = set()
        for h in hits:
            key = (h['group'], h['word'])
            if key in seen:
                continue
            seen.add(key)
            alt = ' · '.join(h['alternatives']) if h['alternatives'] else 'بلا بديل مسجّل'
            print(f"  g{h['group']:03d}  «{h['word']}»  — {h['why']}")
            print(f"        بدائل: {alt}")
            print(f"        السياق: …{h['context']}…")
        out.append({'file': str(path), 'hits': hits, 'count': len(hits)})

    print(f'\nالإجمالي: {total} موضعًا في {len(a.groups)} ملفًا.'
          f'\nالقاعدة: عالج الموضع ببديل لا يحتمل وجهين، أو اعرضه على المستخدم، قبل التوليد.')
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({'files': out, 'total': total}, ensure_ascii=False, indent=1),
                          encoding='utf-8')
        print(f'  → {a.json}')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
