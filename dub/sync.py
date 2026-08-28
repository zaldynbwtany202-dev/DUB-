"""Dialogue-aware sync scheduler.

Each source line owns a *lip window* [start, end] (when the speaker's mouth
moves) followed by a *gap* until the next line. Professional dubbing may:

  1. place the line at its original start time (lip-sync on entry),
  2. stretch it with atempo within a natural limit (default 1.25x),
  3. overflow into the following silence gap (audience cannot see lips then),
  4. as a last resort, request re-synthesis at a faster speaking rate.

The scheduler returns, per segment: final placement, atempo factor, and an
optional speed hint when re-synthesis is recommended.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Segment


@dataclass
class Plan:
    placed_at: float
    stretch: float          # atempo factor to apply to the synthesized clip
    needs_resynth: bool     # True when even max_stretch cannot fit the window
    speed_hint: float       # suggested engine speed for re-synthesis
    overflow: float         # how far past the lip window the line will run


def schedule(
    segments: list[Segment],
    media_duration: float,
    max_stretch: float = 1.25,
    gap: float = 0.08,
    max_resynth_speed: float = 1.8,
) -> list[Plan]:
    """Compute placement for every segment. Mutates nothing; returns Plans."""
    plans: list[Plan] = []
    n = len(segments)
    for i, seg in enumerate(segments):
        next_start = segments[i + 1].start if i + 1 < n else media_duration
        hard_end = max(seg.end, next_start - gap)   # lip window + usable gap
        placed = seg.start
        available = hard_end - placed
        raw = seg.speech_dur or seg.window          # fall back to window size

        if raw <= available:
            stretch = 1.0
            needs, hint = False, 1.0
        else:
            needed = raw / available
            if needed <= max_stretch:
                stretch = needed
                needs, hint = False, 1.0
            else:
                # even max_stretch is not enough -> ask the TTS to speak faster
                stretch = max_stretch
                needs = True
                # choose an engine speed so the *re-synthesized* clip fits
                # with at most max_stretch additional atempo afterwards
                hint = min(needed / max_stretch * 1.05, max_resynth_speed)
        final_dur = raw / stretch
        overflow = max(0.0, placed + final_dur - seg.end)
        # NOTE: stretch is kept at full precision — rounding it would let the
        # clip overrun the hard end by a few microseconds and collide.
        plans.append(Plan(placed, stretch, needs, round(hint, 3),
                          round(overflow, 3)))
    return plans


def apply(plans: list[Plan], segments: list[Segment]) -> None:
    """Write the scheduling decision back into the segments."""
    for plan, seg in zip(plans, segments):
        seg.placed_at = plan.placed_at
        seg.stretch = plan.stretch
