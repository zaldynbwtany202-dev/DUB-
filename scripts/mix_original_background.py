#!/usr/bin/env python3
"""Add an estimated original music stem to an approved dub; never regenerate speech."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.agent_tts import probe_duration
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def mix_graph(duration: float, gain_db: float) -> str:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Need a finite positive duration")
    if not math.isfinite(gain_db) or not -30 <= gain_db <= 12:
        raise ValueError("Background gain must be between -30 and +12 dB")
    return (
        f"[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.6f},atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,asplit=2[voice][side];"
        f"[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.6f},atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={min(0.2, duration / 4):.6f},"
        f"afade=t=out:st={max(0, duration - 0.35):.6f}:d={min(0.35, duration):.6f},volume={gain_db:.3f}dB[bed];"
        "[bed][side]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=500:knee=2:detection=rms:link=average[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json,aresample=48000[out]"
    )


def mix(voice_video: Path, background: Path, output: Path, work: Path, *, gain_db: float = 2.5) -> dict:
    for path in (voice_video, background):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
        if path.resolve() == output.resolve():
            raise ValueError("Do not overwrite an approved input")
    duration = probe_duration(voice_video)
    background_duration = probe_duration(background)
    if abs(duration - background_duration) > 0.2:
        raise ValueError("Background and approved dub timelines do not match")
    graph = mix_graph(duration, gain_db)
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    master = work / "mixed-master.wav"
    result = subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-i", str(voice_video), "-i", str(background),
        "-filter_complex", graph, "-map", "[out]", "-t", f"{duration:.6f}",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(master),
    ], check=True, capture_output=True, text=True)
    subprocess.run([
        ffmpeg_exe(), "-y", "-v", "error", "-i", str(voice_video), "-i", str(master),
        "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-metadata:s:a:0", "language=ara",
        "-t", f"{duration:.6f}", "-movflags", "+faststart", str(output),
    ], check=True, capture_output=True)
    subprocess.run([
        ffmpeg_exe(), "-y", "-v", "error", "-i", str(master), "-c:a", "libmp3lame", "-b:a", "160k",
        str(output.with_suffix(".mp3")),
    ], check=True, capture_output=True)
    subtitles = voice_video.with_suffix(".srt")
    if subtitles.is_file():
        output.with_suffix(".srt").write_bytes(subtitles.read_bytes())
    meter = re.search(r'\{[^}]+"input_i"[^}]+\}', result.stderr, re.S)
    report = {
        "status": "rendered_pending_validation", "duration": duration,
        "voice_source": str(voice_video), "voice_source_sha256": sha256(voice_video),
        "voice_regenerated": False, "background_source": str(background), "background_sha256": sha256(background),
        "background_kind": "neural estimate from original uploaded audio, not newly composed music",
        "background_gain_db": gain_db,
        "ducking": {"threshold_linear": 0.05, "ratio": 3, "attack_ms": 20, "release_ms": 500, "detection": "rms"},
        "output": str(output), "output_sha256": sha256(output), "filter_graph": graph,
        "normalization": json.loads(meter[0]) if meter else None,
        "speech_contamination": "Neural separation can leave residuals; see the background audit, not a purity guarantee.",
        "video_reencoded": False,
    }
    output.with_suffix(".mix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-video", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/background-mix"))
    parser.add_argument("--background-gain-db", type=float, default=2.5)
    args = parser.parse_args()
    print(json.dumps(mix(args.voice_video, args.background, args.output, args.work_dir,
                         gain_db=args.background_gain_db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
