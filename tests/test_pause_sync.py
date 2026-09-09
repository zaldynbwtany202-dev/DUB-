"""The pause plan must preserve speech and fill the original's speaking time."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_auto_dub.pause_sync import (align_sentences, allocate_part_spans, coverage,
                                         cue_times, plan_gaps)


def test_gaps_keep_rhythm_and_total():
    gaps = plan_gaps([0.5, 0.2, 0.05], 1.2, min_gap=0.1, max_gap=0.85)
    assert len(gaps) == 3
    assert abs(sum(gaps) - 1.2) < 1e-4
    assert gaps[0] > gaps[1] > gaps[2] >= 0.1


def test_gaps_never_shrink_below_minimum_when_squeezed():
    gaps = plan_gaps([0.3, 0.3, 0.3], 0.2, min_gap=0.1, max_gap=0.9)
    assert all(gap >= 0.02 for gap in gaps)
    assert abs(sum(gaps) - 0.2) < 1e-3


def test_gaps_spread_when_surplus_exceeds_ceiling():
    gaps = plan_gaps([0.2, 0.2], 3.0, min_gap=0.1, max_gap=0.5)
    assert abs(sum(gaps) - 3.0) < 1e-3


def test_part_can_borrow_time_instead_of_being_cut():
    spans = allocate_part_spans([10.0, 10.0], [12.0, 8.0], [20.0, 20.0], 20.0)
    assert spans[0] >= 12.0  # the tight part keeps its speech
    assert spans[0] + spans[1] == 20.0


def test_allocation_rejects_impossible_timelines():
    try:
        allocate_part_spans([5.0, 5.0], [6.0, 6.0], [10.0, 10.0], 10.0)
    except ValueError:
        return
    raise AssertionError("impossible plan must be refused")


def test_cues_are_monotonic_and_gap_aware():
    times = cue_times(4.0, [1.0, 2.0], [0.3], lead=0.05)
    assert times[0]["start"] == 4.05
    assert times[0]["end"] == 5.05
    assert times[1]["start"] == 5.35


def test_sentences_stay_whole_and_move_forward():
    texts = align_sentences(["أول.", "ثاني.", "ثالث."], [1.0] * 6)
    assert len(texts) == 6
    assert " ".join(texts).split() == "أول. ثاني. ثالث.".split()


def test_coverage_reports_uncovered_source_speech():
    report = coverage([[0.0, 10.0]], [[0.0, 4.0], [5.5, 10.0]], 10.0, min_gap=0.3)
    assert report["gap_count"] == 1
    assert 1.2 <= report["max_uncovered_gap"] <= 1.8
    perfect = coverage([[0.0, 10.0]], [[0.0, 10.0]], 10.0)
    assert perfect["source_speech_coverage"] == 1.0


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print("ok", name)
    print("pause_sync checks passed")
