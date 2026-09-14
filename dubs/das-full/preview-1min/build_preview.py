#!/usr/bin/env python3
"""One-minute voice-33 preview for the Das recap — two pacing variants.

Words: YouTube `unique.txt` via `library/das-full/youtube/NB2YcTh_L6k.spoken.srt`.
Takes: the committed voice-33 recordings `dubs/das-full/youtube-v33/seg-000{0,1,2}.mp3`.
Video: `.cache/das-full/source.mp4` (reassembled from the 9 verified inbox parts).

Variant A (`-natural`): tempo 1.0, no processing at all. Takes are placed one after
  another, so the narration drifts behind the picture as it goes.
Variant B (`-matched`): each take is time-fitted with `atempo` to land exactly on the
  original speech onset/offset of its group. Pitch preserved, picture untouched.

Neither variant keeps the original narrator: the source audio track is dropped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
# ffmpeg_exe() resolves FFMPEG_BINARY, then PATH, then the bundled imageio-ffmpeg binary.

from youtube_auto_dub.agent_tts import trim_silence  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.stem_split import decode_stereo, split_center  # noqa: E402

VIDEO = ROOT / ".cache/das-full/source.mp4"
TAKES = ROOT / "dubs/das-full/youtube-v33"
GROUPS = json.loads((TAKES / "groups.json").read_text(encoding="utf-8"))["groups"][:3]
SR = 24000
GAP = 0.35          # variant A: pause between takes
TAIL_SAFETY = 0.08  # variant B: never touch the next group's onset


def decode(path: Path, sr: int = SR, mono: bool = True) -> np.ndarray:
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr),
           "-ac", "1" if mono else "2", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True)
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty decode: {path}")
    return audio.copy()


def fitted(take: np.ndarray, rate: float) -> np.ndarray:
    """atempo only — pitch preserving time fit. rate 1.0 returns the take unchanged."""
    if abs(rate - 1.0) < 0.002:
        return take
    tmp_in = HERE / "work-in.wav"
    tmp_out = HERE / "work-out.wav"
    sf.write(tmp_in, take, SR, subtype="PCM_24")
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(tmp_in),
                    "-af", f"atempo={rate:.9f}", "-ar", str(SR), "-ac", "1", str(tmp_out)],
                   check=True, capture_output=True)
    out, _ = sf.read(tmp_out, dtype="float32")
    tmp_in.unlink(missing_ok=True)
    tmp_out.unlink(missing_ok=True)
    return out.astype(np.float32)


def build(variant: str, placements: list[tuple[float, np.ndarray]], video_seconds: float) -> dict:
    total = int(round((max(s + len(a) / SR for s, a in placements) + 0.5) * SR))
    track = np.zeros(total, dtype=np.float32)
    for start, audio in placements:
        i = int(round(start * SR))
        track[i:i + len(audio)] += audio
    peak = float(np.max(np.abs(track)) or 1.0)
    if peak > 0.95:
        track *= 0.95 / peak
    wav = HERE / f"narration-{variant}.wav"
    sf.write(wav, track, SR, subtype="PCM_24")

    out = HERE / f"final-dub-preview-{variant}.mp4"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error",
         "-i", str(VIDEO), "-t", f"{video_seconds:.3f}",
         "-i", str(wav),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-metadata:s:a:0", "language=ara",
         "-shortest", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True)

    # Verification: one audio stream, no original narrator correlation, duration sane.
    out_audio = decode(out)
    stereo = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(VIDEO), "-t", f"{video_seconds:.3f}",
         "-ar", str(SR), "-ac", "2", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    st = np.frombuffer(stereo, dtype=np.float32)
    left, right = st[0::2], st[1::2]
    original_voice, _ = split_center(left, right, SR, strength=1.8)
    n = min(len(out_audio), len(original_voice))
    a = out_audio[:n] - out_audio[:n].mean()
    b = original_voice[:n] - original_voice[:n].mean()
    corr = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(out)],
                             capture_output=True, text=True).stderr
    n_audio = streams.count("Audio:")
    duration = len(out_audio) / SR
    report = {"variant": variant, "output": str(out.relative_to(ROOT)), "bytes": out.stat().st_size,
              "audio_seconds": round(duration, 3), "video_seconds": round(video_seconds, 3),
              "audio_streams": n_audio, "corr_original_vocals": round(corr, 4),
              "original_voice_in_mix": abs(corr) > 0.08}
    if n_audio != 1:
        raise RuntimeError(f"{variant}: {n_audio} audio streams")
    if abs(corr) > 0.08:
        raise RuntimeError(f"{variant}: original narrator still present corr={corr:.3f}")
    wav.unlink(missing_ok=True)
    return report


def main() -> int:
    if not VIDEO.exists():
        raise SystemExit(f"missing source video: {VIDEO}")
    takes = []
    for g in GROUPS:
        raw = decode(TAKES / f"seg-{g['i']:04d}.mp3")
        trimmed, _ = trim_silence(raw, SR)
        takes.append((g, trimmed))

    reports = []

    # --- Variant A: natural speed, sequential, drift allowed --------------------
    placements, cursor = [], GROUPS[0]["t0"]
    for g, audio in takes:
        placements.append((cursor, audio))
        cursor += len(audio) / SR + GAP
    reports.append(build("natural", placements, cursor + 0.2))
    reports[-1]["tempo"] = 1.0
    reports[-1]["drift_at_end_s"] = round(cursor - GAP - GROUPS[-1]["t1"], 2)

    # --- Variant B: atempo fit onto the original onsets ------------------------
    placements = []
    tempi = []
    for g, audio in takes:
        window = g["t1"] - g["t0"]
        rate = len(audio) / SR / max(window - TAIL_SAFETY, 0.5)
        fit = fitted(audio, rate)
        placements.append((g["t0"], fit))
        tempi.append(round(rate, 3))
    reports.append(build("matched", placements, GROUPS[-1]["t1"] + 0.5))
    reports[-1]["tempo_per_take"] = tempi
    reports[-1]["drift_at_end_s"] = 0.0

    # --- Variant C: one uniform pace, sequential, small residual drift ----------
    uniform = round(sum(len(a) for _, a in takes) / SR
                    / (GROUPS[-1]["t1"] - GROUPS[0]["t0"]), 3)
    placements, cursor, ends = [], GROUPS[0]["t0"], []
    for g, audio in takes:
        fit = fitted(audio, uniform)
        placements.append((cursor, fit))
        cursor += len(fit) / SR + 0.20
        ends.append(cursor - 0.20 - g["t1"])
    reports.append(build("uniform", placements, max(cursor, GROUPS[-1]["t1"]) + 0.5))
    reports[-1]["tempo"] = uniform
    reports[-1]["per_take_end_offset_s"] = [round(e, 2) for e in ends]
    reports[-1]["drift_at_end_s"] = round(ends[-1], 2)

    meta = {
        "voice_id": "voice-33",
        "takes_from": "dubs/das-full/youtube-v33/seg-0000..0002.mp3 (committed, no re-generation)",
        "words_from": "youtube unique.txt",
        "times_from": "NB2YcTh_L6k.spoken.srt",
        "dsp": "atempo only in variant matched; none in variant natural",
        "original_narrator_kept": False,
        "background_music_kept": False,
        "merge": "forbidden",
        "groups_covered": [g["i"] for g in GROUPS],
        "source_window_s": [GROUPS[0]["t0"], GROUPS[-1]["t1"]],
        "variants": reports,
    }
    (HERE / "preview-report.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
