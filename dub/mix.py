"""Assemble the final dubbed audio and mux it onto the video.

Every synthesized line is time-stretched (atempo) to its scheduled factor,
delayed to its placement time, mixed with the other lines, then blended with
the background stem (music / crowd / SFX) under loudness normalization.
"""
from __future__ import annotations

from pathlib import Path

from . import media
from .models import Segment


def build_audio(
    segments: list[Segment],
    background: str | Path,
    out_wav: str | Path,
    duration: float,
    bg_volume: float = 0.5,
    voice_volume: float = 1.35,
    target_lufs: int = -16,
) -> Path:
    """Render the full-length dubbed audio track."""
    out_wav = Path(out_wav)
    segs = [s for s in segments if s.audio_path]
    if not segs:
        raise ValueError("no synthesized segments to mix")

    inputs: list[str] = []
    for s in segs:
        inputs += ["-i", str(s.audio_path)]
    inputs += ["-i", str(background)]
    bg_index = len(segs)

    filters: list[str] = []
    labels: list[str] = []
    for i, s in enumerate(segs):
        chain = []
        if abs(s.stretch - 1.0) > 1e-3:
            chain.append(media.atempo_chain(s.stretch))
        # short fade in/out keeps line transitions smooth — no clicks
        chain.append("afade=t=in:st=0:d=0.03,afade=t=out:st=0:d=0.03:curve=exp")
        delay_ms = int(round(s.placed_at * 1000))
        chain.append(f"adelay={delay_ms}|{delay_ms}")
        filters.append(f"[{i}:a]{','.join(chain)}[a{i}]")
        labels.append(f"[a{i}]")

    filters.append(
        f"{''.join(labels)}amix=inputs={len(segs)}:normalize=0,"
        f"volume={voice_volume}[voice]"
    )
    filters.append(
        f"[{bg_index}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume={bg_volume}[bg]"
    )
    filters.append(
        f"[voice][bg]amix=inputs=2:normalize=0,"
        f"alimiter=limit=0.95,"
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11[out]"
    )

    media.run(["ffmpeg", "-y", "-v", "error", *inputs,
               "-filter_complex", ";".join(filters),
               "-map", "[out]", "-t", f"{duration:.3f}", str(out_wav)])
    return out_wav


def render(video: str | Path, segments: list[Segment], background: str | Path,
           out_video: str | Path, workdir: str | Path,
           bg_volume: float = 0.5) -> Path:
    """Full final step: build audio + mux onto the source video."""
    duration = media.probe_duration(video)
    wav = build_audio(segments, background, Path(workdir) / "dub_track.wav",
                      duration, bg_volume=bg_volume)
    return media.mux(video, wav, out_video)
