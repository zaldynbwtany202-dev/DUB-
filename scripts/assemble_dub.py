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


# Two time-stretchers with different failure modes. atempo is fast and pitch
# correct, but it does not preserve formants, so at 1.5x the narrator starts to
# sound like a smaller, thinner person -- which is the single most audible thing
# about a sped-up dub. rubberband preserves formants: the voice is the same voice
# reading faster. It costs about three times the CPU and nothing else.
STRETCHERS = {
    "rubberband": "rubberband=tempo={t}:formant=preserved:pitchq=quality"
                  ":transients=crisp:detector=soft:window=long:smoothing=on",
    "atempo": "atempo={t}",
}


def tighten(src, dst, tmpdir, pause_keep=0.25, min_pause=0.32,
            thresh_db=-38.0):
    """Cut the silence out of a take before it is stretched.

    A synthesised take is not continuous speech. Measured across ten takes,
    thirteen and a half percent of the audio is silence -- half a second to two
    seconds at the edges and four to five seconds of pauses inside -- and all of
    it used to be stretched along with the words, because tempo is computed as
    total duration over the window.

    That is the wrong way round twice over. The pauses do not need to be sped up;
    they need to be removed, and removing them costs no quality at all, while
    stretching costs quality on every syllable it touches. And the edge silence
    shifted the speech late inside its own window, because the take is laid down
    at t0 with its leading silence intact.

    So: decode once, find the silent runs, drop the internal ones down to a
    natural pause, trim the edges, and join. What remains is speech, and whatever
    stretch factor is needed after this is a stretch factor applied to words.
    """
    import numpy as np
    import soundfile as sf

    raw = Path(tmpdir) / (Path(src).stem + "-dec.wav")
    run(["-i", str(src), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le",
         "-y", str(raw)])
    x, sr = sf.read(str(raw), always_2d=True)
    mono = x.mean(axis=1)
    hop, win = int(0.01 * sr), int(0.02 * sr)
    frames = max(1, (len(mono) - win) // hop)
    rms = np.array([float(np.sqrt(np.mean(mono[i * hop:i * hop + win] ** 2)))
                    for i in range(frames)])
    silent = rms < 10 ** (thresh_db / 20.0)

    runs, i = [], 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            runs.append((i * hop, min(j * hop + win, len(mono))))
            i = j
        else:
            i += 1

    # Speech intervals: everything that is not an internal pause long enough to
    # be a pause rather than the gap between two syllables of one word.
    speech, pos, limit = [], 0, int(0.02 * sr)
    for a, b in runs:
        if (b - a) >= min_pause * sr and a > limit and b < len(mono) - limit:
            speech.append((pos, a))
            pos = b
    speech.append((pos, len(mono)))

    gap = np.zeros((int(pause_keep * sr), x.shape[1]), dtype=x.dtype)
    parts = []
    for a, b in speech:
        a = max(0, a - limit)
        b = min(len(mono), b + limit)
        if b - a > win:                      # drop slivers shorter than a frame
            parts.append(x[a:b])
    if not parts:
        raise SystemExit(f"التسجيل كله صمت: {src}")
    out = parts[0]
    for p in parts[1:]:
        out = np.concatenate([out, gap, p])

    # Edges last, so the take begins and ends on speech.
    m = out.mean(axis=1)
    loud = np.flatnonzero(np.abs(m) > 10 ** (thresh_db / 20.0))
    if loud.size:
        head = max(0, loud[0] - int(0.04 * sr))
        tail = min(len(out), loud[-1] + int(0.10 * sr))
        out = out[head:tail]
    sf.write(str(dst), out, sr, subtype="PCM_16")
    return dst


def build_group(take_dir, index, names, window, tmpdir, max_tempo, stretch="rubberband",
                tighten_takes=True, pause_keep=0.25):
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

    # Cut the silence before measuring, so the stretch factor is a stretch factor
    # on words. On the ten takes measured, the pauses and edge silence are 13.6%
    # of the audio; removing them takes the required speed-up from 1.563x to
    # about 1.35x, and every bit of that difference is artifact not applied.
    fit_src = tmpdir / f"g{index:03d}-nat.wav"
    if tighten_takes:
        tight = tmpdir / f"g{index:03d}-tight.wav"
        try:
            tighten(fit_src, tight, tmpdir, pause_keep=pause_keep)
            natural = duration(tight)
            fit_src = tight
        except SystemExit as exc:
            print(f"  ! #{index}: {exc} — استخدام التسجيل كما هو", file=sys.stderr)

    tempo = natural / window
    if tempo < 1.0:
        tempo = 1.0  # never slow the locked voice below natural
    if tempo > max_tempo:
        print(f"  ! #{index}: needs {natural / window:.3f}x, clamped to {max_tempo}x "
              f"— will overrun by {natural / max_tempo - window:.2f}s", file=sys.stderr)
        tempo = max_tempo

    out = tmpdir / f"g{index:03d}-fit.wav"
    run(["-i", str(fit_src),
         "-af", STRETCHERS[stretch].format(t=f"{tempo:.6f}"),
         "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)])
    return out, tempo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stretch", choices=sorted(STRETCHERS), default="rubberband",
                    help="time-stretcher; rubberband preserves formants (default)")
    ap.add_argument("--pause-keep", type=float, default=0.25,
                    help="seconds of silence to keep where a pause was cut (default 0.25)")
    ap.add_argument("--no-tighten", action="store_true",
                    help="stretch the take as generated, pauses and all")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--music", default=None, help="separated music bed to mix underneath")
    ap.add_argument("--gain-db", type=float, default=0.0,
                    help="boost applied to the music bed. Zero is the faithful choice: "
                         "the bed is the original music at its original level, so the mix "
                         "reproduces the source balance. Measured on this film, +2 dB "
                         "clips the sum to 0.0 dBFS while +0 lands at -1.2 dBFS.")
    ap.add_argument("--limit", type=float, default=0.95,
                    help="safety ceiling only. It is not a substitute for setting the "
                         "music gain correctly -- alimiter barely moved the peak once the "
                         "sum had already reached full scale.")
    ap.add_argument("--max-tempo", type=float, default=1.80)
    ap.add_argument("--takes", default=None,
                    help="directory of takes; defaults to work/<slug>/takes. A second "
                         "narrator's takes live beside the first rather than replacing "
                         "them, so both can be assembled and compared.")
    ap.add_argument("--pieces", default=None,
                    help='JSON map of group index -> ordered take names, e.g. \'{"5": ["g005a","g005c"]}\'')
    a = ap.parse_args()

    work = Path(a.work_dir)
    take_dir = Path(a.takes) if a.takes else (work / "takes")
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
        fitted, tempo = build_group(take_dir, i, names, g["window"], tmpdir,
                                    a.max_tempo, a.stretch,
                                    not a.no_tighten, a.pause_keep)
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
