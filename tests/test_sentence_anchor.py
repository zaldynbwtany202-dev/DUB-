"""Sentence-level anchoring: units follow the source, placements stay legal."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_auto_dub.pause_sync import sentence_indices  # noqa: E402
from youtube_auto_dub.sentence_anchor import (anchor_errors, source_units, spread_gaps,  # noqa: E402
                                         split_sentences, token_count)


def words(times: list[float], text: str = "كلمة") -> list[dict]:
    return [{"word": text, "start": start, "end": start + 0.2} for start in times]


def test_split_sentences_keeps_order_and_strips_separators() -> None:
    sentences = split_sentences("دخل الليل. والحرم نائم، والشمس غربت،  ")
    assert sentences == ["دخل الليل", "والحرم نائم", "والشمس غربت"]
    assert split_sentences("؛ ، .") == []


def test_units_pair_by_word_mass() -> None:
    units = source_units(words([0.0, 0.3, 0.6, 1.0, 1.3]), 3, 0.0, 2.0)
    assert [unit["index"] for unit in units] == [0, 1, 2]
    assert units[0]["start"] == 0.0 and units[-1]["next"] == pytest.approx(2.0, abs=1e-6)
    assert sum(unit["words"] for unit in units) == 5
    assert [unit["words"] for unit in units] == [2, 1, 2]  # even pace -> even split by rank


def test_a_breath_in_the_source_splits_units_without_a_breath_splitting_uniformly() -> None:
    # six words at an even pace and one requested unit -> no room to cut, so one unit
    even = words([i * 0.6 for i in range(6)])
    assert len(source_units(even, 1, 0.0, 3.6)) == 1
    assert len(source_units(even, 3, 0.0, 3.6)) == 3
    # a real pause in the middle of the window moves the boundary onto it
    breathing = words([0.0, 0.4, 0.8, 2.0, 2.4, 2.8])
    units = source_units(breathing, 2, 0.0, 3.2)
    assert len(units) == 2
    assert units[0]["end"] == pytest.approx(0.8 + 0.2, abs=1e-6)
    assert units[1]["start"] == pytest.approx(2.0, abs=1e-6)
    assert units[0]["gap"] == pytest.approx(1.0, abs=1e-6)


def test_gaps_follow_the_source_but_still_fill_the_window() -> None:
    # anchors want a long pause after the first clause; the part owns 3.0 s of silence
    gaps = spread_gaps([2.0, 0.2, 0.2, 0.2], 3.0, min_gap=0.08, max_gap=1.5)
    assert sum(gaps) == pytest.approx(3.0)
    assert all(0.08 - 1e-9 <= gap <= 1.5 + 1e-9 for gap in gaps)  # capped, never colliding
    assert gaps == sorted(gaps, reverse=True)  # the anchored pause keeps the biggest share


def test_no_pause_idles_past_the_hole_budget_even_when_an_anchor_is_late() -> None:
    gaps = spread_gaps([9.0, 0.1, 0.1], 1.2, min_gap=0.1, max_gap=0.5)
    assert max(gaps) <= 0.5 + 1e-9          # source speech may not be left unanswered
    assert sum(gaps) == pytest.approx(1.2)
    assert gaps[0] == max(gaps)              # the late anchor still gets the biggest share


def test_a_window_that_needs_more_silence_than_the_cap_spreads_evenly() -> None:
    gaps = spread_gaps([0.2, 0.2, 0.2], 3.0, min_gap=0.08, max_gap=0.4)
    assert gaps == [pytest.approx(1.0)] * 3
    with pytest.raises(ValueError, match="cannot shrink below"):
        spread_gaps([0.5, 0.5], 0.2, min_gap=0.3)
    with pytest.raises(ValueError, match="single chunk"):
        spread_gaps([], 1.0)


def test_anchor_errors_ignores_unanchored_chunks() -> None:
    errors = anchor_errors([0.0, 1.0, 2.0], [0.2, None, -1.0])
    assert errors["anchored"] == 2 and "mean_seconds" in errors
    assert errors["mean_seconds"] == pytest.approx(0.6, abs=1e-6)
    assert anchor_errors([0.0], [None]) == {"anchored": 0}
