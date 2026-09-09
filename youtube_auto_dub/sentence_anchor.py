"""Monotone sentence-to-source anchoring for narration that must follow a picture.

The dub script is a rewriting of the same narration that is audible in the source, so
the *order* of sentences is shared even when the wording is not (a literal token match
between an Egyptian rewrite and a transcription is only ~35 %, which is far too weak to
anchor on). Sentences are therefore paired by rank: :func:`source_units` segments the
original into as many speech units as the dub has clauses, cutting on the original's own
pauses where it has them, and :func:`spread_gaps` moves the dub's silence so that each
clause starts as near as possible to the matching unit while the part still covers its
window. Only silence moves: measured speech lengths are never changed.

Three properties a listener notices are kept by construction:

* a clause never starts before the previous one finished (plus a minimum breath);
* speech is never cut or stretched to make an anchor - only silence moves;
* no clause idles while the original is still talking, so the picture is never silent.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Sequence

__all__ = ["split_sentences", "source_units", "spread_gaps", "anchor_errors"]

# Arabic narration in this script is separated by "،" as often as by a full stop, and the
# unit the listener hears is the clause, so clause ends are sentence ends for anchoring.
SENTENCE_END = re.compile(r"(?<=[.!?؟،؛:])\s+")
DIACRITICS = re.compile(r"[\u064B-\u0652\u0640\u0670]")
TRANSLIT = str.maketrans("أإآىةؤئ", "ااايهوي")


def normalize(text: str) -> str:
    text = DIACRITICS.sub("", text or "").translate(TRANSLIT)
    return re.sub(r"[^\u0621-\u064aA-Za-z0-9]", "", text)


def token_count(text: str) -> int:
    """Words in a clause, used to keep the pairing honest when texts differ in length."""
    return len([token for token in normalize(text).split() if token]) or 1


def split_sentences(text: str) -> list[str]:
    rows = []
    for row in SENTENCE_END.split((text or "").strip()):
        row = row.strip().strip(" ،.!?؟،؛—–-")
        if len(normalize(row)) >= 2:
            rows.append(row)
    return rows


def source_units(words: Sequence[dict], count: int, start: float, end: float,
                 min_gap: float = 0.08) -> list[dict[str, float]]:
    """Cut the original speech of ``[start, end)`` into ``count`` consecutive units.

    Each boundary is the largest pause inside a locality window around its ideal word
    rank, so a uniform-pace narrator still gets an even split while a narrator who
    actually breathes gets a cut on the breath. Units stay monotone, non-empty, and
    cover the whole window.
    """
    inside = [w for w in words if start <= float(w["start"]) < end]
    if count <= 0 or not inside:
        return []
    total = len(inside)
    count = min(count, total)
    gaps = [min_gap] + [max(min_gap, float(inside[i + 1]["start"]) - float(inside[i]["end"]))
                        for i in range(total - 1)] + [min_gap]
    ideal = total / count
    cuts: list[int] = []
    for rank in range(1, count):
        low = max((cuts[-1] + 1) if cuts else 1, int(round(rank * ideal - ideal / 2)))
        high = min(total - 1, int(round(rank * ideal + ideal / 2)))
        if high < low:
            continue
        # comparing pauses to millisecond resolution keeps a float-tie from beating the
        # locality term, so an evenly spoken passage splits evenly instead of bunching up
        best = max(range(low, high + 1),
                   key=lambda index: (round(gaps[index], 3), -abs(index - rank * ideal)))
        if not cuts or best > cuts[-1]:
            cuts.append(best)
    bounds = [0, *cuts, total]
    units = []
    for index, (low, high) in enumerate(zip(bounds, bounds[1:])):
        first, last = inside[low], inside[high - 1]
        next_start = float(inside[high]["start"]) if high < total else end
        units.append({"index": index, "start": round(float(first["start"]), 3),
                      "end": round(max(float(last["end"]), float(first["start"]) + .05), 3),
                      "next": round(next_start, 3), "words": high - low,
                      "gap": round(max(min_gap, next_start - float(last["end"])), 3)})
    return units


def spread_gaps(desired: Sequence[float], total: float, *, min_gap: float = 0.08,
                max_gap: float = 0.5) -> list[float]:
    """The pauses that follow the original as closely as possible while still filling a window.

    ``desired`` are the gaps the anchors would like (or the pause the recorder actually
    made, where a chunk has no anchor), and ``total`` is the silence the part owns: the
    window minus its speech. Every gap stays inside ``[min_gap, max_gap]`` so two
    sentences never collide and none of them idles while the source keeps talking, and
    the sum is exactly ``total`` so the part ends where its window ends. That combination
    is the closed-form optimum of "closest to the original, subject to covering the
    picture", and it is why the text can never be compressed or padded with silence.

    A bisection on the Lagrange multiplier finds the shift that clips the desired gaps
    onto the required sum; the cap is raised automatically when a part is too short for
    its window, which then behaves like the older even spread.
    """
    count = len(desired)
    if count == 0:
        if total > 1e-9:
            raise ValueError("a single chunk cannot hold surplus silence")
        return []
    floor = max(0.0, min_gap)
    ceiling = max(floor, float(max_gap), total / count)  # always enough room to fill the window
    if total < floor * count - 1e-9:
        raise ValueError(f"pauses cannot shrink below {floor}s and still fit {total:.2f}s")

    def filled(shift: float) -> float:
        return sum(min(ceiling, max(floor, float(value) - shift)) for value in desired)

    low, high = -2.0 * total - 10.0, 2.0 * total + 10.0
    for _ in range(80):  # filled() is monotone in the shift, so bisect on it
        middle = (low + high) / 2
        if filled(middle) > total:
            low = middle
        else:
            high = middle
    shift = (low + high) / 2
    gaps = [min(ceiling, max(floor, float(value) - shift)) for value in desired]
    leftover = total - sum(gaps)
    if abs(leftover) > 1e-9:  # one float of rounding left over: give it to the largest gap
        index = max(range(count), key=lambda position: gaps[position] - floor if leftover > 0
                   else ceiling - gaps[position])
        gaps[index] = min(ceiling, max(floor, gaps[index] + leftover))
    return [round(gap, 6) for gap in gaps]


def anchor_errors(placed: Sequence[float], errors: Sequence[float | None]) -> dict:
    values = sorted(abs(error) for error in errors if error is not None)
    if not values:
        return {"anchored": 0}
    return {"anchored": len(values), "mean_seconds": round(sum(values) / len(values), 3),
            "median_seconds": round(values[len(values) // 2], 3),
            "p90_seconds": round(values[min(len(values) - 1, int(0.9 * len(values)))], 3),
            "max_seconds": round(values[-1], 3)}
