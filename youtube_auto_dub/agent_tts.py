"""Sync already-generated Arena voice takes; never invoke a TTS provider.

The strict agent path fits complete takes without cutting spoken words, keeps
intentional gaps, and refuses missing audio. An explicit --until render is a
preview, not a completed dub. Relative media paths belong to the cue sheet.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe, ffprobe_exe
from youtube_auto_dub.models import SR_TTS


def load_cues(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("cue sheet must be a JSON object")
    if not isinstance(data.get("segments"), list) or not data["segments"]:
        raise ValueError("cue sheet needs a non-empty segments list")
    return data


def write_cues(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_cue_paths(cues: dict[str, Any], root: Path) -> dict[str, Any]:
    """Resolve copies, never mutate the portable on-disk recipe."""
    def resolve(value):
        p = Path(value)
        return str((root / p).resolve()) if not p.is_absolute() else str(p)
    result = dict(cues)
    result["video"] = resolve(cues["video"])
    result["segments"] = [{**s, "audio": resolve(s["audio"])} for s in cues["segments"]]
    if cues.get("background_audio"):
        result["background_audio"] = resolve(cues["background_audio"])
    return result


def cues_from_segments(video: Path, segments: list[dict[str, Any]], *, voice_id: str = "voice-00",
                       language: str = "ar-EG", audio_dir: Path | None = None) -> dict[str, Any]:
    audio_dir = Path(audio_dir or "agent_voice")
    out = []
    for i, raw in enumerate(segments):
        start, end = float(raw["start"]), float(raw["end"])
        text = str(raw.get("text") or raw.get("translated_text") or raw.get("source_text") or "").strip()
        if not text or not all(math.isfinite(t) for t in (start, end)) or start < 0 or end <= start:
            continue
        out.append({"index": i, "start": round(start, 3), "end": round(end, 3), "text": text,
                    "audio": str(audio_dir / f"seg-{i:04d}.mp3"), "speaker": raw.get("speaker") or "SPEAKER_00"})
    return {"video": str(video), "voice_backend": "agent", "voice_id": voice_id,
            "language": language, "segments": out}


def probe_duration(path: Path) -> float:
    """Use configured ffprobe, or the bundled FFmpeg when ffprobe is absent."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    duration = None
    try:
        result = subprocess.run([ffprobe_exe(), "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                check=True, capture_output=True, text=True)
        duration = float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        # ffmpeg -i exits non-zero when no output was requested; its metadata is still valid.
        result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if match:
            duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Cannot determine media duration: {path}")
    return duration


def validate_timeline(segments: list[dict], duration: float) -> list[dict]:
    ordered = sorted(segments, key=lambda s: float(s["start"]))
    previous_end = 0.0
    for seg in ordered:
        start, end = float(seg["start"]), float(seg["end"])
        if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start or end > duration + 0.001:
            raise ValueError(f"Invalid cue interval: {start}..{end} (duration {duration})")
        if start < previous_end - 0.001:
            raise ValueError("Overlapping agent cues")
        if not str(seg.get("text", "")).strip() or not seg.get("audio"):
            raise ValueError("Every cue must name its text and recorded audio")
        previous_end = end
    return ordered


def trim_silence(samples: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
    """Trim only edge silence, keeping 80 ms of safety around detected audio."""
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError("Empty or non-finite voice take")
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-5:
        raise ValueError("Voice take is silent")
    frame = max(1, int(sr * 0.01))
    padded = np.pad(samples, (0, (-len(samples)) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    active = np.flatnonzero(rms > max(1e-5, peak * 10 ** (-48 / 20)))
    if not len(active):
        raise ValueError("No audible speech in voice take")
    margin = int(sr * 0.08)
    begin = max(0, int(active[0]) * frame - margin)
    end = min(len(samples), (int(active[-1]) + 1) * frame + margin)
    return samples[begin:end].copy(), {"leading_silence_removed": begin / sr,
                                       "trailing_silence_removed": (len(samples) - end) / sr}


def fit_take(seg: dict, work_dir: Path, order: int, *, min_tempo: float = 0.88,
             max_tempo: float = 1.6) -> tuple[np.ndarray, dict]:
    sr = SR_TTS
    decoded = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(seg["audio"]),
                              "-vn", "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
                             capture_output=True, check=True)
    original = np.frombuffer(decoded.stdout, dtype=np.float32)
    source_duration = len(original) / sr
    audio_start = float(seg.get("audio_start", 0))
    audio_end = float(seg.get("audio_end", source_duration))
    if not all(math.isfinite(t) for t in (audio_start, audio_end)) or audio_start < 0 or audio_end <= audio_start or audio_end > source_duration + 0.001:
        raise ValueError("Invalid take slice; do not cut spoken words to fit a cue")
    samples = original[round(audio_start * sr):round(audio_end * sr)]
    samples, silence = trim_silence(samples, sr)
    natural_duration = len(samples) / sr
    window = float(seg["end"]) - float(seg["start"])
    budget = window - min(0.08, window * 0.05)  # small safety tail prevents clipping at the next cue
    rate = max(min_tempo, natural_duration / budget)
    if rate > max_tempo:
        raise ValueError(f"Cue {seg.get('index', order)} needs {rate:.3f}x tempo; shorten/re-record it (cap {max_tempo})")
    raw = work_dir / f"take-{order:04d}.wav"
    fitted = work_dir / f"fit-{order:04d}.wav"
    sf.write(raw, samples, sr, subtype="PCM_24")
    if abs(rate - 1) > 0.002:
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(raw), "-af", f"atempo={rate:.9f}",
                        "-ar", str(sr), "-ac", "1", str(fitted)], check=True, capture_output=True)
        samples, _ = sf.read(fitted, dtype="float32")
    if len(samples) > round(window * sr):
        raise ValueError(f"Fitted cue {seg.get('index', order)} still overruns its window; refusing to truncate speech")
    # Tiny fades only suppress discontinuities; no syllables are discarded.
    fade = min(int(sr * 0.004), len(samples) // 2)
    if fade:
        samples[:fade] *= np.linspace(0, 1, fade)
        samples[-fade:] *= np.linspace(1, 0, fade)
    return samples, {"index": seg.get("index", order), "start": float(seg["start"]), "end": float(seg["end"]),
                     "audio": str(seg["audio"]), "audio_start": audio_start, "audio_end": audio_end,
                     "natural_seconds": natural_duration, "tempo": rate, "fitted_seconds": len(samples) / sr,
                     "text": seg["text"], "speech_truncated": False, **silence}


def _srt_stamp(seconds: float) -> str:
    total, ms = divmod(round(seconds * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def assemble(cues: dict[str, Any], *, work_dir: Path, output: Path, mix_background: bool = True,
             video: Path | None = None, end_time: float | None = None) -> Path:
    """Render complete takes, or an explicitly labelled prefix preview."""
    if cues.get("voice_backend") != "agent":
        raise ValueError("This renderer accepts agent-generated voice takes only")
    video = Path(video or cues["video"])
    output = Path(output)
    if video.resolve() == output.resolve():
        raise ValueError("Never overwrite the source video")
    source_duration = probe_duration(video)
    duration = source_duration if end_time is None else float(end_time)
    if not math.isfinite(duration) or not 0 < duration <= source_duration:
        raise ValueError("Preview end must be inside the source video")
    all_segments = validate_timeline(cues["segments"], source_duration)
    segments = [s for s in all_segments if float(s["start"]) < duration]
    if any(float(s["end"]) > duration + 0.001 for s in segments):
        raise ValueError("Preview end must be at a cue boundary, never in the middle of spoken text")
    if not segments:
        raise ValueError("No agent takes in requested interval")
    missing = [str(s["audio"]) for s in segments if not Path(s["audio"]).is_file() or Path(s["audio"]).stat().st_size == 0]
    if missing:
        raise FileNotFoundError("agent takes missing: " + ", ".join(missing))
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    timeline = np.zeros(round(duration * SR_TTS), dtype=np.float32)
    placed = []
    for i, seg in enumerate(segments):
        samples, report = fit_take(seg, work_dir, i)
        start = round(float(seg["start"]) * SR_TTS)
        if start + len(samples) > len(timeline):
            raise ValueError("Speech would extend past the video; refusing to trim it")
        timeline[start:start + len(samples)] += samples
        placed.append(report)
    aligned = work_dir / "aligned.wav"
    sf.write(aligned, timeline, SR_TTS, subtype="PCM_24")
    mix_input = aligned
    background_method = "removed_original_track"
    if mix_background:
        from youtube_auto_dub.audio import finalize_audio
        mix_input = work_dir / "with-background.wav"
        background = Path(cues["background_audio"]) if cues.get("background_audio") else None
        if background is not None and not background.is_file():
            raise FileNotFoundError(background)
        finalize_audio(aligned, video, mix_input, match_loudness=False, mix_ambient=True,
                       stereo_source=video, ambient_source=background)
        background_method = "provided_stem" if background else "approximate_source_separation_not_guaranteed_speech_free"
    final_wav = work_dir / "final.wav"
    normalise = subprocess.run([ffmpeg_exe(), "-y", "-v", "info", "-i", str(mix_input),
                               "-af", "loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json", "-ar", str(SR_TTS),
                               "-ac", "1", str(final_wav)], check=True, capture_output=True, text=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg_exe(), "-y", "-v", "error", "-i", str(video), "-i", str(final_wav),
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
               "-ar", "48000", "-metadata:s:a:0", "language=ara", "-t", f"{duration:.6f}"]
    if output.suffix.lower() == ".mp4":
        command.extend(["-movflags", "+faststart"])
    subprocess.run(command + [str(output)], check=True, capture_output=True)
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(final_wav), "-c:a", "libmp3lame",
                    "-b:a", "128k", str(output.with_suffix(".mp3"))], check=True, capture_output=True)
    output.with_suffix(".srt").write_text("\n\n".join(
        f"{i}\n{_srt_stamp(float(s['start']))} --> {_srt_stamp(float(s['end']))}\n{s['text']}"
        for i, s in enumerate(segments, 1)) + "\n", encoding="utf-8")
    meter = re.search(r'\{[^}]+"input_i"[^}]+\}', normalise.stderr, re.DOTALL)
    report = {
        "voice_backend": "agent", "voice_id": cues.get("voice_id"), "language": cues.get("language"),
        "video": str(video), "output": str(output), "source_duration": source_duration, "duration": duration,
        "status": "preview_incomplete" if duration < source_duration - 0.05 else "complete",
        "segments": len(placed), "mix_background": mix_background, "background_method": background_method,
        "video_codec": "copy", "target_lufs": -16, "speech_truncated": False,
        "loudness_normalization": json.loads(meter[0]) if meter else None, "placements": placed,
    }
    write_cues(output.with_suffix(".agent.json"), report)
    return output
