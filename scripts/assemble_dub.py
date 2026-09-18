#!/usr/bin/env python
"""Assemble recorded takes into a continuous dub track placed on the original timing.

Reads the group plan, fits each group into its timing window with atempo only, and
lays the results end to end at the times the original narrator spoke them. Optionally
mixes the separated music bed underneath.

A group is either a single take (gNNN.mp3) or an ordered list of pieces that get
trimmed and concatenated first. Piece takes are a fallback for text that moderation
rejects whole; they read slower than one continuous take, so prefer single takes.

Usage:
    python scripts/assemble_dub.py WORK_DIR --out OUT.wav [--start 0] [--end 88]
                                   [--music MUSIC.wav] [--gain-db 2]
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def find_ffmpeg():
    hits = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    if hits:
        return hits[-1]
    return "ffmpeg"


FF = find_ffmpeg()

TRIM = (
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:detection=peak,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:detection=peak,"
    "areverse"
)


def run(args, quiet=True):
    level = ["-v", "error"] if quiet else []
    subprocess.run([FF] + level + args, check=True)


def duration(path):
    """Length in seconds, via a temporary single-channel wav."""
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        run(["-i", str(path), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", "-y", tmp])
        return (os.path.getsize(tmp) - 44) / 2 / 44100
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def build_group(take_dir, index, names, window, tmpdir, max_tempo):
    """Return (path to the fitted wav, reported tempo)."""
    pieces = []
    for name in names:
        src = take_dir / f"{name}.mp3"
        if not src.exists():
            raise SystemExit(f"missing take: {src}")
        pieces.append(src)

    if len(pieces) == 1:
        natural_src = pieces[0]
        natural = duration(natural_src)
        # A lone take keeps its own edges; only pieces need their seams trimmed.
        run(["-i", str(natural_src), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
             "-y", str(tmpdir / f"g{index:03d}-nat.wav")])
    else:
        trimmed = []
        for n, p in enumerate(pieces):
            out = tmpdir / f"g{index:03d}-p{n}-trim.wav"
            run(["-i", str(p), "-af", TRIM, "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(out)])
            trimmed.append(out)
        lst = tmpdir / f"g{index:03d}-list.txt"
        lst.write_text("".join(f"file '{t.resolve()}'\n" for t in trimmed), encoding="utf-8")
        nat = tmpdir / f"g{index:03d}-nat.wav"
        run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "2",
             "-c:a", "pcm_s16le", "-y", str(nat)])
        natural = duration(nat)

    tempo = natural / window
    if tempo < 1.0:
        tempo = 1.0  # never slow the locked voice below natural
    if tempo > max_tempo:
        print(f"  ! #{index}: needs {natural / window:.3f}x, clamped to {max_tempo}x "
              f"— will overrun by {natural / max_tempo - window:.2f}s", file=sys.stderr)
        tempo = max_tempo

    out = tmpdir / f"g{index:03d}-fit.wav"
    run(["-i", str(tmpdir / f"g{index:03d}-nat.wav"), "-af", f"atempo={tempo:.6f}",
         "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)])
    return out, tempo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--music", default=None, help="separated music bed to mix underneath")
    ap.add_argument("--gain-db", type=float, default=2.0, help="boost applied to the music bed")
    ap.add_argument("--limit", type=float, default=0.89,
                    help="output ceiling; 0.89 keeps the peak near -1 dBFS so the "
                         "limiter never has to work hard enough to audibly pump")
    ap.add_argument("--max-tempo", type=float, default=1.80)
    ap.add_argument("--pieces", default=None,
                    help='JSON map of group index -> ordered take names, e.g. \'{"5": ["g005a","g005c"]}\'')
    a = ap.parse_args()

    work = Path(a.work_dir)
    take_dir = work / "takes"
    groups = json.loads((work / "groups.json").read_text(encoding="utf-8"))["groups"]
    pieces_map = json.loads(a.pieces) if a.pieces else {}

    sel = [g for g in groups if a.start <= g["i"] < a.end]
    if not sel:
        raise SystemExit("no groups in range")

    tmpdir = Path(tempfile.mkdtemp(prefix="dub-assemble-"))
    inputs = []
    filters = []
    placed = []

    for g in sel:
        i = g["i"]
        names = pieces_map.get(str(i), [f"g{i:03d}"])
        fitted, tempo = build_group(take_dir, i, names, g["window"], tmpdir, a.max_tempo)
        delay_ms = int(round(g["t0"] * 1000))
        inputs += ["-i", str(fitted)]
        filters.append(f"[{len(placed)}:a]adelay={delay_ms}|{delay_ms}[d{len(placed)}]")
        placed.append(f"[d{len(placed)}]")
        print(f"  #{i:3d}  t0={g['t0']:8.2f}s  window={g['window']:6.2f}s  tempo={tempo:.3f}")

    chain = ";".join(filters)
    n = len(placed)
    total = sel[-1]["t0"] + sel[-1]["window"]
    dub = tmpdir / "dub.wav"
    run(inputs + ["-filter_complex",
                  f"{chain};{''.join(placed)}amix=inputs={n}:normalize=0:duration=longest:"
                  f"dropout_transition=0,atrim=0:{total:.3f},apad,atrim=0:{total:.3f}[a]",
                  "-map", "[a]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(dub)])

    if a.music:
        # amix divides by the input count, so pass normalize=0 and keep the dub's level.
        fc = (f"[1:a]volume={a.gain_db}dB,atrim=0:{total:.3f}[m];"
              f"[0:a][m]amix=inputs=2:normalize=0:duration=first:dropout_transition=0,"
              f"alimiter=limit={a.limit},atrim=0:{total:.3f}[mix]")
        run(["-i", str(dub), "-i", a.music, "-filter_complex", fc, "-map", "[mix]",
             "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", a.out])
    else:
        run(["-i", str(dub), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", a.out])

    print(f"\nwrote {a.out}  ({duration(a.out):.2f}s)")


if __name__ == "__main__":
    main()
