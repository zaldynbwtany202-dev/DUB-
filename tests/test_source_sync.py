import pytest

from youtube_auto_dub.source_sync import (
    assert_coverage,
    count_words,
    coverage_from_placements,
    evaluate_take,
    forbid_merge,
    load_policy,
    plan_from_asr,
    soundtrack_onset,
    soundtrack_windows,
    tts_safe_egyptian,
    word_budget,
)


def test_policy_forbids_merge_and_slowdown():
    policy = load_policy()
    assert policy["merge"] == "forbidden"
    assert policy["session_branch"] == "arena/01a0922c-dub"
    assert policy["min_tempo"] == 1.0
    assert policy["never_slow_to_fill"] is True


def test_session_branch_cannot_be_merged():
    with pytest.raises(PermissionError, match="forbidden"):
        forbid_merge("arena/01a0922c-dub", "main")
    with pytest.raises(PermissionError, match="forbidden"):
        forbid_merge("arena/01a0922c-dub", "arena/01a07c92-dub")


def test_other_branches_are_not_the_session_lock():
    forbid_merge("feature", "arena/01a0922c-dub")


def test_word_budget_matches_natural_speech_rate():
    assert word_budget(5.32) == 8
    assert word_budget(1.56) == 2


def test_too_long_take_must_be_rewritten_not_squeezed():
    report = evaluate_take(6.9, 5.32)
    assert report["status"] == "too_long"
    assert report["slowed"] is False
    assert report["action"] == "shorten_or_rerecord"


def test_short_take_is_not_slowed():
    report = evaluate_take(3.0, 5.32)
    assert report["tempo"] == 1.0
    assert report["slowed"] is False
    assert report["status"] == "too_short"
    assert report["uncovered_seconds"] > 0.4


def test_refuses_slowdown_policy():
    with pytest.raises(ValueError, match="slow"):
        evaluate_take(3.0, 5.0, min_tempo=0.85)


def test_plan_follows_source_soundtrack_windows():
    sheet = plan_from_asr([
        {"start": 0.0, "end": 5.32, "text": "فدل يتحك ويبتسم لانه بعد سنين"},
        {"start": 5.32, "end": 9.72, "text": "ارض المعركة اخيرا مية نضيفة"},
    ])
    assert sheet["timing_mode"] == "source_audio"
    assert sheet["min_tempo"] == 1.0
    assert sheet["merge"] == "forbidden"
    assert sheet["segments"][0]["start"] == 0.0
    assert sheet["segments"][1]["start"] == 5.32
    assert sheet["segments"][0]["word_budget"] == word_budget(5.32)


def test_soundtrack_onset_uses_dtw_not_asr_padding():
    raw = {
        "start": 0.0,
        "end": 5.32,
        "text": "فدل يتحك",
        "words": [
            {"word": "فدل", "start": 0.0, "end": 0.45, "dtw_points": [0.9, 1.1]},
            {"word": "يتحك", "start": 0.45, "end": 1.05, "dtw_points": [1.28]},
        ],
    }
    assert soundtrack_onset(raw) == pytest.approx(0.9)
    windows = soundtrack_windows([
        raw,
        {"start": 5.32, "end": 9.72, "text": "ارض", "words": [
            {"word": "ارض", "start": 5.32, "end": 5.54, "dtw_points": [5.42, 5.52]},
        ]},
    ], duration=9.72)
    assert windows[0] == (0.9, 5.42)
    assert windows[1][0] == 5.42


def test_plan_with_dtw_places_cues_on_soundtrack_onsets():
    sheet = plan_from_asr([
        {"start": 0.0, "end": 5.32, "text": "فدل يتحك ويبتسم",
         "words": [{"word": "فدل", "start": 0.0, "end": 0.45, "dtw_points": [0.9]}]},
        {"start": 5.32, "end": 9.72, "text": "ارض المعركة",
         "words": [{"word": "ارض", "start": 5.32, "end": 5.54, "dtw_points": [5.42]}]},
    ], duration=9.72)
    assert sheet["segments"][0]["start"] == 0.9
    assert sheet["segments"][0]["end"] == 5.42
    assert sheet["segments"][1]["start"] == 5.42


def test_overlapping_source_windows_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        plan_from_asr([
            {"start": 0, "end": 2, "text": "أ"},
            {"start": 1, "end": 3, "text": "ب"},
        ])


def test_tts_safe_spelling_does_not_invent_plot():
    assert "مية من الطين" in tts_safe_egyptian("وهو بيشرب مية طينية")
    assert "يشرب" in tts_safe_egyptian("بيشرب")
    assert count_words("فضل يضحك ويبتسم") == 3


def test_coverage_finds_tails_without_claiming_lip_sync():
    report = coverage_from_placements(
        [(0.0, 5.32), (5.32, 9.72)],
        [
            {"start": 0.0, "fitted_seconds": 3.0},
            {"start": 5.32, "fitted_seconds": 3.0},
        ],
    )
    assert report["slowed"] is False
    assert report["longest_uncovered_seconds"] == pytest.approx(2.32, abs=0.05)
    with pytest.raises(ValueError, match="uncovered"):
        assert_coverage(report)


def test_coverage_counts_observed_leading_internal_and_tail_silence():
    # A 5 s file with 2 s of silence inside it must not be called 5 s of speech.
    report = coverage_from_placements(
        [(0.0, 5.0)],
        [{"start": 0.0, "fitted_seconds": 5.0,
          "active_start_seconds": 0.4, "active_end_seconds": 4.5,
          "internal_silences": [{"start": 1.2, "end": 3.2}]}],
    )
    assert report["uncovered_seconds"] == pytest.approx(2.9, abs=0.01)
    assert report["longest_uncovered_seconds"] == pytest.approx(2.0, abs=0.01)
    assert [h["kind"] for h in report["holes"]] == ["leading", "internal", "tail"]
    assert report["holes"][1]["start"] == pytest.approx(1.2)
    with pytest.raises(ValueError, match="uncovered"):
        assert_coverage(report)


def test_coverage_coalesces_rounded_or_overlapping_silences():
    report = coverage_from_placements(
        [(2.0, 5.0)],
        [{"start": 2.0, "fitted_seconds": 1.5,
          "active_start_seconds": 0.5, "active_end_seconds": 1.0,
          "internal_silences": [{"start": 0.25, "end": 0.75}]}],
    )
    # 0.25 s of activity (2.75..3.0); overlap merges into one leading gap.
    assert report["uncovered_seconds"] == pytest.approx(2.75)
    assert report["longest_uncovered_seconds"] == pytest.approx(2.0)
    assert len(report["holes"]) == 2


def test_fully_active_take_has_no_estimated_gap():
    report = coverage_from_placements([(0.0, 1.5)], [{"start": 0.0, "fitted_seconds": 1.5,
                                                       "active_start_seconds": 0.0,
                                                       "active_end_seconds": 1.5,
                                                       "internal_silences": []}])
    assert report["uncovered_seconds"] == 0
    assert report["holes"] == []
    assert_coverage(report)
