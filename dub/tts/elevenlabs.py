"""ElevenLabs cloud engine — instant voice cloning + multilingual TTS.

Requires an API key in the environment variable ELEVENLABS_API_KEY.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from . import register
from .base import TTSEngine

API = "https://api.elevenlabs.io/v1"


@register
class ElevenLabsEngine(TTSEngine):
    name = "elevenlabs"

    def __init__(self, api_key: str | None = None, model: str = "eleven_multilingual_v2"):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("set ELEVENLABS_API_KEY to use the elevenlabs engine")
        self.model = model
        self._voices: dict[str, str] = {}

    def _headers(self) -> dict:
        return {"xi-api-key": self.api_key}

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        """Instant Voice Clone from the reference sample."""
        if not reference_wav:
            raise ValueError("elevenlabs engine needs a reference wav per speaker")
        with open(reference_wav, "rb") as fh:
            resp = requests.post(
                f"{API}/voices/add",
                headers=self._headers(),
                data={"name": f"dub-{speaker}", "labels": "{}"},
                files={"files": (Path(reference_wav).name, fh, "audio/wav")},
                timeout=120,
            )
        resp.raise_for_status()
        voice_id = resp.json()["voice_id"]
        self._voices[speaker] = voice_id
        return voice_id

    # Style values that push the voice toward each emotion.
    # Higher style = more dramatic delivery; lower stability = more variance.
    _EMOTION = {
        "neutral": (0.55, 0.35, ""),
        "happy":   (0.35, 0.75, "[happy] "),
        "excited": (0.30, 0.85, "[excited] "),
        "sad":     (0.65, 0.45, "[sadly] "),
        "angry":   (0.40, 0.85, "[angry] "),
        "fearful": (0.55, 0.60, "[nervously] "),
    }

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0,
                   emotion: str = "neutral") -> Path:
        out_path = Path(out_path)
        stab, style, tag = self._EMOTION.get(emotion, self._EMOTION["neutral"])
        # v3 understands inline audio tags; v2 ignores them harmlessly
        prompt = f"{tag}{text}" if tag else text
        resp = requests.post(
            f"{API}/text-to-speech/{voice}",
            headers={**self._headers(), "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={
                "text": prompt,
                "model_id": self.model,
                "voice_settings": {
                    "stability": stab,
                    "similarity_boost": 0.8,
                    "style": style,
                    "use_speaker_boost": True,
                    "speed": max(0.7, min(speed, 1.2)),
                },
            },
            timeout=180,
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return out_path
