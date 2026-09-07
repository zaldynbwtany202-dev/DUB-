#!/usr/bin/env python3
"""Estimate original music/effects with a neural model; no voice generation."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.neural_background import separate_background


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--work-dir", type=Path, default=Path(".cache/separation/work"))
    p.add_argument("--mask-power", type=float, default=1.35)
    args = p.parse_args()
    print(json.dumps(separate_background(args.source, args.output, args.work_dir, power=args.mask_power), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
