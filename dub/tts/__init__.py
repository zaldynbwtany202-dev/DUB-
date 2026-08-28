"""TTS engine registry."""
from __future__ import annotations

from .base import TTSEngine

_ENGINES: dict[str, type[TTSEngine]] = {}


def register(cls: type[TTSEngine]) -> type[TTSEngine]:
    _ENGINES[cls.name] = cls
    return cls


def get_engine(name: str, **kwargs) -> TTSEngine:
    # imports register the engines
    from . import xtts, elevenlabs, minimax  # noqa: F401
    if name not in _ENGINES:
        raise ValueError(
            f"unknown TTS engine '{name}'. Available: {', '.join(sorted(_ENGINES))}"
        )
    return _ENGINES[name](**kwargs)


def available() -> list[str]:
    from . import xtts, elevenlabs, minimax  # noqa: F401
    return sorted(_ENGINES)
