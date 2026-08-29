"""F5-TTS — state-of-the-art zero-shot voice cloning from a short reference.

Very natural prosody and identity match. License: **CC-BY-NC 4.0**
(non-commercial), so use it for research / personal projects. Install:

    pip install f5-tts
"""
from __future__ import annotations

from pathlib import Path

import soundfile as sf

from . import register
from .base import TTSEngine


@register
class F5Engine(TTSEngine):
    name = "f5"

    def __init__(self, model: str = "F5-TTS"):
        from f5_tts.api import F5TTS  # pip install f5-tts
        self.tts = F5TTS(model_type=model)
        self._refs: dict[str, tuple[str, str]] = {}

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        if not reference_wav:
            raise ValueError("f5 needs a reference wav per speaker (~10 s)")
        # F5 works best with a short reference *transcript* too — leaving it
        # blank invokes automatic transcription inside F5. The handle we
        # return is just the reference path.
        self._refs[speaker] = (str(reference_wav), "")
        return str(reference_wav)

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0,
                   emotion: str = "neutral") -> Path:
        out_path = Path(out_path)
        wav, sr, _ = self.tts.infer(
            ref_file=voice, ref_text="",
            gen_text=text, speed=max(0.5, min(speed, 2.0)),
            remove_silence=True,
        )
        sf.write(str(out_path), wav, sr)
        return out_path
