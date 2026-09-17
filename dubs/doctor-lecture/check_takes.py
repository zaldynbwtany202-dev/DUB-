#!/usr/bin/env python3
"""check_takes.py -- are the recorded takes usable for matched pacing?

The raw take is *expected* to be longer than its window: matched pacing speeds
it up with atempo until it lands exactly on the original offsets. So "take >
window" is not a defect. The real constraints are

    1.00 <= take_duration / window_duration <= CEILING

below 1.0 the take would have to be slowed down (forbidden), above the ceiling
it would sound rushed.

usage: check_takes.py [groups-plan.json] [takes-dir] [first] [last]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = next((ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries").glob("ffmpeg-linux-*"), None)
CEILING = 1.60


def duration(path: Path) -> float:
    err = subprocess.run([str(FF), "-i", str(path)], capture_output=True, text=True).stderr
    if "Duration: " not in err:
        raise SystemExit(f"cannot read duration of {path}")
    h, m, s = err.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def main() -> int:
    plan = Path(sys.argv[1] if len(sys.argv) > 1 else "dubs/doctor-lecture/groups-pause-v2.json")
    takes = Path(sys.argv[2] if len(sys.argv) > 2 else "dubs/doctor-lecture/takes-pause")
    lo = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    hi = int(sys.argv[4]) if len(sys.argv) > 4 else 10 ** 6
    groups = json.loads(plan.read_text(encoding="utf-8"))["groups"]

    bad, tempos, missing = [], [], []
    for g in groups:
        i = g["i"]
        if not lo <= i <= hi:
            continue
        p = takes / f"seg-{i:04d}.mp3"
        if not p.exists():
            missing.append(i)
            continue
        win = g["t1"] - g["t0"]
        take = duration(p)
        tempo = take / win
        tempos.append(tempo)
        flag = "" if 1.0 <= tempo <= CEILING else "  <-- خارج الحد"
        if flag:
            bad.append((i, round(tempo, 3)))
        print(f"  seg-{i:04d}: {take:7.3f}s / نافذة {win:6.2f}s -> سرعة {tempo:.3f} "
              f"({g['chars'] / take:5.2f} حرف/ث){flag}")

    if tempos:
        print(f"\nمسجّل: {len(tempos)}  ناقص: {missing or 'لا شيء'}  أقصي سرعة: {max(tempos):.3f}")
    else:
        print("\nلا تسجيلات")
    print("مخالفات:", bad or "لا شيء ✓")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
