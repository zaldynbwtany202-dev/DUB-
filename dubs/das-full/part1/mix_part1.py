#!/usr/bin/env python3
"""voice-33 mux only for the cut first part. No DSP. No original narrator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "FFMPEG_BINARY",
    str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"),
)

from youtube_auto_dub.agent_tts import trim_silence  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.stem_split import decode_stereo, split_center  # noqa: E402

HERE = Path(__file__).resolve().parent
VIDEO = ROOT / "library/das-full/source.mp4"
CUT = HERE / "source-cut.mp4"
OUT = HERE / "final-dub-part1.mp4"
SR = 24000
PAUSE = 0.40
# First continuous takes on disk (seg-0007 blocked this turn).
N = 7
CUT_END = 679.7


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty {path}")
    return audio


def corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main() -> int:
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-v", "error",
            "-i", str(VIDEO), "-t", f"{CUT_END:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-an", "-movflags", "+faststart", str(CUT),
        ],
        check=True,
        capture_output=True,
    )
    gap = np.zeros(int(SR * PAUSE), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i in range(N):
        take, _ = trim_silence(decode(HERE / f"seg-{i:04d}.mp3"), SR)
        if i:
            parts.append(gap)
        parts.append(take.astype(np.float32))
    cat = np.concatenate(parts)
    peak = float(np.max(np.abs(cat)) or 1.0)
    if peak > 0.95:
        cat = cat * (0.95 / peak)
    dur = len(cat) / SR
    wav = HERE / "mixed.wav"
    sf.write(wav, cat, SR, subtype="PCM_24")

    factor = dur / CUT_END
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-v", "error",
            "-i", str(CUT), "-i", str(wav),
            "-filter_complex", f"[0:v]setpts={factor:.6f}*PTS,fps=25[v]",
            "-map", "[v]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-metadata:s:a:0", "language=ara",
            "-t", f"{dur:.6f}", "-movflags", "+faststart",
            str(OUT),
        ],
        check=True,
        capture_output=True,
    )

    out_a = decode(OUT)
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(VIDEO), "-t", f"{CUT_END:.3f}",
         "-ar", str(SR), "-ac", "2", "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    stereo = np.frombuffer(raw.stdout, dtype=np.float32)
    left, right = stereo[0::2], stereo[1::2]
    voice, _ = split_center(left, right, SR, strength=1.8)
    n = min(len(out_a), len(voice))
    c = corr(out_a[:n], voice[:n])
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(OUT)], capture_output=True, text=True).stderr
    n_audio = streams.count("Audio:")
    print("dur", round(dur, 3), "cut", CUT_END, "corr", round(c, 4), "streams", n_audio)
    if n_audio != 1:
        raise RuntimeError(f"audio streams {n_audio}")
    if abs(c) > 0.08:
        raise RuntimeError(f"original still present corr={c:.3f}")
    meta = {
        "voice_id": "voice-33",
        "processing": "none",
        "cut_end": CUT_END,
        "duration": round(dur, 3),
        "takes": N,
        "corr_original_vocals": round(c, 4),
        "audio_streams": n_audio,
        "original_voice_in_mix": False,
        "merge": "forbidden",
    }
    (HERE / "final-dub-part1.agent.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS", OUT, OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
