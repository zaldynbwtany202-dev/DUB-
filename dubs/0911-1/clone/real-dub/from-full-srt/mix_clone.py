#!/usr/bin/env python3
"""Clone original-narrator timbre onto the SRT-locked take, soundtrack-sync."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "FFMPEG_BINARY",
    str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"),
)

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.island_sync import speech_islands  # noqa: E402
from youtube_auto_dub.voice_profile import convert, measure  # noqa: E402

HERE = Path(__file__).resolve().parent
TAKE = HERE / "v27" / "full-pass.mp3"
VOCALS = ROOT / "dubs/0911-1/clone/real-dub/stems/vocals.wav"
MUSIC = ROOT / "dubs/0911-1/clone/real-dub/stems/music.wav"
VIDEO = ROOT / "library/0911-1/source.mp4"
LOCKED = ROOT / "dubs/0911-1/LOCKED_TRANSCRIPT.json"
OUT_MP4 = HERE / "final-dub-clone-srt.mp4"
SR = 24000
MIN_TEMPO = 1.0
MAX_TEMPO = 1.08
DURATION = 32.25


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if audio.size == 0:
        raise RuntimeError(f"empty decode {path}")
    return audio


def stamp(seconds: float) -> str:
    total, ms = divmod(round(seconds * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def layout(dub: np.ndarray, original: np.ndarray) -> tuple[np.ndarray, dict]:
    dub_islands = speech_islands(dub, SR, floor_db=-36.0, min_dur=0.12, merge_gap=0.18)
    orig_islands = speech_islands(original, SR, floor_db=-36.0, min_dur=0.12, merge_gap=0.18)
    if not dub_islands:
        raise RuntimeError("no speech in take")
    chunks = [dub[a:b] for a, b in dub_islands]
    speech_len = sum(len(c) for c in chunks)
    window = len(original)
    room = max(window - int(SR * 0.04), 1)
    needed = (speech_len / SR) / (room / SR)
    if needed > MAX_TEMPO:
        raise RuntimeError(f"speech needs {needed:.3f}x; cap {MAX_TEMPO}")
    tempo = MIN_TEMPO if needed <= MIN_TEMPO else needed
    scale = 1.0 / tempo
    scaled = []
    for chunk in chunks:
        new_len = max(1, int(round(len(chunk) * scale)))
        x = np.arange(new_len) * (len(chunk) / new_len)
        idx = np.clip(x.astype(int), 0, len(chunk) - 1)
        scaled.append(chunk[idx].astype(np.float32))

    starts: list[int] = []
    if orig_islands:
        for i, chunk in enumerate(scaled):
            if i < len(orig_islands):
                starts.append(orig_islands[i][0])
            else:
                starts.append(starts[-1] + len(scaled[i - 1]) + int(0.04 * SR))
    else:
        cursor = 0
        for chunk in scaled:
            starts.append(cursor)
            cursor += len(chunk) + int(0.05 * SR)

    for i in range(1, len(starts)):
        min_start = starts[i - 1] + len(scaled[i - 1])
        if starts[i] < min_start:
            starts[i] = min_start
    last_end = starts[-1] + len(scaled[-1])
    if last_end > window:
        shift = last_end - window
        starts = [max(0, s - shift) for s in starts]
        for i in range(1, len(starts)):
            min_start = starts[i - 1] + len(scaled[i - 1])
            if starts[i] < min_start:
                starts[i] = min_start
        if starts[-1] + len(scaled[-1]) > window:
            raise RuntimeError("fitted speech still overruns; refusing to truncate")

    out = np.zeros(window, dtype=np.float32)
    placed = []
    for start, chunk in zip(starts, scaled):
        end = start + len(chunk)
        out[start:end] += chunk
        placed.append({"start": round(start / SR, 3), "end": round(end / SR, 3)})
    fade = min(int(SR * 0.004), max(1, window // 8))
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out, {
        "tempo": round(tempo, 4),
        "slowed": tempo < 0.999,
        "speech_truncated": False,
        "dub_islands": len(dub_islands),
        "original_islands": len(orig_islands),
        "speech_seconds": round(sum(len(c) for c in scaled) / SR, 3),
        "placements": placed,
    }


def main() -> int:
    take = decode(TAKE)
    vocals = decode(VOCALS)
    music = decode(MUSIC)
    n = int(round(DURATION * SR))
    vocals = vocals[:n] if len(vocals) >= n else np.pad(vocals, (0, n - len(vocals)))
    music = music[:n] if len(music) >= n else np.pad(music, (0, n - len(music)))

    target = measure(vocals, SR)
    source = measure(take, SR, keep=0.35)
    report = {"pitch_semitones": 0.0, "formant_ratio": 1.0}
    converted = take
    if target and source and target.f0_median > 0 and source.f0_median > 0:
        converted, report = convert(take, SR, source, target)
        print("clone", report, "target_f0", round(target.f0_median, 1), "source_f0", round(source.f0_median, 1))
    else:
        print("clone skipped", "target", target, "source", source)

    peak_t = float(np.max(np.abs(take)) or 1.0)
    peak_c = float(np.max(np.abs(converted)) or 1.0)
    converted = (converted * (peak_t / peak_c)).astype(np.float32)

    aligned, layout_report = layout(converted, vocals)
    print("layout", {k: v for k, v in layout_report.items() if k != "placements"})

    # Match original speech loudness, keep music under it.
    speech_mask = np.abs(vocals) > (float(np.max(np.abs(vocals)) or 1.0) * 0.08)
    if speech_mask.any() and float(np.max(np.abs(aligned))) > 0:
        orig_rms = float(np.sqrt(np.mean(vocals[speech_mask] ** 2)))
        dub_mask = np.abs(aligned) > (float(np.max(np.abs(aligned))) * 0.08)
        dub_rms = float(np.sqrt(np.mean(aligned[dub_mask] ** 2))) if dub_mask.any() else 1.0
        if dub_rms > 1e-6:
            aligned = aligned * (orig_rms / dub_rms) * 1.05

    mixed = aligned + music * 0.42
    peak = float(np.max(np.abs(mixed)) or 1.0)
    if peak > 0.95:
        mixed = mixed * (0.95 / peak)

    aligned_wav = HERE / "aligned.wav"
    mixed_wav = HERE / "mixed.wav"
    sf.write(aligned_wav, aligned, SR, subtype="PCM_24")
    sf.write(mixed_wav, mixed, SR, subtype="PCM_24")

    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-v", "error",
            "-i", str(VIDEO), "-i", str(mixed_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-metadata:s:a:0", "language=ara",
            "-t", f"{DURATION:.3f}", "-movflags", "+faststart",
            str(OUT_MP4),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(mixed_wav), "-c:a", "libmp3lame", "-b:a", "160k", str(HERE / "final-dub-clone-srt.mp3")],
        check=True,
        capture_output=True,
    )

    locked = json.loads(LOCKED.read_text(encoding="utf-8"))
    lines = locked["lines"]
    if lines and float(lines[0]["start"]) > 0.01:
        lines[0]["start"] = 0.0
        if not str(lines[0]["text"]).startswith("الارض"):
            lines[0]["text"] = "الارض بتاعته " + lines[0]["text"]
    srt = "\n\n".join(
        f"{i}\n{stamp(float(s['start']))} --> {stamp(float(s['end']))}\n{s['text']}"
        for i, s in enumerate(lines, 1)
    ) + "\n"
    (HERE / "final-dub-clone-srt.srt").write_text(srt, encoding="utf-8")

    meta = {
        "voice_id": "voice-27",
        "clone_method": "praat_f0_formant_from_original_vocals",
        "words": "full-video-srt-cues-44-56",
        "clone": report,
        "layout": layout_report,
        "min_tempo": MIN_TEMPO,
        "max_tempo": MAX_TEMPO,
        "merge": "forbidden",
        "output": str(OUT_MP4.relative_to(ROOT)),
    }
    (HERE / "final-dub-clone-srt.agent.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", OUT_MP4, "size", OUT_MP4.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
