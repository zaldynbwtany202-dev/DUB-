#!/usr/bin/env python3
"""معدل إلقاء كل أخذة + فحص سلامة النص + قائمة الاختيار (نبرة/سرعة).

لماذا: (1) المحرّك يتفاوت ±12% في معدل الإلقاء بين السحبات، فهو رافعة حقيقية
لتخفيف التسريع؛ (2) أخذة بنص مكتوب من الذاكرة تُكشف فورًا لأن معدلها خارج المتوقع.

    python scripts/report_take_rates.py work/into-the-wild --end 51
    python scripts/report_take_rates.py work/into-the-wild --candidates   # قارن البدائل
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RATE = 9.6          # حرف/ث وسيط المحرّك المقيس
TOL = 0.15          # سماحية فحص السلامة


def med_pitch(path: Path) -> float:
    from measure_voice_pitch import frame_pitch
    x, sr = sf.read(str(path), dtype='float32')
    if x.ndim > 1:
        x = x.mean(axis=1)
    _, f = frame_pitch(x.astype(np.float64), sr)
    f = f[(f > 70) & (f < 300)]
    return float(np.median(f)) if len(f) else float('nan')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('work', type=Path)
    ap.add_argument('--takes', default='takes-voice25')
    ap.add_argument('--end', type=int, default=10 ** 9)
    ap.add_argument('--target', type=float, default=137.2)
    ap.add_argument('--candidates', action='store_true', help='قارن البدائل الموجودة وأوصِ بالأفضل')
    a = ap.parse_args()

    gdoc = json.loads((a.work / 'groups.json').read_text(encoding='utf-8'))
    groups = {int(g['i']): g for g in (gdoc['groups'] if isinstance(gdoc, dict) else gdoc)}
    takes = a.work / a.takes
    rows = []
    print(f"{'مجموعة':>8} {'مدة':>7} {'حرف/ث':>7} {'نبرة':>8} {'انزياح':>7}  ملاحظة")
    for i in sorted(groups):
        if i >= a.end:
            break
        p = takes / f'g{i:03d}.mp3'
        if not p.exists():
            continue
        chars = len(re.sub(r'\s', '', groups[i]['text']))
        d = sf.info(str(p)).duration
        rate = chars / d
        hz = med_pitch(p)
        semis = 12 * np.log2(hz / a.target) if hz == hz else float('nan')
        note = ''
        if abs(d - chars / RATE) > TOL * (chars / RATE):
            note = '⚠ الطول خارج المتوقع — تحقّق من النص'
        rows.append((i, d, rate, hz, semis))
        print(f"g{i:03d}  {d:>7.1f} {rate:>7.2f} {hz:>8.1f} {semis:>+7.2f}  {note}")
    if rows:
        rr = np.array([r[2] for r in rows])
        ss = np.array([abs(r[4]) for r in rows])
        print(f"\nالمعدل: وسيط {np.median(rr):.2f} · مدى {rr.min():.2f}–{rr.max():.2f} حرف/ث")
        slow = [r for r in rows if r[2] < np.median(rr) * 0.97]
        far = [r for r in rows if abs(r[4]) > 1.0]
        print(f"مرشّحات للاستبدال — بطيئة (<{np.median(rr)*0.97:.2f}): "
              + (', '.join(f'g{r[0]:03d}({r[2]:.2f})' for r in slow) or 'لا شيء'))
        print(f"مرشّحات للاستبدال — نبرة بعيدة (>1 نصف نغمة): "
              + (', '.join(f'g{r[0]:03d}({r[4]:+.2f})' for r in far) or 'لا شيء'))
    if a.candidates:
        cd = takes / 'candidates'
        print('\nالبدائل الموجودة:')
        for i in sorted(groups):
            cur = takes / f'g{i:03d}.mp3'
            alts = sorted(cd.glob(f'g{i:03d}.alt*.mp3'))
            if not alts or not cur.exists():
                continue
            chars = len(re.sub(r'\s', '', groups[i]['text']))
            best = None
            line = [f'g{i:03d} الحالي {chars/sf.info(str(cur)).duration:.2f} حرف/ث · {med_pitch(cur):.1f} هرتز']
            for alt in alts:
                r = chars / sf.info(str(alt)).duration
                hz = med_pitch(alt)
                line.append(f'{alt.stem}: {r:.2f} حرف/ث · {hz:.1f} هرتز')
            print('  ' + ' | '.join(line))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
