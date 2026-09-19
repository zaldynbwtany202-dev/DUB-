#!/usr/bin/env python
"""Retime recorded takes word by word so the dub tracks the original's local rate.

The group-level assembler gives every group a single atempo, which can only make
the group's start and end land on time. Inside a 25 s group the dub drifts freely:
measured against the source, the offset swings between -3 s and +2.5 s and the
envelope correlation sits near 0.2. Fitting each word to its own slot in the
original timing removes that drift, because the drift is exactly what a single
ratio cannot express.

Take-side word boundaries are estimated, not measured: a synthetic voice reads at
a fairly steady rate, so splitting its duration by character count is a good
first approximation. scripts/align_agent_takes.py can measure them properly with
whisper.cpp DTW when the estimate proves insufficient.

Usage:
    python scripts/retime_words.py WORK_DIR --out OUT.wav [--start 0] [--end 88]
                                   [--max-ratio 2.2] [--min-ratio 0.6]
"""
import argparse
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def find_ffmpeg():
    hits = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    return hits[-1] if hits else "ffmpeg"


FF = find_ffmpeg()


def run(args):
    subprocess.run([FF, "-v", "error"] + args, check=True)


def duration(path):
    """Seconds of audio, via a throwaway mono wav."""
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        run(["-i", str(path), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", "-y", tmp])
        import os
        return (os.path.getsize(tmp) - 44) / 2 / 44100
    finally:
        p = Path(tmp)
        if p.exists():
            p.unlink()


TRIM = (
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:detection=peak,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:detection=peak,"
    "areverse"
)


def group_take(work, index, names, tmpdir):
    """Concatenate the group's takes into one trimmed wav; return its path."""
    if len(names) == 1:
        src = work / "takes" / f"{names[0]}.mp3"
        if not src.exists():
            raise SystemExit(f"missing take: {src}")
        out = tmpdir / f"g{index:03d}-raw.wav"
        run(["-i", str(src), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)])
        trimmed = tmpdir / f"g{index:03d}-nat.wav"
        run(["-i", str(out), "-af", TRIM, "-ar", "44100", "-ac", "2",
             "-c:a", "pcm_s16le", "-y", str(trimmed)])
        return trimmed
    trimmed = []
    for n, name in enumerate(names):
        src = work / "takes" / f"{name}.mp3"
        if not src.exists():
            raise SystemExit(f"missing take: {src}")
        out = tmpdir / f"g{index:03d}-p{n}.wav"
        run(["-i", str(src), "-af", TRIM, "-ar", "44100", "-ac", "2",
             "-c:a", "pcm_s16le", "-y", str(out)])
        trimmed.append(out)
    lst = tmpdir / f"g{index:03d}-list.txt"
    lst.write_text("".join(f"file '{t.resolve()}'\n" for t in trimmed), encoding="utf-8")
    nat = tmpdir / f"g{index:03d}-nat.wav"
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "2",
         "-c:a", "pcm_s16le", "-y", str(nat)])
    return nat


def split_by_chars(total, words, texts):
    """Start/end of each word inside the take, proportional to character count.

    The +1 gives every word a share of the boundary, since a real take spends
    time between words as well as on them.
    """
    weights = [max(1.0, len(t) + 1.0) for t in texts]
    s = sum(weights)
    bounds, at = [], 0.0
    for w in weights:
        span = total * w / s
        bounds.append((at, at + span))
        at += span
    return bounds


def retime_group(nat, bounds, targets, tmpdir, index, min_ratio, max_ratio):
    """Stretch each word to its slot; return the group wav."""
    chains, labels, clamped = [], [], 0
    for n, ((s, e), (p, q)) in enumerate(zip(bounds, targets)):
        take_len = e - s
        want = q - p
        ratio = want / take_len if take_len > 1e-4 else 1.0
        if ratio < min_ratio:
            ratio, clamped = min_ratio, clamped + 1
        elif ratio > max_ratio:
            ratio, clamped = max_ratio, clamped + 1
        chains.append(
            f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS,"
            f"atempo={ratio:.5f},adelay={int(round(p * 1000))}|{int(round(p * 1000))}[w{n}]"
        )
        labels.append(f"[w{n}]")
    n = len(labels)
    out = tmpdir / f"g{index:03d}-fit.wav"
    span = targets[-1][1]
    run(["-i", str(nat), "-filter_complex",
         ";".join(chains) + f";{''.join(labels)}amix=inputs={n}:normalize=0:"
         f"duration=longest:dropout_transition=0,atrim=0:{span:.4f},apad,"
         f"atrim=0:{span:.4f}[a]",
         "-map", "[a]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)])
    return out, clamped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--max-ratio", type=float, default=2.2)
    ap.add_argument("--min-ratio", type=float, default=0.6)
    ap.add_argument("--pieces", default=None,
                    help='JSON map of group index -> ordered take names')
    a = ap.parse_args()

    work = Path(a.work_dir)
    words = json.loads((work / "FvWDRz8WP5s.spoken-capped.json").read_text(encoding="utf-8"))["words"]
    groups = json.loads((work / "groups.json").read_text(encoding="utf-8"))["groups"]
    pieces_map = json.loads(a.pieces) if a.pieces else {}

    sel = [g for g in groups if a.start <= g["i"] < a.end]
    if not sel:
        raise SystemExit("no groups in range")

    tmpdir = Path(tempfile.mkdtemp(prefix="dub-reword-"))

    # Walk the word list in order; group i owns words[ptr : ptr + words_n].
    ptr, spans = 0, {}
    for g in groups:
        spans[g["i"]] = (ptr, ptr + g["words_n"])
        ptr += g["words_n"]

    inputs, filters, placed, total_clamped = [], [], [], 0
    for g in sel:
        i = g["i"]
        a0, a1 = spans[i]
        wseg = words[a0:a1]
        texts = [w["w"] for w in wseg]
        names = pieces_map.get(str(i), [f"g{i:03d}"])
        nat = group_take(work, i, names, tmpdir)
        nat_len = duration(nat)

        if len(texts) != len(g["text"].split()):
            # Word count drifted from the timing list; fall back to the group's
            # own text spread evenly, so the group still ends on time.
            texts = g["text"].split()
            n = len(texts)
            bounds = [(nat_len * k / n, nat_len * (k + 1) / n) for k in range(n)]
            step = g["window"] / n
            targets = [(k * step, (k + 1) * step) for k in range(n)]
            note = " (نص بديل)"
        else:
            bounds = split_by_chars(nat_len, wseg, texts)
            targets = [(w["t0"] - g["t0"], w["t1"] - g["t0"]) for w in wseg]
            note = ""

        fitted, clamped = retime_group(nat, bounds, targets, tmpdir, i,
                                       a.min_ratio, a.max_ratio)
        total_clamped += clamped
        delay = int(round(g["t0"] * 1000))
        inputs += ["-i", str(fitted)]
        filters.append(f"[{len(placed)}:a]adelay={delay}|{delay}[d{len(placed)}]")
        placed.append(f"[d{len(placed)}]")
        print(f"  #{i:3d}  {len(texts):3d} كلمة  t0={g['t0']:8.2f}s  "
              f"مقيَّد={clamped}{note}")

    total = sel[-1]["t0"] + sel[-1]["window"]
    run(inputs + ["-filter_complex",
                  ";".join(filters) + f";{''.join(placed)}amix=inputs={len(placed)}:"
                  f"normalize=0:duration=longest:dropout_transition=0,"
                  f"atrim=0:{total:.3f},apad,atrim=0:{total:.3f}[a]",
                  "-map", "[a]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", a.out])
    print(f"\nwrote {a.out} ({duration(a.out):.2f}s)  كلمات مقيَّدة: {total_clamped}")


if __name__ == "__main__":
    main()
