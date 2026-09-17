#!/usr/bin/env python3
"""Build the Das recap dub with the approved `matched` pacing.

Every recorded take is time-fitted with `atempo` (pitch preserving, the only
processing allowed on locked takes) so it lands on the original speech onset of
its group and ends inside that group's window. Nothing is slowed down
(`min_tempo` 1.0), no spoken word is cut, and the original narrator is never
mixed back in: the source audio track is dropped entirely.

Groups without a take yet are simply absent from the timeline; the run stops at
the last contiguous take unless `--allow-gaps` is given. The report states exactly
how much of the source speech is covered, because an uncovered gap is the failure
this project has been burned by before.

    python dubs/das-full/build_matched_dub.py --until-group 9
    python dubs/das-full/build_matched_dub.py --until-time 600 --allow-gaps
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
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.agent_tts import trim_silence  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.stem_split import split_center  # noqa: E402

SR = 24000
TAIL_SAFETY = 0.08      # never touch the next group's onset
DEFAULT_TAKES = [HERE / "takes-v2", HERE / "youtube-v33"]


def decode(path: Path, mono: bool = True) -> np.ndarray:
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(SR),
           "-ac", "1" if mono else "2", "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    audio = np.frombuffer(out, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty decode: {path}")
    return audio.copy()


def atempo(samples: np.ndarray, rate: float) -> np.ndarray:
    if abs(rate - 1.0) < 0.002:
        return samples
    a, b = HERE / "work-in.wav", HERE / "work-out.wav"
    sf.write(a, samples, SR, subtype="PCM_24")
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(a), "-af",
                    f"atempo={rate:.9f}", "-ar", str(SR), "-ac", "1", str(b)],
                   check=True, capture_output=True)
    out, _ = sf.read(b, dtype="float32")
    a.unlink(missing_ok=True)
    b.unlink(missing_ok=True)
    return out.astype(np.float32)


def source_duration(path: Path) -> float:
    """Media duration in seconds, read from ffmpeg's banner (no ffprobe here)."""
    err = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True).stderr
    if "Duration: " not in err:
        raise SystemExit(f"cannot read the duration of {path}")
    h, m, s = err.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def find_take(index: int, dirs: list[Path]) -> Path | None:
    for d in dirs:
        p = d / f"seg-{index:04d}.mp3"
        if p.exists():
            return p
        p = d / f"seg-{index:04d}.wav"
        if p.exists():
            return p
    return None


def srt_stamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--groups", type=Path, default=HERE / "youtube-v33/groups-v2.json")
    p.add_argument("--source", type=Path, default=ROOT / ".cache/das-full/source.mp4")
    p.add_argument("--out-dir", type=Path, default=HERE / "matched")
    p.add_argument("--takes-dir", type=Path, action="append", default=None)
    p.add_argument("--from-group", type=int, default=None,
                   help="first group to place; the video is cut from its onset")
    p.add_argument("--until-group", type=int, default=None)
    p.add_argument("--until-time", type=float, default=None)
    p.add_argument("--max-tempo", type=float, default=1.60)
    p.add_argument("--allow-gaps", action="store_true",
                   help="keep going past a group with no take instead of stopping")
    p.add_argument("--tag", default="das-matched")
    args = p.parse_args()

    if not args.source.exists():
        raise SystemExit(f"missing source video: {args.source}")
    plan = json.loads(args.groups.read_text(encoding="utf-8"))
    groups = plan["groups"]
    take_dirs = args.takes_dir or DEFAULT_TAKES
    args.out_dir.mkdir(parents=True, exist_ok=True)

    selected = []
    for g in groups:
        if args.from_group is not None and g["i"] < args.from_group:
            continue
        if args.until_group is not None and g["i"] > args.until_group:
            break
        if args.until_time is not None and g["t0"] > args.until_time:
            break
        selected.append(g)
    if not selected:
        raise SystemExit("no groups in the requested range")
    # One voice per output: a run of groups is only coherent if its takes share a voice.
    origin = selected[0]["t0"] if args.from_group is not None else 0.0

    placements: list[tuple[float, np.ndarray, dict]] = []
    rows: list[dict] = []
    missing: list[int] = []
    for g in selected:
        take = find_take(g["i"], take_dirs)
        if take is None:
            missing.append(g["i"])
            if not args.allow_gaps:
                selected = [x for x in selected if x["i"] < g["i"]]
                break
            continue
        raw = decode(take)
        samples, silence = trim_silence(raw, SR)
        window = g["t1"] - g["t0"]
        natural = len(samples) / SR
        rate = max(1.0, natural / max(window - TAIL_SAFETY, 0.5))
        if rate > args.max_tempo:
            raise SystemExit(
                f"group {g['i']} needs {rate:.3f}x (cap {args.max_tempo}); "
                "re-record shorter text or widen the window — speech is never truncated")
        fit = atempo(samples, rate)
        placed = len(fit) / SR
        placements.append((g["t0"] - origin, fit, g))
        rows.append({"i": g["i"], "t0": g["t0"], "t1": g["t1"], "window": round(window, 3),
                     "chars": g["chars"], "take": str(take.relative_to(ROOT)),
                     "natural_s": round(natural, 3), "tempo": round(rate, 4),
                     "placed_s": round(placed, 3), "ends_at": round(g["t0"] + placed, 3),
                     "overrun_s": round(max(0.0, g["t0"] + placed - g["t1"]), 3),
                     "leading_silence_removed": round(silence["leading_silence_removed"], 3),
                     "trailing_silence_removed": round(silence["trailing_silence_removed"], 3)})

    if not placements:
        raise SystemExit("no takes found for the selected groups")

    end_time = max(s + len(a) / SR for s, a, _ in placements) + 0.5
    # Never shorten the picture. The narration usually stops before the video
    # does -- an end card, music, a channel outro -- and `-t span` plus
    # `-shortest` would cut those last seconds off the film (the 19:20
    # doctor-lecture came out 19:17.51). Pad the dub track with silence to the
    # full source length instead: the picture stays whole and the tail is quiet.
    src_dur = source_duration(args.source)
    span = max(end_time, src_dur - origin)
    track = np.zeros(int(round(span * SR)), dtype=np.float32)
    for start, audio, _ in placements:
        i = int(round(start * SR))
        track[i:i + len(audio)] += audio
    peak = float(np.max(np.abs(track)) or 1.0)
    if peak > 0.95:
        track *= 0.95 / peak
        gain = round(20 * np.log10(0.95 / peak), 2)
    else:
        gain = 0.0
    wav = args.out_dir / f"{args.tag}.wav"
    sf.write(wav, track, SR, subtype="PCM_24")

    out = args.out_dir / f"final-dub-{args.tag}.mp4"
    seek = ["-ss", f"{origin:.3f}"] if origin > 0 else []
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", *seek, "-i", str(args.source), "-t", f"{span:.3f}",
         "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-metadata:s:a:0", "language=ara", "-shortest", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True)

    srt = args.out_dir / f"final-dub-{args.tag}.srt"
    with srt.open("w", encoding="utf-8") as fh:
        for k, r in enumerate(rows, 1):
            text = next(g["text"] for g in selected if g["i"] == r["i"])
            fh.write(f"{k}\n{srt_stamp(r['t0'] - origin)} --> {srt_stamp(r['ends_at'] - origin)}\n{text}\n\n")

    # ---- verification -------------------------------------------------------
    out_audio = decode(out)
    del track            # 222 MB of float32 we no longer need
    # Read the source stereo in 60 s chunks. Decoding all 19 minutes at once
    # (~445 MB) on top of the dub track and the output decode is what got this
    # step OOM-killed (exit 137) after the mp4 was already written.
    CHUNK = 60 * SR
    proc = subprocess.Popen(
        [ffmpeg_exe(), "-v", "error", *seek, "-i", str(args.source), "-t", f"{span:.3f}",
         "-ar", str(SR), "-ac", "2", "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    dot = na = nb = 0.0
    pos = 0
    while pos < len(out_audio):
        raw = proc.stdout.read(2 * CHUNK * 4)
        if not raw:
            break
        st = np.frombuffer(raw[: len(raw) // 8 * 8], dtype=np.float32)
        left, right = st[0::2], st[1::2]
        voice, _ = split_center(left, right, SR, strength=1.8)
        m = min(len(voice), len(out_audio) - pos)
        if m <= 0:
            break
        a = out_audio[pos:pos + m]
        b = voice[:m]
        a = a - a.mean()
        b = b - b.mean()
        dot += float(np.dot(a, b))
        na += float(np.dot(a, a))
        nb += float(np.dot(b, b))
        pos += m
    if proc.stdout:
        proc.stdout.close()
    proc.wait()
    corr = float(dot / (np.sqrt(na * nb) + 1e-12))
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(out)],
                             capture_output=True, text=True).stderr
    n_audio = streams.count("Audio:")

    horizon = selected[-1]["t1"]
    covered = sum(min(r["ends_at"], r["t1"]) - r["t0"] for r in rows if r["ends_at"] > r["t0"])
    report = {
        "tag": args.tag, "voice_id": plan.get("voice_id"), "pacing": plan.get("processing"),
        "groups_from": selected[0]["i"], "groups_to": selected[-1]["i"],
        "source_window_s": [round(origin, 3), round(origin + span, 3)],
        "output": str(out.relative_to(ROOT)), "srt": str(srt.relative_to(ROOT)),
        "bytes": out.stat().st_size, "audio_seconds": round(len(out_audio) / SR, 3),
        "video_seconds": round(end_time, 3), "audio_streams": n_audio,
        "corr_original_vocals": round(corr, 4),
        "original_narrator_in_mix": abs(corr) > 0.08,
        "peak_gain_db": gain,
        "groups_selected": len(selected), "groups_placed": len(rows),
        "groups_missing_takes": missing,
        "source_speech_covered_s": round(covered, 2),
        "source_speech_in_scope_s": round(horizon - selected[0]["t0"], 2),
        "coverage_ratio": round(covered / max(horizon - selected[0]["t0"], 1e-6), 4),
        "tempo_min": min(r["tempo"] for r in rows), "tempo_max": max(r["tempo"] for r in rows),
        "max_overrun_s": max(r["overrun_s"] for r in rows),
        "takes": rows,
    }
    (args.out_dir / f"final-dub-{args.tag}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wav.unlink(missing_ok=True)

    if n_audio != 1:
        raise SystemExit(f"{n_audio} audio streams in the output")
    if abs(corr) > 0.08:
        raise SystemExit(f"original narrator still audible: corr={corr:.3f}")
    summary = {k: report[k] for k in ("output", "bytes", "video_seconds", "groups_placed",
                                      "groups_missing_takes", "coverage_ratio", "tempo_min",
                                      "tempo_max", "max_overrun_s", "corr_original_vocals",
                                      "audio_streams")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
