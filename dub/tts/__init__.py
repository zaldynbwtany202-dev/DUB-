"""TTS engine registry."""
from __future__ import annotations

from .base import TTSEngine

_ENGINES: dict[str, type[TTSEngine]] = {}


def register(cls: type[TTSEngine]) -> type[TTSEngine]:
    _ENGINES[cls.name] = cls
    return cls


def _import_all() -> None:
    """Import every optional engine; each is best-effort — a missing extra
    keeps its engine out of the registry without crashing the CLI."""
    for mod in ("xtts", "elevenlabs", "minimax", "piper", "chatterbox", "f5"):
        try:
            __import__(f"{__name__}.{mod}", fromlist=["*"])
        except Exception:
            pass


def get_engine(name: str, **kwargs) -> TTSEngine:
    _import_all()
    if name not in _ENGINES:
        raise ValueError(
            f"unknown TTS engine '{name}'. Available: {', '.join(sorted(_ENGINES))}"
        )
    return _ENGINES[name](**kwargs)


def available() -> list[str]:
    _import_all()
    return sorted(_ENGINES)
