"""Transcription with word-level timestamps (faster-whisper)."""
from __future__ import annotations

from pathlib import Path

from .models import Segment, Word


def transcribe(
    audio: str | Path,
    language: str | None = None,
    model_size: str = "small",
    device: str = "auto",
    min_segment_gap: float = 1.2,
) -> tuple[list[Segment], str]:
    """Transcribe and group words into dialogue lines.

    Words are merged into a Segment until a pause longer than
    ``min_segment_gap`` is hit — each Segment is one dubbing unit whose
    [start, end] window the scheduler will respect.

    Returns (segments, detected_language).
    """
    from faster_whisper import WhisperModel  # optional dependency

    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    model = WhisperModel(model_size, device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    seg_iter, info = model.transcribe(
        str(audio), language=language, word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
    )

    words: list[Word] = []
    for seg in seg_iter:
        for w in (seg.words or []):
            words.append(Word(start=w.start, end=w.end, text=w.word.strip()))

    segments = _words_to_segments(words, min_segment_gap)
    return segments, info.language


def _words_to_segments(words: list[Word], max_gap: float) -> list[Segment]:
    if not words:
        return []
    segments: list[Segment] = []
    cur: list[Word] = [words[0]]
    for prev, w in zip(words, words[1:]):
        if w.start - prev.end > max_gap:
            segments.append(_make_segment(len(segments), cur))
            cur = [w]
        else:
            cur.append(w)
    segments.append(_make_segment(len(segments), cur))
    return segments


def _make_segment(idx: int, words: list[Word]) -> Segment:
    return Segment(
        id=idx,
        start=round(words[0].start, 3),
        end=round(words[-1].end, 3),
        text_src=" ".join(w.text for w in words),
    )


def assign_speakers(segments: list[Segment], mapping: dict[str, str] | None = None,
                    n_speakers: int = 2) -> None:
    """Assign a speaker label to every segment.

    Without a diarization model this uses alternating turns (the common
    case for dialogue). ``mapping`` can force specific segment ids:
    {"0": "S2", "3": "S1"}.
    """
    mapping = mapping or {}
    turn = 0
    for seg in segments:
        if str(seg.id) in mapping:
            seg.speaker = mapping[str(seg.id)]
            turn = int(seg.speaker.lstrip("S") or 1) - 1
        else:
            seg.speaker = f"S{turn % n_speakers + 1}"
            turn += 1
