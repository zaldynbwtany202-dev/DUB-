#!/usr/bin/env python3
"""فحص نطق «حلم الحج» مع إعادة تفريغ الأخذات المتغيرة بدل الثقة باسم gNNN."""
import argparse
from pathlib import Path
from transcribe_takes import ROOT, transcribe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', type=int, nargs='*', help='أرقام المجموعات المطلوب تحديثها')
    a = ap.parse_args()
    takes = ROOT / 'work/hajj-dream-2108415/takes'
    out = ROOT / 'work/hajj-dream-2108415/qa/asr-takes'
    files = [takes / f'g{i:03d}.mp3' for i in a.only] if a.only is not None else sorted(takes.glob('g*.mp3'))
    for i, path in enumerate(files, 1):
        print(f'[{i}/{len(files)}] {path.stem}: {transcribe(path, out / (path.stem + ".json"))}', flush=True)


if __name__ == '__main__':
    main()
