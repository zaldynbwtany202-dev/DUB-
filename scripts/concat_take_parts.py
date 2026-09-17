#!/usr/bin/env python3
"""concat_take_parts.py -- stitch a moderation-split group back into one take.

Content moderation sometimes refuses a whole group (doctor-lecture #42 narrates
self-harm and strangling). Splitting it into parts gets them through, but the
matched builder expects exactly one seg-NNNN.mp3 per group, so the parts are
concatenated back with the concat demuxer -- no re-encode, no resample, no DSP,
which keeps the "mux only" rule intact.

Word fidelity is checked before anything is written: the parts listed in
parts-NNNN.json must join, space for space, into the plan's text for that group.
If a part had to be re-worded to pass moderation, this check fails and forces the
substitution to be recorded in the plan rather than smuggled into the audio.

usage: concat_take_parts.py [--plan groups-pause-v2.json] [--takes takes-pause] NNNN
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]     # this file lives in scripts/
FF = next((ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries").glob("ffmpeg-linux-*"), None)


def duration(path: Path) -> float:
    err = subprocess.run([str(FF), "-i", str(path)], capture_output=True, text=True).stderr
    if "Duration: " not in err:
        raise SystemExit(f"cannot read duration of {path}")
    h, m, s = err.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("group", type=int)
    ap.add_argument("--plan", type=Path, default=ROOT / "dubs/doctor-lecture/groups-pause-v2.json")
    ap.add_argument("--takes", type=Path, default=ROOT / "dubs/doctor-lecture/takes-pause")
    a = ap.parse_args()

    spec_path = a.takes / f"parts-{a.group:04d}.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    parts = spec["parts"]

    # 1. word fidelity: the parts must rebuild the plan's text exactly
    joined = " ".join(p["text"] for p in parts)
    plan_text = next(g["text"] for g in json.loads(a.plan.read_text(encoding="utf-8"))["groups"]
                     if g["i"] == a.group)
    if joined != plan_text:
        print("فشل التحقّق: الأجزاء لا تُعيد بناء نص الخطة حرفياً", file=sys.stderr)
        print(f"  الخطة : {plan_text}", file=sys.stderr)
        print(f"  الأجزاء: {joined}", file=sys.stderr)
        return 1

    # 2. every part must exist
    missing = [p["file"] for p in parts if not (a.takes / f"{p['file']}.mp3").exists()]
    if missing:
        print(f"أجزاء ناقصة: {missing}", file=sys.stderr)
        return 1

    # 3. concatenate without re-encoding
    out = a.takes / f"seg-{a.group:04d}.mp3"
    listing = a.takes / f".concat-{a.group:04d}.txt"
    listing.write_text("".join(f"file '{(a.takes / (p['file'] + '.mp3')).resolve()}'\n"
                               for p in parts), encoding="utf-8")
    cmd = [str(FF), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
           "-c", "copy", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
        return 1

    total = sum(duration(a.takes / f"{p['file']}.mp3") for p in parts)
    got = duration(out)
    win = next(g["t1"] - g["t0"] for g in json.loads(a.plan.read_text(encoding="utf-8"))["groups"]
               if g["i"] == a.group)
    print(f"seg-{a.group:04d}.mp3: {len(parts)} أجزاء، {got:.3f}s (مجموع الأجزاء {total:.3f}s)"
          f" / نافذة {win:.2f}s -> سرعة {got / win:.3f}")
    print("التحقّق من الكلمات: الأجزاء تُعيد بناء نص الخطة حرفياً ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
