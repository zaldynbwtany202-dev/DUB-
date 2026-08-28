"""Coqui XTTS v2 — fully local voice cloning, 17 languages including Arabic.

This is the recommended engine for offline / self-hosted professional dubbing:
voice cloning needs only ~10 s of clean reference audio per speaker.
"""
from __future__ import annotations

from pathlib import Path

from . import register
from .base import TTSEngine


@register
class XTTSEngine(TTSEngine):
    name = "xtts"

    def __init__(self, model: str = "tts_models/multilingual/multi-dataset/xtts_v2",
                 device: str = "auto"):
        from TTS.api import TTS  # optional dependency (pip install TTS)
        import torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tts = TTS(model).to(device)
        self._refs: dict[str, str] = {}

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        if not reference_wav:
            raise ValueError("XTTS voice cloning requires a reference wav per speaker")
        self._refs[speaker] = str(reference_wav)
        return str(reference_wav)  # the handle IS the reference path

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0) -> Path:
        out_path = Path(out_path)
        kwargs = dict(text=text, speaker_wav=voice, language=language,
                      file_path=str(out_path))
        # `speed` exists only on newer Coqui releases — degrade gracefully
        try:
            self.tts.tts_to_file(**kwargs, speed=speed)
        except TypeError:
            self.tts.tts_to_file(**kwargs)
        return out_path
