#!/usr/bin/env python
"""Split each take into a few long pieces and give each one its own tempo.

Group length decides the sync error. Measured per word on the finished dub:
groups under 20 s land at a median 0.14 s with p90 0.51 s, groups of 25-30 s at
0.37 s and p90 4.64 s. One atempo per group can only place the first and last
word of it, so everything between drifts.

Splitting a take into many pieces does not help, and the word counts say why.
Thirteen or fourteen pieces per group -- roughly 1.5 s each -- stop being
speech: whisper recovers 46 words from six minutes of the phrase-level build
against 377 from the unretimed one. The pauses between phrases are what carries
the rhythm, and giving each a different tempo destroys them.

Two or three pieces is a different regime: 8-12 s each, which the measurement
says tracks well, with only one or two cuts per group, placed at the deepest
silence so they land between phrases rather than inside words.

Usage:
    python scripts/retime_splits.py WORK --timings T.json --plan P.json --out OUT.wav
                                      [--splits 2] [--start 0] [--end 14]
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

FF = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))[-1]


def run(args):
    subprocess.run([FF, "-v", "error"] + args, check=True)


def duration(path):
    import os
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        run(["-i", str(path), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", "-y", tmp])
        return (os.path.getsize(tmp) - 44) / 2 / 44100
    finally:
        p = Path(tmp)
        if p.exists():
            p.unlink()


def envelope(path, hop=0.01):
    tmp = Path(tempfile.mkdtemp(prefix="splits-")) / "a.wav"
    run(["-i", str(path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(tmp)])
    x = np.fromfile(tmp, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(hop * 16000)
    return np.array([np.sqrt(np.mean(x[i * n:(i + 1) * n] ** 2) + 1e-12) for i in range(len(x) // n)])


def best_cuts(path, ncuts, min_gap_s=0.08):
    """Return the ncuts quietest stretches, each at least min_gap_s long."""
    e = envelope(path)
    thr = np.percentile(e, 12) * 1.6 + 1e-4
    quiet = e < thr
    gaps, i, min_f = [], 0, max(int(min_gap_s / 0.01), 1)
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if j - i >= min_f:
                # depth = how far below threshold the quietest frame sits
                gaps.append(((i + j) / 2 * 0.01, float(thr - e[i:j].min())))
            i = j
        else:
            i += 1
    gaps.sort(key=lambda t: -t[1])
    return sorted(t for t, _ in gaps[:ncuts])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--timings", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--splits", type=int, default=2, help="pieces per group")
    ap.add_argument("--max-ratio", type=float, default=2.2)
    ap.add_argument("--min-ratio", type=float, default=0.6)
    ap.add_argument("--pieces", default=None)
    a = ap.parse_args()

    work = Path(a.work_dir)
    words = json.loads(Path(a.timings).read_bytes().decode("utf-8", "surrogateescape"))["words"]
    plan = Path(a.plan) if a.plan else (work / "groups-vocal.json")
    groups = json.loads(plan.read_text(encoding="utf-8"))["groups"]
    pieces_map = json.loads(a.pieces) if a.pieces else {}

    sel = [g for g in groups if a.start <= g["i"] < a.end]
    tmpdir = Path(tempfile.mkdtemp(prefix="dub-splits-"))

    ptr, spans = 0, {}
    for g in groups:
        spans[g["i"]] = (ptr, ptr + g["words_n"])
        ptr += g["words_n"]

    inputs, filters, placed = [], [], []
    for g in sel:
        i = g["i"]
        a0, a1 = spans[i]
        target = [w for w in words[a0:a1]]
        t0g = target[0]["t0"]
        names = pieces_map.get(str(i), [f"g{i:03d}"])
        nat = tmpdir / f"g{i:03d}-nat.wav"
        if len(names) == 1:
            run(["-i", str(work / "takes" / f"{names[0]}.mp3"), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(nat)])
        else:
            ps = []
            for k, nm in enumerate(names):
                p = tmpdir / f"g{i:03d}-p{k}.wav"
                run(["-i", str(work / "takes" / f"{nm}.mp3"), "-ar", "44100", "-ac", "2",
                     "-c:a", "pcm_s16le", "-y", str(p)])
                ps.append(p)
            lst = tmpdir / f"g{i:03d}.txt"
            lst.write_text("".join(f"file '{x.resolve()}'\n" for x in ps), encoding="utf-8")
            run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(nat)])

        nat_len = duration(nat)
        cuts = [0.0] + best_cuts(nat, a.splits - 1) + [nat_len]
        if cuts[-1] <= cuts[-2]:
            cuts = cuts[:-1] + [nat_len]

        # split the word list in proportion to the piece lengths, so each piece
        # carries the words it actually speaks
        lens = [cuts[k + 1] - cuts[k] for k in range(len(cuts) - 1)]
        tot = sum(lens) or 1.0
        chains, labels, at = [], [], 0
        for k, L in enumerate(lens):
            nw = max(1, round(len(target) * L / tot))
            seg = target[at:at + nw] if k < len(lens) - 1 else target[at:]
            at += nw
            if not seg:
                continue
            s, e = cuts[k], cuts[k + 1]
            s = max(0.0, min(s, max(nat_len - 0.05, 0.0)))
            e = max(s + 0.1, min(e, nat_len))
            want = seg[-1]["t1"] - seg[0]["t0"]
            ratio = max(want, 1e-3) / (e - s)
            ratio = min(max(ratio, a.min_ratio), a.max_ratio)
            delay = seg[0]["t0"] - t0g
            chains.append(
                f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS,"
                f"atempo={ratio:.5f},"
                f"adelay={int(round(delay*1000))}|{int(round(delay*1000))}[w{k}]")
            labels.append(f"[w{k}]")

        fitted = tmpdir / f"g{i:03d}-fit.wav"
        run(["-i", str(nat), "-filter_complex",
             ";".join(chains) + f";{''.join(labels)}amix=inputs={len(labels)}:"
             f"normalize=0:duration=longest:dropout_transition=0,"
             f"atrim=0:{g['window']:.3f}[o]",
             "-map", "[o]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(fitted)])
        print(f"  #{i:3d}  {len(labels)} قطعة · نافذة {g['window']:.1f}ث · طبيعي {nat_len:.1f}ث")

        d = int(round(g["t0"] * 1000))
        inputs += ["-i", str(fitted)]
        filters.append(f"[{len(placed)}:a]adelay={d}|{d}[d{len(placed)}]")
        placed.append(f"[d{len(placed)}]")

    total = sel[-1]["t1"]
    subprocess.run([FF, "-v", "error"] + inputs + ["-filter_complex",
                   ";".join(filters) + f";{''.join(placed)}amix=inputs={len(placed)}:"
                   f"normalize=0:duration=longest:dropout_transition=0,atrim=0:{total:.3f}[o]",
                   "-map", "[o]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
                   "-y", a.out], check=True)
    print(f"\nwrote {a.out} ({duration(a.out):.2f}s)")


if __name__ == "__main__":
    main()
