import json
from pathlib import Path

import pytest

from youtube_auto_dub.content_validation import is_non_speech_text, normalize_tokens, validate_spoken_content, word_timing_report
from youtube_auto_dub.voice_profiles import ENGINES, REFERENCE_MODES, VOICE_CONVERSIONS, load_voice_profiles, template_for_speakers


def test_every_character_gets_all_independent_options(tmp_path):
    document = {
        "speakers": {
            "SPEAKER_00": {"reference_mode": "source", "tts_engine": "xtts", "voice_conversion": "seed-vc", "approved": True},
            "SPEAKER_01": {"reference_mode": "source", "tts_engine": "voxcpm", "voice_conversion": "none", "approved": True},
        }
    }
    path = tmp_path / "voices.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    profiles = load_voice_profiles(path, ["SPEAKER_00", "SPEAKER_01"], require_approval=True)
    assert profiles["SPEAKER_00"]["tts_engine"] == "xtts"
    assert profiles["SPEAKER_01"]["tts_engine"] == "voxcpm"
    assert profiles["SPEAKER_00"] is not profiles["SPEAKER_01"]
    assert set(ENGINES) == {"voxcpm", "xtts"}
    assert set(REFERENCE_MODES) == {"source", "custom"}
    assert set(VOICE_CONVERSIONS) == {"seed-vc", "none"}



def test_removed_engines_and_synthetic_reference_are_refused(tmp_path):
    for profile in (
        {"reference_mode": "source", "tts_engine": "edge", "approved": True},
        {"reference_mode": "source", "tts_engine": "qwen", "approved": True},
        {"reference_mode": "synthetic", "tts_engine": "voxcpm", "approved": True},
    ):
        path = tmp_path / "voices.json"
        path.write_text(json.dumps({"speakers": {"SPEAKER_00": profile}}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_voice_profiles(path, ["SPEAKER_00"], require_approval=True)

def test_custom_reference_must_exist_and_unapproved_profile_is_refused(tmp_path):
    missing = tmp_path / "missing.wav"
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"speakers": {"SPEAKER_00": {"reference_mode": "custom", "reference_path": str(missing), "approved": True}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="custom reference is missing"):
        load_voice_profiles(path, ["SPEAKER_00"], require_approval=True)
    path.write_text(json.dumps({"speakers": {"SPEAKER_00": {"reference_mode": "source", "approved": False}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="not approved"):
        load_voice_profiles(path, ["SPEAKER_00"], require_approval=True)


def test_template_contains_full_controls_for_each_character():
    template = template_for_speakers(["A", "B"])
    for speaker in ("A", "B"):
        profile = template["speakers"][speaker]
        assert {"reference_mode", "reference_path", "tts_engine", "voice_conversion", "style", "gender", "approved"} <= set(profile)


def test_content_validator_detects_missing_phrase():
    expected = "the quick brown fox jumps over the lazy dog near the river"
    good = "The quick brown fox jumps over the lazy dog near the river."
    missing = "the quick fox near the river"
    assert validate_spoken_content(expected, good)["ok"] is True
    result = validate_spoken_content(expected, missing)
    assert result["ok"] is False
    assert result["recall"] < 0.70
    assert "brown" in result["missing_words"]


def test_word_timing_report_does_not_invent_linear_word_targets():
    words = [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 1.0},
    ]
    result = word_timing_report(words, 4.0, 5.0)
    assert result["ok"] is True
    assert result["word_count"] == 2
    assert result["words"][0]["actual_start"] == 4.0
    assert result["words"][-1]["actual_end"] == 5.0
    assert result["sync_verified"] is False
    assert result["ok_scope"] == "structural_timing_only_not_sync_approval"
    assert result["reference_available"] is False
    assert "ideal_end" not in result["words"][-1]
    assert "reference" in result["evidence"]


def test_word_timing_report_measures_boundaries_against_human_reference():
    words = [
        {"word": "طعامهم", "start": 0.02, "end": 0.42},
        {"word": "خير", "start": 0.51, "end": 0.98},
    ]
    reference = {
        "reference_verified": True, "reviewer": "listener-1",
        "words": [
            {"word": "طعامهم", "start": 0.0, "end": 0.4},
            {"word": "خير", "start": 0.5, "end": 1.0},
        ],
    }
    result = word_timing_report(words, 10.0, 11.0, reference=reference, tolerance_seconds=0.1)
    assert result["sync_verified"] is True
    assert result["status"] == "human_reference_alignment_pass"
    assert result["mean_abs_boundary_error_seconds"] == 0.0175
    assert result["max_abs_boundary_error_seconds"] == 0.02
    assert result["words"][0]["start_error_seconds"] == 0.02


def test_word_timing_report_rejects_unverified_or_mismatching_reference():
    words = [{"word": "عَلَم", "start": 0.0, "end": 0.4}]
    unverified = {"reference_verified": False, "reviewer": "", "words": words}
    result = word_timing_report(words, 0.0, 1.0, reference=unverified)
    assert result["sync_verified"] is False
    assert result["status"] == "reference_not_human_verified"
    mismatch = {"reference_verified": True, "reviewer": "human", "words": [
        {"word": "قَلَم", "start": 0.0, "end": 0.4},
    ]}
    result = word_timing_report(words, 0.0, 1.0, reference=mismatch)
    assert result["sync_verified"] is False
    assert result["status"] == "reference_text_or_timing_mismatch"


def test_alignment_cli_requires_human_reference(tmp_path):
    from scripts.evaluate_word_alignment import main

    source = tmp_path / "alignment.json"
    source.write_text(json.dumps({
        "speech_start": 0.0, "speech_end": 1.0,
        "observed_words": [{"word": "كلمة", "start": 0.0, "end": 1.0}],
    }), encoding="utf-8")
    out = tmp_path / "alignment-report.json"
    assert main(["--input", str(source), "--out", str(out)]) == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["sync_verified"] is False


def test_non_speech_labels_are_not_synthesized():
    assert is_non_speech_text("Music") is True
    assert is_non_speech_text("[MUSIC]") is True
    assert is_non_speech_text("موسيقى") is True
    assert is_non_speech_text("music begins now") is False


def test_arabic_vowels_no_longer_split_asr_words_or_hide_missing_words():
    assert normalize_tokens("طَعَامِهِم سَيَجْلِبُ الْخَيْر") == ["طعامهم", "سيجلب", "الخير"]
    same = validate_spoken_content("طعامهم سيجلب الخير", "طَعَامِهِم سَيَجْلِبُ الْخَيْر")
    assert same["ok"] is True and same["matched_words"] == 3
    assert same["listener_review_required"] is True
    assert same["pronunciation_verified"] is False
    wrong = validate_spoken_content("طعامهم سيجلب الخير", "قواتهم سيجلب الخير")
    assert wrong["ok"] is False
    assert "طعامهم" in wrong["missing_words"]
    assert normalize_tokens("إيه ايه آية") == ["ايه", "ايه", "اية"]
