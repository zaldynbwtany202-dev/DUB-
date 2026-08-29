"""Piper TTS — fast local neural TTS, Apache 2.0, many languages incl. Arabic.

Piper produces **preset** voices (no per-speaker cloning), so it can't mimic
the original speaker — but the voices are natural and it synthesizes in
real-time on CPU. Use it when narration matters more than identity, or when
you need a fully-libre pipeline (XTTS is CPML — non-commercial only).

Voice models: https://huggingface.co/rhasspy/piper-voices
Point ``PIPER_MODEL`` at a downloaded ``.onnx`` file, or pass ``model_path``.
"""
from __future__ import annotations

import os
import urllib.request
import wave
from pathlib import Path

from . import register
from .base import TTSEngine

# Curated defaults per target language. Change with ``PIPER_MODEL``.
_DEFAULTS = {
    "ar": ("ar", "ar_JO", "kareem", "medium"),
    "en": ("en", "en_US", "lessac", "medium"),
    "fr": ("fr", "fr_FR", "siwis", "medium"),
    "es": ("es", "es_ES", "davefx", "medium"),
    "de": ("de", "de_DE", "thorsten", "medium"),
    "it": ("it", "it_IT", "riccardo", "x_low"),
    "pt": ("pt", "pt_BR", "faber", "medium"),
    "tr": ("tr", "tr_TR", "dfki", "medium"),
    "ru": ("ru", "ru_RU", "denis", "medium"),
    "nl": ("nl", "nl_BE", "nathalie", "medium"),
    "zh": ("zh", "zh_CN", "huayan", "medium"),
}

_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _ensure_model(lang: str) -> str:
    """Download the default voice for ``lang`` into ~/.cache/dub/piper."""
    if lang not in _DEFAULTS:
        raise ValueError(
            f"no default piper voice registered for '{lang}'. "
            f"Set PIPER_MODEL to a downloaded .onnx from {_HF}"
        )
    lg, loc, name, quality = _DEFAULTS[lang]
    fname = f"{loc}-{name}-{quality}.onnx"
    cache = Path.home() / ".cache" / "dub" / "piper"
    cache.mkdir(parents=True, exist_ok=True)
    onnx, meta = cache / fname, cache / (fname + ".json")
    base = f"{_HF}/{lg}/{loc}/{name}/{quality}/{fname}"
    if not onnx.exists():
        urllib.request.urlretrieve(base, onnx)
        urllib.request.urlretrieve(base + ".json", meta)
    return str(onnx)


@register
class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self, model_path: str | None = None):
        from piper.voice import PiperVoice  # pip install piper-tts
        self._PiperVoice = PiperVoice
        self._model_path = model_path or os.environ.get("PIPER_MODEL")
        self._voice = None
        self._loaded_for_lang: str | None = None

    def _load(self, language: str) -> None:
        path = self._model_path or _ensure_model(language)
        if self._loaded_for_lang != path:
            self._voice = self._PiperVoice.load(path)
            self._loaded_for_lang = path

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        # piper has no cloning — the "voice" is just the speaker label; the
        # actual model is chosen from the target language at synthesis time.
        return speaker

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0,
                   emotion: str = "neutral") -> Path:
        self._load(language)
        out_path = Path(out_path)
        # length_scale: piper >1 slows down, <1 speeds up  (inverse of atempo)
        length_scale = max(0.5, min(1.0 / speed, 2.0))
        with wave.open(str(out_path), "wb") as wav:
            self._voice.synthesize(text, wav, length_scale=length_scale)
        return out_path
