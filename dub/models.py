"""Shared data models for the dubbing pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    """One line of dialogue in the source video."""
    id: int
    start: float                    # seconds, absolute in source media
    end: float
    text_src: str
    speaker: str = "S1"             # speaker label (S1, S2, ...)
    text_tgt: str = ""              # translated text
    audio_path: Optional[str] = None  # synthesized clip for this line
    speech_dur: float = 0.0         # measured speech duration of the clip (after silence trim)
    stretch: float = 1.0            # final atempo factor chosen by the scheduler
    placed_at: float = 0.0          # final placement time chosen by the scheduler

    @property
    def window(self) -> float:
        return self.end - self.start


@dataclass
class Project:
    """Serializable pipeline state — enables resume after any stage."""
    src_video: str
    src_lang: str
    tgt_lang: str
    duration: float = 0.0
    vocals_path: Optional[str] = None      # isolated speech stem
    background_path: Optional[str] = None  # residual (music / crowd / SFX)
    speaker_refs: dict = field(default_factory=dict)   # speaker label -> clean sample wav
    voice_ids: dict = field(default_factory=dict)      # speaker label -> cloned voice id
    segments: list = field(default_factory=list)       # list[Segment]

    # ---------- persistence ----------
    def save(self, path: str | Path) -> None:
        data = asdict(self)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["segments"] = [Segment(**s) for s in data.get("segments", [])]
        return cls(**data)
