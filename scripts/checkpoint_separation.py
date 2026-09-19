#!/usr/bin/env python
"""Commit finished separation segments so a wiped sandbox cannot take them back.

The separation runs about seventy minutes for the Sindbad feature, and the
sandbox wipes without warning: it resets the whole working tree to the first
commit on the branch, taking unpushed commits with it. .cache is untracked, so
anything left there is simply gone. This converts each finished segment to mp3
under a tracked directory and commits it, turning one long run into twenty-one
short ones that survive independently.

The mp3 exemption in .gitignore matters here. Without !work/*/stems/*.mp3 the
blanket *.mp3 rule eats the audio and only index.json is stored -- that happened
three times and cost two hundred and ten minutes of inference.

Usage:
    python scripts/checkpoint_separation.py SLUG [--commit]
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
from pathlib import Path


def find_ffmpeg():
    hits = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    return hits[-1] if hits else "ffmpeg"


FF = find_ffmpeg()
BRANCH = "arena/01a09fa1-dub"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--seg-dir", default=".cache/separation/segmented/segments")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    seg = Path(a.seg_dir)
    out = Path("work") / a.slug / "stems"
    out.mkdir(parents=True, exist_ok=True)
    index = out / "index.json"
    done = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}

    new = []
    for music in sorted(seg.glob("seg-*-music.wav")):
        i = music.name.split("-")[1]
        vocal = seg / f"work-{i}" / "estimated-dialogue.wav"
        vm, mm = out / f"vocal-{i}.mp3", out / f"music-{i}.mp3"
        if not mm.exists() and music.exists():
            subprocess.run([FF, "-v", "error", "-i", str(music), "-c:a", "libmp3lame",
                            "-b:a", "96k", "-y", str(mm)], check=True,
                           capture_output=True)
            new.append(mm.name)
        if not vm.exists() and vocal.exists():
            subprocess.run([FF, "-v", "error", "-i", str(vocal), "-c:a", "libmp3lame",
                            "-b:a", "96k", "-y", str(vm)], check=True,
                           capture_output=True)
            new.append(vm.name)
        if mm.exists():
            done[i] = {"music": mm.name, "vocal": vm.name if vm.exists() else None}

    index.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    total = len(list(seg.glob("seg-*-music.wav")))
    print(f"  محفوظ: {len(done)} مقطع ({total} جاهز من الفصل) · جديد: {len(new)}")
    if new:
        print("  " + ", ".join(sorted(new)[:6]) + ("…" if len(new) > 6 else ""))

    if a.commit and new:
        subprocess.run(["git", "add", "-A", str(out)], check=True)
        r = subprocess.run(["git", "commit", "-q", "-m",
                            f"Checkpoint {len(done)} separated segments for {a.slug}\n\n"
                            f"Converted to mp3 and committed as each segment finishes, because the\n"
                            f"sandbox wipes without warning and the separation needs about seventy\n"
                            f"minutes in total. Committed: {', '.join(sorted(new)[:8])}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            p = subprocess.run(["git", "push", "origin", BRANCH],
                               capture_output=True, text=True)
            print("  ✓ التزام + دفع" if p.returncode == 0 else f"  التزام ✓ · الدفع فشل: {p.stderr[-100:]}")
        else:
            print(f"  لا شيء جديد: {r.stderr[-80:]}")


if __name__ == "__main__":
    main()
