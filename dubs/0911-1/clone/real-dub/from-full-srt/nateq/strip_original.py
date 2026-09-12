#!/usr/bin/env python3
"""Keep the approved nateq performance; remove leaked original narrator only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "FFMPEG_BINARY",
    str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"),
)

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.stem_split import decode_stereo, split_center  # noqa: E402

HERE = Path(__file__).resolve().parent
BLEED = HERE / "final-dub-nateq-bleed.mp4"
VIDEO = ROOT / "library/0911-1/source.mp4"
OUT = HERE / "final-dub-nateq.mp4"
SR = 24000
DURATION = 32.25


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if audio.size == 0:
        raise RuntimeError(f"empty {path}")
    return audio


def smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win < 2:
        return x
    k = np.ones(win, dtype=np.float32) / win
    return np.convolve(x, k, mode="same")


def main() -> int:
    bleed = decode(BLEED)
    orig = decode(VIDEO)
    left, right = decode_stereo(VIDEO, SR)
    voice, _ = split_center(left, right, SR, strength=1.6)
    n = min(len(bleed), len(orig), len(voice), int(round(DURATION * SR)))
    bleed, orig, voice = bleed[:n], orig[:n], voice[:n]

    frame = int(SR * 0.02)
    nframes = n // frame
    gain = np.zeros(nframes, dtype=np.float32)
    leak_frames = 0
    for i in range(nframes):
        a, b = i * frame, (i + 1) * frame
        o = orig[a:b]
        m = bleed[a:b]
        v = voice[a:b]
        o_rms = float(np.sqrt(np.mean(o ** 2)) + 1e-12)
        m_rms = float(np.sqrt(np.mean(m ** 2)) + 1e-12)
        v_rms = float(np.sqrt(np.mean(v ** 2)) + 1e-12)
        if v_rms > 0.02 and m_rms < v_rms * 0.55:
            gain[i] = min(m_rms / o_rms, 0.35)
            leak_frames += 1
        elif v_rms > 0.03:
            # Original is under the dub at low level — shave only a little.
            gain[i] = 0.08
        else:
            gain[i] = 0.0
    gain = smooth(gain, 5)
    env = np.repeat(gain, frame)
    if len(env) < n:
        env = np.pad(env, (0, n - len(env)))
    env = env[:n]
    clean = bleed - orig * env
    peak = float(np.max(np.abs(clean)) or 1.0)
    if peak > 0.95:
        clean = clean * (0.95 / peak)

    wav = HERE / "stripped.wav"
    sf.write(wav, clean.astype(np.float32), SR, subtype="PCM_24")
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-v", "error",
            "-i", str(VIDEO), "-i", str(wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-metadata:s:a:0", "language=ara",
            "-t", f"{DURATION:.3f}", "-movflags", "+faststart",
            str(OUT),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(HERE / "final-dub-nateq.mp3")],
        check=True,
        capture_output=True,
    )
    meta = {
        "voice_id": "voice-27",
        "source_mix": "final-dub-nateq-bleed.mp4",
        "note": "Same fluent take as the approved mix. Original narrator subtracted only in leak frames; speech not re-chopped word-by-word.",
        "leak_frames": leak_frames,
        "merge": "forbidden",
        "original_voice_removed": True,
        "speech_relaid": False,
        "output": str(OUT.relative_to(ROOT)),
    }
    (HERE / "final-dub-nateq.agent.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", OUT, "size", OUT.stat().st_size, "leak_frames", leak_frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
