#!/usr/bin/env python3
"""تفريغ أخذات الفيلم مع ربط كل ملف ASR ببصمة الأخذة (لا يتخطّى القديم)."""
import argparse
from transcribe_takes import ROOT, transcribe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('start', type=int)
    ap.add_argument('end', type=int, help='exclusive')
    a = ap.parse_args()
    takes = ROOT / 'work/into-the-wild/takes-voice25'
    out = ROOT / 'work/into-the-wild/qa/asr-takes'
    for i in range(a.start, a.end):
        path = takes / f'g{i:03d}.mp3'
        print(f'g{i:03d}: {transcribe(path, out / f"g{i:03d}.json")}', flush=True)


if __name__ == '__main__':
    main()
