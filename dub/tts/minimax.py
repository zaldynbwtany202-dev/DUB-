"""Minimax cloud engine via fal.ai — voice-clone + speech-2.8-hd.

Requires:
    pip install fal-client
    export FAL_KEY=...

Note: keep ``language_boost`` unset for Arabic — forcing it degrades output.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import register
from .base import TTSEngine


@register
class MinimaxEngine(TTSEngine):
    name = "minimax"

    def __init__(self, api_key: str | None = None):
        import fal_client  # optional dependency
        if api_key:
            os.environ["FAL_KEY"] = api_key
        if not os.environ.get("FAL_KEY"):
            raise RuntimeError("set FAL_KEY to use the minimax engine")
        self.fal = fal_client
        self._voices: dict[str, str] = {}

    def prepare_voice(self, speaker: str, reference_wav: str | Path | None) -> str:
        if not reference_wav:
            raise ValueError("minimax engine needs a reference wav per speaker (>= 10 s)")
        url = self.fal.upload_file(str(reference_wav))
        result = self.fal.subscribe(
            "fal-ai/minimax/voice-clone",
            arguments={"audio_url": url},
        )
        voice_id = result.get("voice_id") or result.get("custom_voice_id")
        if not voice_id:
            raise RuntimeError(f"minimax voice-clone returned no voice id: {result}")
        self._voices[speaker] = voice_id
        return voice_id

    def synthesize(self, text: str, voice: str, out_path: str | Path,
                   language: str, speed: float = 1.0) -> Path:
        out_path = Path(out_path)
        result = self.fal.subscribe(
            "fal-ai/minimax/speech-2.8-hd",
            arguments={
                "text": text,
                "voice_setting": {
                    "voice_id": voice,
                    "speed": max(0.5, min(speed, 2.0)),
                },
            },
        )
        audio_url = result["audio"]["url"]
        import requests
        resp = requests.get(audio_url, timeout=180)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return out_path
