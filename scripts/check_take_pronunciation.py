#!/usr/bin/env python3
"""قارن نصّ كل أخذة بما سمعه مُفرِّغ الكلام منها — لكشف أخطاء النطق.

خطأ النطق الذي يبدّل الكلمة (مثل «قوتهم» → «قوّتهم») يظهر في التفريغ كتابةً
مختلفة، لأن المُفرِّغ يكتب ما سمعه. تُطبَّع الحروف أولًا (همزات/ألف/ياء/تشكيل)
حتى لا تُلوّث الفروق الإملائية كانونيةً، ثم تُطبع الفروق الحقيقية وحدها.

    python scripts/check_take_pronunciation.py work/hajj-dream-2108415
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from pathlib import Path

AR_DIACRITICS = re.compile(r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]')


def norm_word(w: str) -> str:
    w = unicodedata.normalize('NFC', w)
    w = AR_DIACRITICS.sub('', w)
    w = w.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
    w = w.replace('ى', 'ي').replace('ة', 'ه').replace('ؤ', 'و').replace('ئ', 'ي')
    w = re.sub(r'[^\w\u0600-\u06ff]', '', w)
    return w


def words_of(text: str) -> list[str]:
    return [norm_word(w) for w in re.split(r'\s+', text.strip()) if norm_word(w)]


def asr_text(path: Path) -> str:
    raw = path.read_bytes().decode('utf-8', 'replace')
    doc = json.loads(raw)
    return ' '.join(t.get('text', '') for t in doc.get('transcription', [])).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('work', type=Path)
    ap.add_argument('--asr', default='qa/asr-takes')
    a = ap.parse_args()

    gdoc = json.loads((a.work / 'groups.json').read_text(encoding='utf-8'))
    print(f"{'مجموعة':>8} {'تشابه':>6}  الفروق (النص المقصود → ما سمعه المُفرِّغ)")
    flagged = 0
    for g in gdoc['groups']:
        i = int(g['i'])
        p = a.work / a.asr / f'g{i:03d}.json'
        if not p.exists():
            continue
        want = words_of(g['text'])
        got = words_of(asr_text(p))
        sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
        ratio = sm.ratio()
        diffs = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                continue
            diffs.append(f'{" ".join(want[i1:i2]) or "∅"} → {" ".join(got[j1:j2]) or "∅"}')
        if diffs:
            flagged += 1
            print(f'g{i:03d}   {ratio:6.3f}  ' + ' · '.join(diffs[:6]))
        else:
            print(f'g{i:03d}   {ratio:6.3f}  —')
    print(f'\nمجموعات فيها فروق: {flagged} من {len(gdoc["groups"])}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
