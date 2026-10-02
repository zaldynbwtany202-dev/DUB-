#!/usr/bin/env python3
"""Place a take inside its window by the original's word timings, not by uniform rate.

The ear judges sync word by word, and the original narration does not speak at a
constant rate: it rushes a clause and then rests on the next one. Laying a
uniform take across the whole window makes the start and the end land right and
everything in between drift -- measured at 0.70 s average, 3.12 s at worst, on
this film. That drift is what "المزامنة سيئة" means.

This script keeps the take intact and moves its *parts*: the take is split at the
silences it already contains (no speech is cut, the audio is only parted where it
is quiet), and each part is anchored to the source word it starts at, with the
minimum time-stretch that keeps it inside its target span (atempo again: pitch
untouched, and the voice is the same raw take).

    python scripts/align_by_words.py work/into-the-wild \
        --takes work/into-the-wild/takes-voice25 \
        --timed work/into-the-wild/full-timed.json \
        --out build/voice.wav --placement build/placement.json \
        --start 0 --end 32 --max-tempo 1.8 --min-tempo 0.9
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def silence_splits(x: np.ndarray, sr: int, floor: float = 0.012,
                   min_gap: float = 0.12) -> list[float]:
    """Times (seconds) where the take is quiet long enough to part it safely."""
    hop, win = int(0.01 * sr), int(0.03 * sr)
    rms = np.array([float(np.sqrt((x[i:i + win] ** 2).mean()))
                    for i in range(0, len(x) - win, hop)])
    quiet = rms < floor
    splits, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 0.01 >= min_gap and start > 3 and i < len(quiet) - 3:
                splits.append((start + i) / 2 * 0.01)   # middle of the quiet run
            start = None
    return splits


def words_of(text: str) -> list[str]:
    return [w for w in text.replace('،', ' ').replace('؛', ' ').split() if w]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--takes", type=Path, required=True)
    ap.add_argument("--timed", type=Path, required=True, help="JSON بعناصر words فيها t0/t1")
    ap.add_argument("--groups", type=Path, default=None, help="افتراضي: work/groups.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--placement", type=Path, required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--max-tempo", type=float, default=1.6)
    ap.add_argument("--min-tempo", type=float, default=0.95)
    a = ap.parse_args()

    groups_path = a.groups or (a.work / "groups.json")
    doc = json.loads(groups_path.read_text(encoding="utf-8"))
    groups = [g for g in doc["groups"] if a.start <= int(g["i"]) < a.end]
    timed = json.loads(a.timed.read_text(encoding="utf-8"))
    src_words = [w for w in timed["words"] if w.get("t0") is not None and w.get("t1") is not None]

    tmp = a.out.parent / "align-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    placements = []
    label = 0

    for grp in groups:
        i = int(grp["i"])
        t0, t1 = float(grp["t0"]), float(grp["t1"])
        take = a.takes / f"g{i:03d}.mp3"
        if not take.exists():
            raise SystemExit(f"  ✗ لا توجد أخذة: {take}")
        x, sr = sf.read(str(take), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        n = len(x) / sr

        ws = [w for w in src_words if t0 <= w["t0"] < t1]
        text_words = words_of(grp["text"])
        splits = silence_splits(x, sr)

        bounds = [0.0] + splits + [n]
        segs = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
        segs = [(s, e) for s, e in segs if e - s > 0.08]
        if len(ws) < 4 or len(text_words) < 8 or len(segs) < 2:
            segs = [(0.0, n)]                      # nothing to anchor to; one piece

        total_speech = sum(e - s for s, e in segs)
        M, N = len(ws), max(1, len(text_words))
        placed = []
        cursor = t0
        tempos_used = []
        cum = 0.0
        for k, (s, e) in enumerate(segs):
            d = e - s
            a_k = int(round(N * cum / total_speech)) if total_speech else 0
            cum += d
            j = min(M - 1, max(0, int(round(M * a_k / N)))) if M else 0
            target = ws[j]["t0"] if M else t0
            if k == len(segs) - 1 and M:
                span = max(0.6, ws[-1]["t1"] - target)
            else:
                cum_next = cum
                a_k1 = int(round(N * cum_next / total_speech)) if total_speech else N
                j1 = min(M - 1, max(0, int(round(M * a_k1 / N)))) if M else 0
                span = max(0.6, (ws[j1]["t0"] if M else t1) - target)
            tempo = d / span
            tempo = min(a.max_tempo, max(a.min_tempo, tempo))
            start = max(target, cursor)
            end = start + d / tempo
            placed.append({"seg": k, "from": round(s, 3), "dur": round(d, 3),
                           "target": round(target, 3), "start": round(start, 3),
                           "tempo": round(tempo, 3), "end": round(end, 3)})
            cursor = end
            tempos_used.append(tempo)

            seg_file = tmp / f"g{i:03d}-s{k}.wav"
            if not seg_file.exists():
                run([ffmpeg(), "-y", "-v", "error", "-ss", f"{s:.3f}", "-t", f"{d:.3f}",
                     "-i", str(take), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(seg_file)])
            placed[-1]["file"] = seg_file.name
            placed[-1]["delay_ms"] = int(round(start * 1000))

        g_start = placed[0]["start"]
        g_end = max(p["end"] for p in placed)
        g_file = tmp / f"g{i:03d}-placed.wav"
        parts = []
        for k, seg in enumerate(placed):
            parts.append(f"[{k}:a]atempo={seg['tempo']:.6f},adelay={seg['delay_ms']}|{seg['delay_ms']}[p{k}]")
        mix = ";".join(parts) + ";" + "".join(f"[p{k}]" for k in range(len(placed))) + \
              f"amix=inputs={len(placed)}:normalize=0:duration=longest"
        gin = []
        for seg in placed:
            gin += ["-i", str(tmp / seg["file"])]
        run([ffmpeg(), "-y", "-v", "error", *gin, "-filter_complex", mix,
             "-t", f"{total:.3f}" if False else f"{g_end + 0.5:.3f}",
             "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(g_file)])
        inputs += ["-i", str(g_file)]
        filters.append(f"[{label}:a]adelay=0|0[t{label}]")
        label += 1
        placements.append({
            "group": i, "t0": round(t0, 3), "window": round(t1 - t0, 3),
            "natural": round(total_speech, 3), "tempo": round(max(tempos_used), 3),
            "start": round(g_start, 3), "end": round(g_end, 3),
            "late": round(g_start - t0, 3),
            "clamped": any(t >= a.max_tempo - 1e-6 for t in tempos_used),
            "segments": placed})
        mark = "⚠" if placements[-1]["clamped"] else " "
        print(f"  g{i:03d}  قطع {len(placed)}  {t0:7.2f}→{g_end:7.2f}  تأخير {g_start - t0:+.2f} ث"
              f"  سرعة قصوى {max(tempos_used):.2f}× {mark}")
    total = max(p["end"] for p in placements)
    mix = ";".join(filters) + ";" + "".join(f"[t{k}]" for k in range(label)) + \
        f"amix=inputs={label}:normalize=0:duration=longest"
    run([ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", mix,
         "-t", f"{total:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(a.out)])
    a.placement.write_text(json.dumps(
        {"total": round(total, 3), "pack": True, "max_tempo": a.max_tempo,
         "aligned_by": "word timings of the original narration", "groups": placements},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # How far our words sit from the original's words, as planned.
    errs = []
    for p, grp in zip(placements, groups):
        ws = [w for w in src_words if float(grp["t0"]) <= w["t0"] < float(grp["t1"])]
        if len(ws) < 4:
            continue
        for seg in p["segments"]:
            errs.append(abs(seg["start"] - seg["target"]))
    if errs:
        print(f"  متوسط انزياح القطع عن موضع كلماتها: {sum(errs)/len(errs):.2f} ث · "
              f"أقصاه {max(errs):.2f} ث")
    print(f"  → {a.out}  ({total:.2f} ث)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
