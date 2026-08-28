"""Unit tests for the sync scheduler (no external dependencies)."""
from dub.models import Segment
from dub.sync import schedule


def seg(i, start, end, dur):
    s = Segment(id=i, start=start, end=end, text_src="x")
    s.speech_dur = dur
    return s


def test_fitting_line_is_untouched():
    segs = [seg(0, 0.0, 2.0, 1.5), seg(1, 5.0, 7.0, 1.8)]
    plans = schedule(segs, media_duration=10.0)
    assert plans[0].placed_at == 0.0
    assert plans[0].stretch == 1.0
    assert not plans[0].needs_resynth


def test_long_line_stretches_into_gap():
    # line needs 3.0s, lip window is 2.0s, gap until next line is 1.4s
    segs = [seg(0, 0.0, 2.0, 3.0), seg(1, 3.4, 5.0, 1.0)]
    plans = schedule(segs, media_duration=10.0, gap=0.1)
    # available = 3.4 - 0.1 - 0.0 = 3.3  →  fits without stretch
    assert plans[0].stretch == 1.0
    assert plans[0].overflow > 0  # runs past the lip window into the gap


def test_excessive_line_gets_resynth_hint():
    # 8s of speech, only ~2.9s available → beyond max_stretch
    segs = [seg(0, 0.0, 2.0, 8.0), seg(1, 3.0, 4.0, 1.0)]
    plans = schedule(segs, media_duration=10.0, gap=0.1, max_stretch=1.25)
    assert plans[0].needs_resynth
    assert plans[0].stretch == 1.25
    assert 1.0 < plans[0].speed_hint <= 1.8


def test_last_line_uses_media_end():
    segs = [seg(0, 8.0, 9.0, 2.0)]
    plans = schedule(segs, media_duration=12.0)
    assert plans[0].stretch == 1.0  # 2.0s fits into 12 - 0.08 - 8 = 3.92s


def test_never_collides_with_next_line():
    segs = [seg(0, 0.0, 1.0, 1.5), seg(1, 1.5, 2.5, 1.0)]
    plans = schedule(segs, media_duration=10.0, gap=0.1)
    end0 = plans[0].placed_at + segs[0].speech_dur / plans[0].stretch
    assert end0 <= segs[1].start - 0.1 + 1e-6
