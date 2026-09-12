#!/usr/bin/env python3
"""Fresh 0911-1 dub: fluent phrases, dubbed voice only, no original narrator."""
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

from youtube_auto_dub.agent_tts import trim_silence  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.island_sync import speech_islands  # noqa: E402
from youtube_auto_dub.stem_split import (  # noqa: E402
    N_FFT,
    _istft,
    _smooth,
    _stft,
    decode_stereo,
    split_center,
)

HERE = Path(__file__).resolve().parent
VIDEO = ROOT / "library/0911-1/source.mp4"
OUT = HERE / "final-dub-fresh.mp4"
SR = 24000
DURATION = 32.25
# Original narrator is faster than this TTS. Matching soundtrack
# needs a bit over 1.08; never slow down, never chop words.
MIN_TEMPO = 1.0
MAX_TEMPO = 1.22

PHRASES = [
    {
        "start": 0.00,
        "end": 9.40,
        "text": "الارض بتاعته فضل يضحك ويبتسم لانه بعد ما قضى سنين طويله وهو بيشرب ميه طينيه في ارض المعركه اخيرا بقى قدامه ميه نظيفه بيقدر ان هو يشربها",
    },
    {
        "start": 9.40,
        "end": 16.78,
        "text": "ودي في حد ذاتها تعتبر رفاهيه بالنسبه لبطلنا وعلى ما الليل جا داس كان اخد قراره وقال لنفسه ان هو هيعمل كل اللي يقدر عليه",
    },
    {
        "start": 16.78,
        "end": 23.98,
        "text": "ويدي المكان ده فرصه اخيره لكن لو الدنيا فضلت مقفله وضلمه في وشه بعد يومين هيمشي وهيسيب المكان قبل ما يموت هنا",
    },
    {
        "start": 23.98,
        "end": 32.25,
        "text": "ولو وصلت لكده هيدور على اي قريه ثانيه ويحاول يفيدهم باي طريقه علشان يفضل دايما محقق وصيه اهله الاخيره قبل ما يموتوا وعلى",
    },
]


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


def stamp(seconds: float) -> str:
    total, ms = divmod(round(seconds * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def clause_audio(take: np.ndarray) -> np.ndarray:
    """Keep clauses together. Do not slice into isolated words."""
    trimmed, _ = trim_silence(take, SR)
    islands = speech_islands(trimmed, SR, floor_db=-36.0, min_dur=0.12, merge_gap=0.28)
    if not islands:
        raise RuntimeError("no speech")
    gap = np.zeros(int(SR * 0.06), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i, (a, b) in enumerate(islands):
        if i:
            parts.append(gap)
        parts.append(trimmed[a:b])
    return np.concatenate(parts)


def scale_audio(chunk: np.ndarray, tempo: float) -> np.ndarray:
    if abs(tempo - 1.0) < 0.002:
        return chunk.astype(np.float32)
    new_len = max(1, int(round(len(chunk) / tempo)))
    x = np.arange(new_len) * (len(chunk) / new_len)
    idx = np.clip(x.astype(int), 0, len(chunk) - 1)
    return chunk[idx].astype(np.float32)


def clean_bed(sr: int, n: int) -> np.ndarray:
    """Music/effects only. Speech band is muted so original voice cannot leak."""
    left, right = decode_stereo(VIDEO, sr)
    voice, residual = split_center(left, right, sr, strength=2.2)
    residual = residual[:n] if len(residual) >= n else np.pad(residual, (0, n - len(residual)))
    voice = voice[:n] if len(voice) >= n else np.pad(voice, (0, n - len(voice)))
    spec = _stft(residual.astype(np.float32))
    vspec = _stft(voice.astype(np.float32))
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    speech = (freqs >= 140.0) & (freqs <= 4500.0)
    mag_v, mag_r = np.abs(vspec), np.abs(spec)
    mask = np.ones_like(mag_r, dtype=np.float32)
    mask[speech, :] = 0.04
    mask[speech, :] = np.where(mag_v[speech, :] > mag_r[speech, :] * 0.15, 0.015, mask[speech, :])
    mask = _smooth(mask, frames=5)
    bed = _istft(spec * mask, n)
    return bed.astype(np.float32)


def main() -> int:
    phrases = [clause_audio(decode(HERE / f"seg-{i:04d}.mp3")) for i in range(4)]
    n = int(round(DURATION * SR))
    speech_len = sum(len(p) for p in phrases)
    room = n - int(SR * 0.04)
    needed = (speech_len / SR) / (room / SR)
    if needed > MAX_TEMPO:
        raise RuntimeError(f"needs {needed:.3f}x; cap {MAX_TEMPO}")
    tempo = MIN_TEMPO if needed <= MIN_TEMPO else needed
    scaled = [scale_audio(p, tempo) for p in phrases]
    starts_sec = [p["start"] for p in PHRASES]
    starts = [int(s * SR) for s in starts_sec]
    for i in range(1, 4):
        min_start = starts[i - 1] + len(scaled[i - 1])
        if starts[i] < min_start:
            starts[i] = min_start
    last = starts[-1] + len(scaled[-1])
    if last > n:
        shift = last - n
        starts = [max(0, s - shift) for s in starts]
        for i in range(1, 4):
            min_start = starts[i - 1] + len(scaled[i - 1])
            if starts[i] < min_start:
                starts[i] = min_start
        if starts[-1] + len(scaled[-1]) > n:
            raise RuntimeError("overrun; refusing to truncate")

    aligned = np.zeros(n, dtype=np.float32)
    placed = []
    for start, chunk, phrase in zip(starts, scaled, PHRASES):
        aligned[start:start + len(chunk)] += chunk
        placed.append(
            {
                "start": round(start / SR, 3),
                "end": round((start + len(chunk)) / SR, 3),
                "text": phrase["text"],
            }
        )
    fade = min(int(SR * 0.004), n // 8)
    aligned[:fade] *= np.linspace(0, 1, fade)
    aligned[-fade:] *= np.linspace(1, 0, fade)

    bed = clean_bed(SR, n)
    mixed = aligned + bed * 0.25
    peak = float(np.max(np.abs(mixed)) or 1.0)
    if peak > 0.95:
        mixed *= 0.95 / peak

    wav = HERE / "mixed.wav"
    sf.write(HERE / "aligned.wav", aligned, SR, subtype="PCM_24")
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
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(HERE / "final-dub-fresh.mp3")],
        check=True,
        capture_output=True,
    )
    srt = "\n\n".join(
        f"{i}\n{stamp(p['start'])} --> {stamp(p['end'])}\n{p['text']}"
        for i, p in enumerate(placed, 1)
    ) + "\n"
    (HERE / "final-dub-fresh.srt").write_text(srt, encoding="utf-8")
    meta = {
        "voice_id": "voice-27",
        "phrases": 4,
        "tempo": round(tempo, 4),
        "slowed": False,
        "speech_truncated": False,
        "original_voice_in_mix": False,
        "word_by_word_islands": False,
        "merge": "forbidden",
        "placements": placed,
        "output": str(OUT.relative_to(ROOT)),
    }
    (HERE / "final-dub-fresh.agent.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("tempo", round(tempo, 4), "wrote", OUT, "size", OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
