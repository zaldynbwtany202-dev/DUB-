"""Redistribute measured pauses inside complete agent takes so the dub follows the
original speech timeline.

The narration of a recap video is close to continuous. Placing a whole take at the
start of its window therefore leaves silence exactly where the source is still
speaking, which is what a viewer reads as a "gap". This module never changes the
recorded speech itself: it moves whole spoken chunks in time and edits only the
silence between them.

Rules enforced here:

* chunks are cut strictly inside measured silence, so no syllable is clipped;
* speech never overlaps speech;
* a part may borrow time from the following part instead of being cut;
* gap length stays inside a bounded window so the reading still sounds human.

Everything in this module is pure: numbers go in, numbers come out, so the timing
policy is unit-testable without FFmpeg or a speech model.
"""
from __future__ import annotations

import math
from typing import Sequence

__all__ = ["plan_gaps", "allocate_part_spans", "cue_times", "align_sentences",
           "overlap_seconds", "coverage"]


def plan_gaps(recorded: Sequence[float], surplus: float, *, min_gap: float = 0.1,
              max_gap: float = 0.85) -> list[float]:
    """Distribute ``surplus`` seconds of silence across the gaps, keeping the rhythm.

    Longer recorded pauses stay longer: every gap is multiplied by one factor and the
    result is clamped into ``[min_gap, max_gap]``. Bisection is used because the sum is
    monotone in that factor, so the part still lands exactly on its target span.
    """
    gaps = [max(0.0, float(g)) for g in recorded]
    count = len(gaps)
    if count == 0:
        return []
    surplus = float(surplus)
    if surplus <= min_gap * count:  # not enough room: even, shortest legal pauses
        base = surplus / count if surplus > 0 else min_gap
        return [round(min(max(base, 0.02), max_gap), 6)] * count
    if surplus >= max_gap * count:  # too much time to hide: spread evenly anyway
        return [round(surplus / count, 6)] * count
    total = sum(gaps)
    if total <= 1e-6:  # a take without measured pauses
        return [round(surplus / count, 6)] * count

    def trial(scale: float) -> list[float]:
        return [min(max_gap, max(min_gap, gap * scale)) for gap in gaps]

    low, high = 0.0, max(surplus / total, 1.0) * 2.0
    for _ in range(60):
        middle = (low + high) / 2
        if sum(trial(middle)) < surplus:
            low = middle
        else:
            high = middle
    result = trial((low + high) / 2)
    drift = surplus - sum(result)
    if abs(drift) > 1e-6:  # absorb rounding on the longest gaps first
        for index in sorted(range(count), key=lambda i: -result[i]):
            room = (max_gap - result[index]) if drift > 0 else (result[index] - min_gap)
            if room <= 0:
                continue
            step = min(abs(drift), room)
            result[index] += step if drift > 0 else -step
            drift = surplus - sum(result)
            if abs(drift) < 1e-9:
                break
    return [round(value, 6) for value in result]


def allocate_part_spans(windows: Sequence[float], min_spans: Sequence[float],
                        max_spans: Sequence[float], total: float) -> list[float]:
    """Give every part a span that fits the timeline, letting tight parts borrow.

    ``windows`` are the reviewed source windows. A part whose recording is longer than
    its window pushes the following parts instead of being truncated or sped up.
    """
    if not (len(windows) == len(min_spans) == len(max_spans)) or not windows:
        raise ValueError("part arrays must be the same non-empty length")
    if any(maximum < minimum for minimum, maximum in zip(min_spans, max_spans)):
        raise ValueError("a part cannot have a minimum span above its maximum")
    wanted = [float(window) for window in windows]
    wanted[-1] += float(total) - sum(wanted)  # the last part absorbs rounding
    if any(window <= 0 for window in wanted):
        raise ValueError("every part needs positive time")
    if sum(min_spans) > total + 1e-6:
        raise ValueError(f"recordings need {sum(min_spans):.2f}s but only {total:.2f}s exists")
    spans: list[float] = []
    carry = 0.0
    for window, minimum, maximum in zip(wanted, min_spans, max_spans):
        target = window + carry
        chosen = min(max(target, minimum), maximum)
        spans.append(round(chosen, 6))
        carry = target - chosen
    if abs(sum(spans) - total) > 1e-3:
        raise ValueError(f"allocated spans miss the duration by {total - sum(spans):.3f}s")
    return spans


def cue_times(span_start: float, chunk_durations: Sequence[float], gaps: Sequence[float],
              lead: float = 0.02) -> list[dict[str, float]]:
    """Absolute in/out times for every chunk of one part."""
    times: list[dict[str, float]] = []
    cursor = float(span_start) + float(lead)
    for index, duration in enumerate(chunk_durations):
        if duration <= 0:
            raise ValueError("chunk duration must be positive")
        gap = float(gaps[index]) if index < len(gaps) else 0.0
        times.append({"start": round(cursor, 6), "end": round(cursor + duration, 6)})
        cursor += duration + gap
    return times


def align_sentences(sentences: Sequence[str], chunk_durations: Sequence[float]) -> list[str]:
    """Attach whole sentences to recorded chunks by cumulative proportion.

    Sentences are never split across cues, which keeps the generated subtitle readable
    even though the speech model pauses more often than there are periods. Chunks that
    receive no sentence merge into the previous cue.
    """
    count = len(chunk_durations)
    if count == 0:
        return []
    rows = [str(sentence).strip() for sentence in sentences if str(sentence).strip()]
    if not rows:
        return [""] * count
    weights = [max(0.0, float(duration)) for duration in chunk_durations]
    cumulative = 0.0
    boundaries = []
    for weight in weights:
        cumulative += weight
        boundaries.append(cumulative)
    total_weight = cumulative or 1.0
    boundaries = [value / total_weight for value in boundaries[:-1]]
    char_total = sum(len(sentence) for sentence in rows) or 1
    assigned: list[list[str]] = [[] for _ in range(count)]
    position = 0.0
    last_used = 0
    for sentence in rows:
        middle = (position + len(sentence) / 2) / char_total
        position += len(sentence)
        index = sum(1 for boundary in boundaries if boundary <= middle)
        index = min(max(index, last_used), count - 1)  # strictly forward only
        assigned[index].append(sentence)
        last_used = index
    merged: list[str] = []
    pending: list[str] = []
    for chunk_rows in assigned:
        pending.extend(chunk_rows)
        if chunk_rows:
            merged.append(" ".join(pending).strip())
            pending = []
    merged += [""] * (count - len(merged))
    if pending:
        tail = max((index for index, text in enumerate(merged) if text), default=0)
        merged[tail] = " ".join([merged[tail], *pending]).strip()
    return merged[:count]


def overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def coverage(source_spans: Sequence[Sequence[float]], dub_spans: Sequence[Sequence[float]],
             duration: float, step: float = 0.01, tolerance: float = 0.15,
             min_gap: float = 0.3) -> dict:
    """Coverage of original speech by dubbed speech on a shared 10 ms grid."""
    size = max(1, math.ceil(float(duration) / step))
    source = bytearray(size)
    dub = bytearray(size)

    def paint(target: bytearray, spans: Sequence[Sequence[float]]) -> None:
        for start, end in spans:
            low = max(0, int(float(start) / step))
            high = min(size, int(math.ceil(float(end) / step)))
            for index in range(low, high):
                target[index] = 1

    paint(source, source_spans)
    paint(dub, dub_spans)
    radius = int(tolerance / step)
    tolerant = bytearray(size)
    for index in range(size):
        if dub[index]:
            for shift in range(-radius, radius + 1):
                neighbour = index + shift
                if 0 <= neighbour < size:
                    tolerant[neighbour] = 1
    runs: list[list[float]] = []
    for index in range(size):
        if source[index] and not tolerant[index]:
            if runs and runs[-1][1] == index:
                runs[-1][1] = index + 1
            else:
                runs.append([index, index + 1])
    gaps = [{"start": round(low * step, 3), "end": round(high * step, 3),
             "duration": round((high - low) * step, 3)} for low, high in runs
            if (high - low) * step >= min_gap]
    source_total = sum(source) * step
    uncovered = sum(high - low for low, high in runs) * step
    return {
        "source_speech_seconds": round(source_total, 3),
        "dubbed_speech_seconds": round(sum(dub) * step, 3),
        "uncovered_source_speech_seconds": round(uncovered, 3),
        "source_speech_coverage": round(1 - uncovered / source_total, 6) if source_total else 1.0,
        "max_uncovered_gap": max((gap["duration"] for gap in gaps), default=0.0),
        "gap_count": len(gaps),
        "gaps": sorted(gaps, key=lambda gap: -gap["duration"])[:25],
        "boundary_tolerance_seconds": tolerance,
        "minimum_reported_gap_seconds": min_gap,
    }
