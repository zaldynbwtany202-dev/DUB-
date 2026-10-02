#!/usr/bin/env python3
"""تفريغ كل أخذة من أخذات فيلم «حلم الحج» كي تُقارن بالنص المقصود.

الغرض: أخطاء النطق التي تُبدّل الكلمة (مثل «قوتهم» تُقرأ «قوّتهم») يظهر أثرها
في التفريغ، لأن مُفرِّغ الكلام يكتب الشكل الذي سمعه. فحص كل الأخذات يكشف ما لم
تُصرّح به الأذن بعد.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/user/DUB-')
WH = ROOT / '.cache/tools/whisper.cpp/build/bin/whisper-cli'
MODEL = ROOT / '.cache/models/whisper-ggml/ggml-small.bin'
TAKES = ROOT / 'work/hajj-dream-2108415/takes'
OUT = ROOT / 'work/hajj-dream-2108415/qa/asr-takes'
OUT.mkdir(parents=True, exist_ok=True)

files = sorted(TAKES.glob('g*.mp3'))
for i, f in enumerate(files, 1):
    dst = OUT / f'{f.stem}.json'
    if dst.exists():
        print(f'[{i}/{len(files)}] {f.stem} موجود', flush=True)
        continue
    subprocess.run([str(WH), '-m', str(MODEL), '-f', str(f), '-oj', '-of',
                    str(OUT / f.stem), '-l', 'ar'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'[{i}/{len(files)}] {f.stem} تمّ', flush=True)
print('انتهى', flush=True)
