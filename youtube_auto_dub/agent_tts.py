"""Place agent-generated speech on the source timeline.

The Arena agent synthesizes each line (generate_speech). This module never
calls Edge-TTS, VoxCPM, or XTTS. It only:

1. reads a cue sheet of timed lines + audio files
2. tempo-fits each take into its window
3. mixes an optional music/effects bed
4. muxes the result onto the source picture
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from youtube_auto_dub.audio import align_segments, finalize_audio, mux_video
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
from youtube_auto_dub.models import SR_TTS


def load_cues(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("cue sheet must be a JSON object")
    segs = data.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError("cue sheet needs a non-empty segments list")
    return data


def write_cues(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def cues_from_segments(
    video: Path,
    segments: list[dict[str, Any]],
    *,
    voice_id: str = "voice-00",
    language: str = "ar-EG",
    audio_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a cue sheet the agent fills by writing ``audio`` files."""
    audio_dir = Path(audio_dir or "agent_voice")
    out_segs = []
    for i, raw in enumerate(segments):
        start = float(raw["start"])
        end = float(raw["end"])
        text = str(raw.get("text") or raw.get("translated_text") or raw.get("source_text") or "").strip()
        if not text or end <= start:
            continue
        out_segs.append({
            "index": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "audio": str(audio_dir / f"seg-{i:04d}.mp3"),
            "speaker": raw.get("speaker") or "SPEAKER_00",
        })
    return {
        "video": str(video),
        "voice_backend": "agent",
        "voice_id": voice_id,
        "language": language,
        "segments": out_segs,
    }


def _to_wav(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_exe(), "-y", "-i", str(src), "-ar", str(SR_TTS), "-ac", "1", str(dest)],
        check=True, capture_output=True,
    )
    if not dest.exists() or dest.stat().st_size < 64:
        raise RuntimeError(f"empty agent take: {src}")
    return dest


def probe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(res.stdout.strip())


def assemble(
    cues: dict[str, Any],
    *,
    work_dir: Path,
    output: Path,
    mix_background: bool = True,
    video: Path | None = None,
) -> Path:
    """Sync agent takes to the picture. Does not generate speech."""
    video = Path(video or cues["video"])
    if not video.exists():
        raise FileNotFoundError(video)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)
    info: list[dict[str, Any]] = []
    missing: list[str] = []
    for seg in cues["segments"]:
        audio = Path(seg["audio"])
        if not audio.is_absolute():
            # relative to cue sheet later; caller may pass cwd-relative
            audio = Path(seg["audio"])
        if not audio.exists():
            missing.append(str(audio))
            continue
        wav = _to_wav(audio, work_dir / f"seg-{int(seg.get('index', len(info))):04d}.wav")
        start = float(seg["start"])
        end = float(seg["end"])
        info.append({
            "start": start,
            "target_dur": max(0.12, end - start),
            "wav_path": wav,
            "text": seg.get("text", ""),
        })
    if missing:
        raise FileNotFoundError("agent takes missing: " + ", ".join(missing))
    if not info:
        raise RuntimeError("no agent takes to place on the timeline")
    aligned = work_dir / "aligned.wav"
    align_segments(info, duration, aligned)
    final_wav = work_dir / "final.wav"
    finalize_audio(
        aligned,
        video,
        final_wav,
        match_loudness=True,
        mix_ambient=mix_background,
        stereo_source=video,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    mux_video(video, final_wav, output)
    report = {
        "voice_backend": "agent",
        "video": str(video),
        "output": str(output),
        "segments": len(info),
        "duration": duration,
        "mix_background": mix_background,
    }
    output.with_suffix(".agent.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output
