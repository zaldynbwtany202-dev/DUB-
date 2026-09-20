#!/usr/bin/env python
"""Measure sync by transcribing the dub and comparing word times to the targets.

Envelope correlation was the obvious metric and it does not work. It compares a
log-energy envelope of the dub against the source over 10 s windows, and every
candidate lands in a 0.34-0.41 band: it cannot tell a build that clamps 55
stretch ratios from one that clamps 459, and worse, it falls as processing
increases (0.41 unretimed, 0.36 word by word), so it penalises exactly the
candidates it is meant to be judging.

Ask the question directly instead. Transcribe the finished dub -- the words are
known and it is clean speech with no music bed -- and compare each recognised
word's start time against the time the script says it should start. That offset
is the sync error, in seconds, per word, with no envelope in the way.

The word count itself is the second half of the measurement. Whisper recovers
377 words from six minutes of the unretimed build and 46 from the phrase-level
one; that gap is not a timing problem, it is the dub having stopped being
intelligible speech, and no offset statistic would have shown it.

Usage:
    python scripts/measure_dub_sync.py DUB_WAV SLUG [--seconds 120]
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from scripts.align_vocal_timings import align, norm  # noqa: E402
from scripts.transcribe_vocals import words_from_whisper  # noqa: E402

WHISPER = ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ".cache/models/whisper-ggml/ggml-small.bin"


def find_ffmpeg():
    hits = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    return hits[-1] if hits else "ffmpeg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dub")
    ap.add_argument("slug")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--timings", default=None)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()

    work = Path("work") / a.slug
    tp = Path(a.timings) if a.timings else (work / "FvWDRz8WP5s.vocal-timed.json")
    targets = [w for w in json.loads(tp.read_bytes().decode("utf-8", "surrogateescape"))["words"]
               if w["t0"] < a.seconds]

    tmp = Path(tempfile.mkdtemp(prefix="dubsync-"))
    wav = tmp / "dub.wav"
    subprocess.run([find_ffmpeg(), "-v", "error", "-i", a.dub, "-t", str(a.seconds),
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(wav)],
                   check=True, capture_output=True)
    subprocess.run([WHISPER, "-m", MODEL, "-f", str(wav), "-l", "ar",
                    "-t", str(a.threads), "-ojf", "-sow", "-ml", "160",
                    "-of", str(tmp / "asr")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = words_from_whisper(str(tmp / "asr.json"))

    src = [(norm(w["w"]), w["t0"], w["t1"]) for w in got]
    dst = [(norm(w["w"]), 0.0, 0.0) for w in targets]
    pairs = align(src, dst)

    offs = sorted((got[i]["t0"] - targets[j]["t0"], j, i) for j, i, _ in pairs)
    if not offs:
        print("  لا تطابقات — التعرّف فشل")
        return 1
    d = [o for o, _, _ in offs]
    n = len(d)
    ad = sorted(abs(x) for x in d)

    def pct(p):
        return ad[min(int(p * n), n - 1)]

    print(f"  {Path(a.dub).name}  ({a.seconds}ث)")
    print(f"    كلمات معرَّفة {len(got)} · مطابقة {n} من {len(targets)} "
          f"({100*n/max(len(targets),1):.0f}%)")
    print(f"    وسيط الإزاحة {st.median(d):+.2f}ث · الانحراف {st.pstdev(d):.2f}ث")
    print(f"    |إزاحة| وسيط {st.median(ad):.2f}ث · p75 {pct(.75):.2f}ث · "
          f"p90 {pct(.90):.2f}ث · p95 {pct(.95):.2f}ث")
    print(f"    ضمن 0.3ث: {100*sum(1 for x in ad if x<0.3)/n:.0f}% · "
          f"ضمن 0.5ث: {100*sum(1 for x in ad if x<0.5)/n:.0f}% · "
          f"أكثر من 1ث: {100*sum(1 for x in ad if x>1)/n:.0f}%")
    h = n // 2
    if h > 20:
        print(f"    النصف الأول {st.median(d[:h]):+.2f}ث · الثاني {st.median(d[h:]):+.2f}ث")
    return 0


if __name__ == "__main__":
    sys.exit(main())
