import pytest

from youtube_auto_dub.source_sync import (
    assert_coverage,
    count_words,
    coverage_from_placements,
    evaluate_take,
    forbid_merge,
    load_policy,
    plan_from_asr,
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
