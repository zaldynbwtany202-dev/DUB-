#!/usr/bin/env python3
"""صنّف فروق تفريغ الأخذات عن نصّها: ضجيج إملائي أم استبدال معجمي حقيقي؟

الدرس من الفحص الشامل: نسبة التشابه وحدها **لا تصلح بوابة**، لأن مُفرِّغ الكلام
يبدّل حروفًا متقاربة (ذ↔د، ت↔ط، س↔ص، ا↔ن) ويدمج/يفصل الكلمات بلا أن يكون خطأ نطق.
الصالح للمراجعة هو **الاستبدال المعجمي**: كلمة حقيقية مختلفة تحلّ محلّ كلمة النص —
وخاصة حين تكون من فئة المتجانسات (انظر guard_homonyms.py).

    python scripts/classify_pron_diff.py work/hajj-dream-2108415 \
        --asr qa/asr-takes --json qa/pron-classification.json

التصنيف: single_letter (ضجيج) · lexical (يُراجع) · insert · delete · diacritic.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from pathlib import Path
from transcribe_takes import source_matches

AR = re.compile(r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]')


def nw(w: str) -> str:
    w = AR.sub('', unicodedata.normalize('NFC', w))
    for a, b in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ى', 'ي'), ('ة', 'ه'), ('ؤ', 'و'), ('ئ', 'ي')):
        w = w.replace(a, b)
    # علامات الترقيم العربية (060C/061B/061F/066A-066D/06D4) ليست حروفًا
    w = re.sub(r'[\u060c\u061b\u061f\u066a-\u066d\u06d4]', '', w)
    return re.sub(r'[^\w\u0621-\u064a\u0660-\u0669]', '', w)


def words(text: str) -> list[str]:
    return [nw(x) for x in re.split(r'\s+', text.strip()) if nw(x)]


def asr_words(path: Path) -> list[str]:
    raw = path.read_bytes().decode('utf-8', 'replace')
    doc = json.loads(raw)
    return words(' '.join(t.get('text', '') for t in doc.get('transcription', [])))


def similar_letters(a: str, b: str) -> bool:
    """هل الفرق كله تبديل حروف متقاربة صوتيًا/رسميًا (ضجيج المُفرِّغ)؟"""
    close = {('ذ', 'د'), ('د', 'ذ'), ('ت', 'ط'), ('ط', 'ت'), ('س', 'ص'), ('ص', 'س'),
             ('ا', 'ن'), ('ن', 'ا'), ('ه', 'ا'), ('ا', 'ه'), ('ي', 'ا'), ('ا', 'ي'),
             ('ة', 'ه'), ('ه', 'ة'), ('ث', 'س'), ('س', 'ث'), ('ق', 'ك'), ('ك', 'ق'),
             ('ب', 'و'), ('و', 'ب'), ('غ', 'ق'), ('ز', 'س'), ('ح', 'خ'), ('خ', 'ح')}
    if len(a) != len(b):
        return False
    return all(x == y or (x, y) in close for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('work', type=Path)
    ap.add_argument('--asr', default='qa/asr-takes')
    ap.add_argument('--json', type=Path, default=None)
    a = ap.parse_args()

    gdoc = json.loads((a.work / 'groups.json').read_text(encoding='utf-8'))
    groups = gdoc['groups'] if isinstance(gdoc, dict) else gdoc
    report, totals = [], {'single_letter': 0, 'lexical': 0, 'insert': 0, 'delete': 0}
    lex_all = []
    stale = []
    for g in groups:
        i = int(g['i'])
        p = a.work / a.asr / f'g{i:03d}.json'
        take_dirs = [a.work / name for name in ('takes-voice25', 'takes')]
        take = next((d / f'g{i:03d}.mp3' for d in take_dirs
                     if (d / f'g{i:03d}.mp3').is_file()), None)
        if not p.exists() or take is None:
            continue
        if not source_matches(p, take):
            stale.append(i)
            continue  # ASR produced from an older or unknown take must not claim a pronunciation error
        want, got = words(g['text']), asr_words(p)
        sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
        row = {'group': i, 'ratio': round(sm.ratio(), 3), 'single_letter': [], 'lexical': [],
               'insert': [], 'delete': []}
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                continue
            if tag == 'insert':
                row['insert'] += got[j1:j2]
                totals['insert'] += j2 - j1
            elif tag == 'delete':
                row['delete'] += want[i1:i2]
                totals['delete'] += i2 - i1
            else:
                pairs = list(zip(want[i1:i2] + ['∅'] * max(0, (j2 - j1) - (i2 - i1)),
                                 got[j1:j2] + ['∅'] * max(0, (i2 - i1) - (j2 - j1))))
                if (i2 - i1) == (j2 - j1) == 1 and similar_letters(*pairs[0]):
                    row['single_letter'].append('↔'.join(pairs[0]))
                    totals['single_letter'] += 1
                else:
                    for x, y in pairs:
                        row['lexical'].append(f'{x}→{y}')
                    totals['lexical'] += len(pairs)
                    lex_all.append({'group': i, 'pairs': [f'{x}→{y}' for x, y in pairs],
                                    'context': ' '.join(want[max(0, i1 - 3):i2 + 3])})
        report.append(row)

    print(f"أخذات ذات بصمة مؤكدة: {len(report)} · تقارير قديمة/غير مثبتة: {len(stale)}")
    if stale:
        print('  غير مثبتة (أُقصيت): ' + ', '.join(f'g{i:03d}' for i in stale))
    print(f"  تهجئة صوتية محتملة (ليست خطأ مثبتًا): {totals['single_letter']}")
    print(f"  استبدال محتمل للمراجعة السمعية (ليس خطأ مثبتًا): {totals['lexical']}")
    print(f"  إدراج: {totals['insert']} · حذف: {totals['delete']}")
    print('\nالاستبدالات المعجمية — للأخذات الأسوأ:')
    for row in sorted(report, key=lambda r: -len(r['lexical']))[:8]:
        if row['lexical']:
            print(f"  g{row['group']:03d} (تشابه {row['ratio']}): " + ' · '.join(row['lexical'][:6]))
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({'totals': totals, 'rows': report, 'review_candidates': lex_all,
                                     'stale_or_unverified': stale,
                                     'warning': 'ASR suggestions only; verify by ear before edits'},
                                     ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'\n  → {a.json}')
    print('\nالقاعدة: الضجيج الإملائي لا يُعالَج. الاستبدال المعجمي يُعرض على المستخدم '
          'بسياقه؛ وإن كان من فئة المتجانسات فيُستبدل ببديل لا يحتمل وجهين قبل التوليد.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
