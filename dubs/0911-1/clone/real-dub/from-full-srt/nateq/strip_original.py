#!/usr/bin/env python3
"""Keep the approved fluent nateq take; kill original narrator in the bed."""
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
from youtube_auto_dub.stem_split import (  # noqa: E402
    N_FFT,
    HOP,
    _istft,
    _smooth,
    _stft,
    decode_stereo,
    split_center,
)

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


def kill_original(bleed: np.ndarray, voice: np.ndarray, sr: int) -> np.ndarray:
    """Wiener-suppress original vocals in the speech band; keep loud dubbed speech."""
    n = min(len(bleed), len(voice))
    bleed = bleed[:n].astype(np.float32)
    voice = voice[:n].astype(np.float32)
    b_spec = _stft(bleed)
    v_spec = _stft(voice)
    mag_b = np.abs(b_spec)
    mag_v = np.abs(v_spec)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    speech = ((freqs >= 140.0) & (freqs <= 4500.0))[:, None]
    # If the original dominates a bin, mute it. If the dub is louder, keep it.
    ratio = (mag_v ** 2) / (mag_b ** 2 + 1e-8)
    suppress = np.clip(1.0 - 2.8 * ratio, 0.02, 1.0)
    # Extra: bins where original is clearly present and mix is not much louder.
    leak = speech & (mag_v > 0.004) & (mag_b < mag_v * 1.15)
    suppress = np.where(leak, 0.02, suppress)
    suppress = np.where(speech, suppress, 1.0)
    suppress = _smooth(suppress.astype(np.float32), frames=3)
    cleaned = _istft(b_spec * suppress, n)

    # Time-domain residual: project remaining original out of quiet frames.
    frame = int(sr * 0.02)
    out = cleaned.copy()
    v_peak = float(np.max(np.abs(voice)) or 1.0)
    for start in range(0, n - frame, frame):
        sl = slice(start, start + frame)
        v = voice[sl]
        c = out[sl]
        v_rms = float(np.sqrt(np.mean(v ** 2)))
        c_rms = float(np.sqrt(np.mean(c ** 2)))
        if v_rms < v_peak * 0.04:
            continue
        denom = float(np.dot(v, v) + 1e-12)
        proj = (np.dot(c, v) / denom) * v
        # Always remove the parallel original component; stronger in quiet beds.
        amount = 1.0 if c_rms < v_rms * 0.8 else 0.65
        out[sl] = c - amount * proj
    return out.astype(np.float32)


def main() -> int:
    bleed = decode(BLEED)
    left, right = decode_stereo(VIDEO, SR)
    voice, residual = split_center(left, right, SR, strength=2.0)
    n = min(len(bleed), len(voice), int(round(DURATION * SR)))
    clean = kill_original(bleed[:n], voice[:n], SR)

    # Add a speech-gated score bed so the mix is not dry after killing leak.
    bed = residual[:n].astype(np.float32)
    b_spec = _stft(bed)
    v_spec = _stft(voice[:n])
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    speech = ((freqs >= 160.0) & (freqs <= 4200.0))[:, None]
    mag_v, mag_b = np.abs(v_spec), np.abs(b_spec)
    mask = np.ones_like(mag_b, dtype=np.float32)
    mask[speech & (mag_v > mag_b * 0.25)] = 0.03
    mask = _smooth(mask, frames=4)
    bed = _istft(b_spec * mask, n)
    mixed = clean + bed * 0.22
    peak = float(np.max(np.abs(mixed)) or 1.0)
    if peak > 0.95:
        mixed = mixed * (0.95 / peak)

    wav = HERE / "stripped.wav"
    sf.write(wav, mixed, SR, subtype="PCM_24")
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
        "note": "Fluent approved take. Original narrator Wiener-suppressed in the speech band; not re-chopped.",
        "merge": "forbidden",
        "original_voice_removed": True,
        "speech_relaid": False,
        "output": str(OUT.relative_to(ROOT)),
    }
    (HERE / "final-dub-nateq.agent.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", OUT, "size", OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
