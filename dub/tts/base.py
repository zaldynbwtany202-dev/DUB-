"""TTS engine interface.

An engine knows how to:
  1. prepare one voice per speaker (clone from a reference sample, or pick a preset)
  2. synthesize one line of text with that voice at a requested speaking rate
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSEngine(ABC):
    name: str = "base"

    @abstractmethod
    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        """Create/select a voice for ``speaker`` from a reference sample.

        Returns an opaque voice handle (voice id, preset name, or path)
        that ``synthesize`` understands.
        """

    @abstractmethod
    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0,
                   emotion: str = "neutral") -> Path:
        """Render ``text`` to ``out_path`` (wav/mp3). Returns the file path.

        ``emotion`` is one of: neutral, happy, sad, excited, angry, fearful.
        Engines that don't natively support emotion map it as best they can
        (ElevenLabs bumps ``style``; XTTS wraps the text in a marker; Minimax
        forwards ``voice_emotion``).
        """
