#!/usr/bin/env python3
"""Split a video into ~N-second chunks at silence-friendly boundaries.

Usage: python scripts/split_video.py input.mp4 out_dir chunk_seconds
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def probe_duration(path: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True)
    return float(out.strip())


def find_silences(audio: str, min_silence: float = 0.6, thr: str = "-32dB") -> list[float]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", audio,
         "-af", f"silencedetect=noise={thr}:d={min_silence}",
         "-f", "null", "-"], capture_output=True, text=True)
    ts = []
    for line in r.stderr.splitlines():
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m:
            ts.append(float(m.group(1)))
    return ts


def choose_cuts(duration: float, silences: list[float], target: float) -> list[float]:
    """Pick cut points around each multiple of ``target``, snapping to the
    nearest silence within ±25 s so no line is split mid-word."""
    cuts = [0.0]
    goal = target
    while goal < duration - 3:
        window = [s for s in silences if abs(s - goal) <= 25 and s > cuts[-1] + target * 0.4]
        cut = min(window, key=lambda s: abs(s - goal)) if window else goal
        if cut - cuts[-1] < target * 0.4:
            goal += target
            continue
        cuts.append(cut)
        goal = cut + target
    cuts.append(duration)
    return cuts


def main() -> None:
    src = sys.argv[1]
    out = Path(sys.argv[2])
    target = float(sys.argv[3])
    out.mkdir(exist_ok=True, parents=True)

    duration = probe_duration(src)
    print(f"duration: {duration:.1f} s   target chunk: {target:.0f} s")

    audio_tmp = str(out / "_probe.wav")
    subprocess.check_call(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1",
         "-ar", "16000", audio_tmp])
    silences = find_silences(audio_tmp)
    os.remove(audio_tmp)
    print(f"found {len(silences)} silence boundaries")

    cuts = choose_cuts(duration, silences, target)
    print("cut points:", [round(c, 2) for c in cuts])

    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        outp = out / f"part_{i:03d}.mp4"
        # re-encode: keeps timing precise even at non-keyframe cuts
        subprocess.check_call(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "160k", str(outp)])
        print(f"  part_{i:03d}: {a:6.2f} → {b:6.2f}   {outp}")


if __name__ == "__main__":
    main()
