"""Place dubbed speech islands on original soundtrack islands.

Speech is never slowed and never cut. Extra silence inside a take is dropped
so the words can cover the original utterance. Gaps between islands follow
the original narrator's pauses.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def speech_islands(
    audio: np.ndarray,
    sr: int,
    *,
    floor_db: float = -40.0,
    min_dur: float = 0.12,
    merge_gap: float = 0.18,
    end_margin: float = 0.0,
) -> list[tuple[int, int]]:
    """Return [begin, end) sample index pairs for audible speech."""
    if audio.size < int(sr * min_dur):
        return []
    peak = float(np.max(np.abs(audio)) or 0.0)
    if peak < 1e-5:
        return []
    frame = max(1, int(sr * 0.01))
    pad = (-len(audio)) % frame
    rms = np.sqrt(np.mean(np.pad(audio, (0, pad)).reshape(-1, frame) ** 2, axis=1) + 1e-12)
    active = rms > max(1e-5, peak * 10 ** (floor_db / 20.0))
    raw: list[list[int]] = []
    inside = False
    begin = 0
    for i, on in enumerate(active):
        if on and not inside:
            begin = i * frame
            inside = True
        elif not on and inside:
            raw.append([begin, i * frame])
            inside = False
    if inside:
        raw.append([begin, len(audio)])
    merged: list[list[int]] = []
    merge_samples = int(merge_gap * sr)
    min_samples = int(min_dur * sr)
    for start, end in raw:
        if merged and start - merged[-1][1] < merge_samples:
            merged[-1][1] = end
        elif end - start >= min_samples:
            merged.append([start, min(end, len(audio))])
    pad = int(max(0.0, end_margin) * sr)
    return [(a, min(b + pad, len(audio))) for a, b in merged if b > a]


def _tempo_fit(length: int, room: int, *, min_tempo: float, max_tempo: float, sr: int) -> tuple[int, float]:
    if room <= 0:
        raise ValueError("no room for speech; rewrite the line")
    natural = length / sr
    budget = room / sr
    needed = natural / max(budget, 1e-6)
    if needed > max_tempo:
        raise ValueError(f"speech needs {needed:.3f}x; shorten/re-record (cap {max_tempo})")
    tempo = min_tempo if needed <= min_tempo else needed
    return int(round(length / tempo)), float(tempo)


def layout_islands(
    dub: np.ndarray,
    original: np.ndarray,
    sr: int,
    *,
    min_tempo: float = 1.0,
    max_tempo: float = 1.08,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Lay dubbed islands onto the original window. Duration matches ``original``."""
    if min_tempo < 1.0:
        raise ValueError("refusing to slow speech; rewrite the line instead")
    dub_islands = speech_islands(dub, sr)
    orig_islands = speech_islands(original, sr, floor_db=-36.0)
    if not dub_islands:
        raise ValueError("dub take has no audible speech")
    window = len(original)
    chunks = [dub[a:b] for a, b in dub_islands]
    speech_len = sum(len(c) for c in chunks)
    fitted_len, tempo = _tempo_fit(speech_len, max(window - int(sr * 0.04), 1), min_tempo=min_tempo, max_tempo=max_tempo, sr=sr)
    scale = fitted_len / max(speech_len, 1)
    scaled = []
    for chunk in chunks:
        new_len = max(1, int(round(len(chunk) * scale)))
        x = np.arange(new_len) * (len(chunk) / new_len)
        idx = np.clip(x.astype(int), 0, len(chunk) - 1)
        scaled.append(chunk[idx].astype(np.float32))

    starts: list[int] = []
    if orig_islands:
        for i, chunk in enumerate(scaled):
            if i < len(orig_islands):
                starts.append(orig_islands[i][0])
            else:
                starts.append(starts[-1] + len(scaled[i - 1]) + int(0.04 * sr))
    else:
        cursor = 0
        for chunk in scaled:
            starts.append(cursor)
            cursor += len(chunk) + int(0.05 * sr)

    # Push overlaps forward; never overlap spoken samples.
    for i in range(1, len(starts)):
        min_start = starts[i - 1] + len(scaled[i - 1])
        if starts[i] < min_start:
            starts[i] = min_start
    last_end = starts[-1] + len(scaled[-1])
    if last_end > window:
        shift = last_end - window
        starts = [max(0, s - shift) for s in starts]
        for i in range(1, len(starts)):
            min_start = starts[i - 1] + len(scaled[i - 1])
            if starts[i] < min_start:
                starts[i] = min_start
        if starts[-1] + len(scaled[-1]) > window:
            raise ValueError("fitted speech still overruns the original window; refusing to truncate")

    out = np.zeros(window, dtype=np.float32)
    placed = []
    for start, chunk in zip(starts, scaled):
        end = start + len(chunk)
        out[start:end] += chunk
        placed.append({"start": round(start / sr, 3), "end": round(end / sr, 3)})
    fade = min(int(sr * 0.004), max(1, window // 8))
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out, {
        "tempo": tempo,
        "slowed": tempo < 0.999,
        "speech_truncated": False,
        "dub_islands": len(dub_islands),
        "original_islands": len(orig_islands),
        "placements": placed,
        "speech_seconds": round(sum(len(c) for c in scaled) / sr, 3),
    }
