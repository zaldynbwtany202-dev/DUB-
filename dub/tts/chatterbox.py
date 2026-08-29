"""Chatterbox TTS by Resemble AI — MIT-licensed voice cloning, high quality.

Cloning from ~6-10 s of reference audio. MIT license makes it the recommended
free choice for **commercial** dubbing (XTTS is non-commercial CPML). Install:

    pip install chatterbox-tts
"""
from __future__ import annotations

from pathlib import Path

import torchaudio

from . import register
from .base import TTSEngine


@register
class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self, device: str = "auto"):
        from chatterbox.tts import ChatterboxTTS  # pip install chatterbox-tts
        import torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = ChatterboxTTS.from_pretrained(device=device)
        self._refs: dict[str, str] = {}

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        if not reference_wav:
            raise ValueError("chatterbox needs a reference wav per speaker")
        self._refs[speaker] = str(reference_wav)
        return str(reference_wav)  # handle IS the reference path

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0,
                   emotion: str = "neutral") -> Path:
        # Chatterbox exposes CFG-weight / exaggeration — turn them up for
        # excited/angry, down for sad, so a single voice covers all emotions.
        exagg = {"neutral": 0.5, "happy": 0.7, "excited": 0.85,
                 "sad": 0.35, "angry": 0.85, "fearful": 0.6}.get(emotion, 0.5)
        cfg = {"neutral": 0.5, "happy": 0.5, "excited": 0.35,
               "sad": 0.6, "angry": 0.35, "fearful": 0.5}.get(emotion, 0.5)
        wav = self.model.generate(text, audio_prompt_path=voice,
                                  exaggeration=exagg, cfg_weight=cfg)
        out_path = Path(out_path)
        torchaudio.save(str(out_path), wav, self.model.sr)
        return out_path
