"""ffmpeg / ffprobe helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise FFmpegError(
            f"'{binary}' not found. Install ffmpeg: https://ffmpeg.org/download.html"
        )


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"command failed: {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def probe_duration(path: str | Path) -> float:
    _require("ffprobe")
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def extract_audio(video: str | Path, out: str | Path, sr: int = 44100) -> Path:
    """Extract the audio track of a video to wav."""
    _require("ffmpeg")
    out = Path(out)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(sr), str(out)])
    return out


def trim_silence(inp: str | Path, out: str | Path, threshold: str = "-45dB") -> Path:
    """Remove leading/trailing silence from a clip (both ends)."""
    _require("ffmpeg")
    out = Path(out)
    af = (
        f"silenceremove=start_periods=1:start_threshold={threshold}:start_silence=0.05,"
        "areverse,"
        f"silenceremove=start_periods=1:start_threshold={threshold}:start_silence=0.08,"
        "areverse,"
        "aformat=sample_rates=44100:channel_layouts=stereo"
    )
    run(["ffmpeg", "-y", "-v", "error", "-i", str(inp), "-af", af, str(out)])
    return out


def cut_concat(audio: str | Path, ranges: list[tuple[float, float]], out: str | Path) -> Path:
    """Cut several [start,end] ranges from one file and concatenate them."""
    _require("ffmpeg")
    out = Path(out)
    parts = []
    labels = []
    for i, (a, b) in enumerate(ranges):
        parts.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS[s{i}]")
        labels.append(f"[s{i}]")
    filt = ";".join(parts) + f";{''.join(labels)}concat=n={len(ranges)}:v=0:a=1,loudnorm[out]"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(audio),
         "-filter_complex", filt, "-map", "[out]", str(out)])
    return out


def atempo_chain(factor: float) -> str:
    """ffmpeg atempo accepts 0.5–100 per filter; chain for safety."""
    factor = max(0.5, min(factor, 4.0))
    steps = []
    while factor > 2.0:
        steps.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        steps.append(0.5)
        factor /= 0.5
    steps.append(factor)
    return ",".join(f"atempo={s:.4f}" for s in steps)


def mux(video: str | Path, audio: str | Path, out: str | Path) -> Path:
    """Replace a video's audio track (video stream copied untouched)."""
    _require("ffmpeg")
    out = Path(out)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return out
