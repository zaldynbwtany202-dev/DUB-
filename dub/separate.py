"""Vocals / background stem separation.

Primary backend: demucs (local, open source).
Fallback: the original mix is reused as background (voice-over style dub).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def separate(audio: str | Path, workdir: str | Path,
             model: str = "htdemucs") -> tuple[Path, Path]:
    """Split ``audio`` into (vocals, background).

    Returns (vocals_path, background_path).
    """
    audio = Path(audio)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if shutil.which("demucs") is None:
        raise RuntimeError(
            "demucs is not installed. Install it with: pip install demucs\n"
            "Or run with --no-separate to dub over the ducked original audio."
        )

    out_root = workdir / "demucs_out"
    subprocess.run(
        ["demucs", "--two-stems", "vocals", "-n", model,
         "-o", str(out_root), str(audio)],
        check=True,
    )
    stem_dir = out_root / model / audio.stem
    vocals = stem_dir / "vocals.wav"
    background = stem_dir / "no_vocals.wav"
    if not vocals.exists() or not background.exists():
        raise RuntimeError(f"demucs output missing under {stem_dir}")

    # move to stable locations
    v_final = workdir / "vocals.wav"
    b_final = workdir / "background.wav"
    shutil.move(str(vocals), v_final)
    shutil.move(str(background), b_final)
    return v_final, b_final


def duck_original(audio: str | Path, out: str | Path, volume: float = 0.15) -> Path:
    """Fallback background: the original mix strongly ducked (voice-over style)."""
    from .media import run
    out = Path(out)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(audio),
         "-af", f"volume={volume}", str(out)])
    return out
