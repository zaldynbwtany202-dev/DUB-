#!/usr/bin/env python3
"""تفريغ أخذات فيلم الوحدة والسعادة (نطاق محدد) لفحص النطق كلمة-كلمة."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/user/DUB-')
WH = ROOT / '.cache/tools/whisper.cpp/build/bin/whisper-cli'
MODEL = ROOT / '.cache/models/whisper-ggml/ggml-small.bin'
TAKES = ROOT / 'work/into-the-wild/takes-voice25'
OUT = ROOT / 'work/into-the-wild/qa/asr-takes'
OUT.mkdir(parents=True, exist_ok=True)

start, end = int(sys.argv[1]), int(sys.argv[2])
for i in range(start, end):
    f = TAKES / f'g{i:03d}.mp3'
    dst = OUT / f'g{i:03d}.json'
    if not f.exists() or dst.exists():
        print(f'[{i}] تخطّي', flush=True)
        continue
    subprocess.run([str(WH), '-m', str(MODEL), '-f', str(f), '-oj', '-of',
                    str(OUT / f'g{i:03d}'), '-l', 'ar'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'[{i}] تمّ', flush=True)
print('انتهى', flush=True)
