"""A lexical ASR match never substitutes for hearing the actual take."""
import hashlib
import json

import pytest

from youtube_auto_dub.pronunciation_review import review_pronunciation
from scripts.review_arabic_take import build_review, main


def test_homonym_and_dialect_checklist_does_not_rewrite_script():
    text = "بقول قوتهم، والسفر هيجلب الرزق."
    report = review_pronunciation(text, "بقول قواتهم والسفر هيجلب الرزق", dialect="ar-EG")
    flagged = {r["matched_word"] for r in report["homonym_hazards"]}
    assert {"قوتهم", "السفر", "الرزق"} <= flagged
    assert any(s["word"] == "بقول" for s in report["dialect_suggestions"])
    assert any(s["word"] == "بقول" for s in report["regional_variants"])
    assert report["asr_disagreement"] is True
    assert "قوتهم" in report["lexical_asr_check"]["missing_words"]
    assert report["script_modified"] is False
    assert report["status"] == "listener_review_required"
    assert text == report["expected_text"]


def test_same_asr_letters_cannot_prove_the_intended_homograph_sound():
    report = review_pronunciation("يجدون قوتهم", "يجدون قوتهم")
    assert report["lexical_asr_check"]["ok"] is True
    assert report["homonym_hazards"]
    assert report["status"] == "listener_review_required"
    assert report["lexical_asr_check"]["pronunciation_verified"] is False


def test_wrong_take_hash_is_rejected_and_previous_checklist_is_preserved(tmp_path):
    audio = tmp_path / "take.mp3"
    audio.write_bytes(b"first take")
    text = tmp_path / "words.txt"
    text.write_text("سيجلب الخير", encoding="utf-8")
    asr = tmp_path / "source.segments.json"
    asr.write_text(json.dumps({"review_required": True, "source": {"sha256": "f" * 64},
                               "segments": [{"text": "سيجلب الخير"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="source hash differs"):
        build_review(text, audio, asr)
    asr.write_text(json.dumps({"review_required": True,
                               "source": {"sha256": hashlib.sha256(audio.read_bytes()).hexdigest()},
                               "asr": {"engine": "faster-whisper"},
                               "segments": [{"text": "سيجلب الخير"}]}), encoding="utf-8")
    report = build_review(text, audio, asr)
    assert report["audio_sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert report["status"] == "listener_review_required"
    out = tmp_path / "review.json"
    argv = ["--text-file", str(text), "--audio", str(audio), "--asr-json", str(asr), "--out", str(out)]
    assert main(argv) == 0
    original = out.read_bytes()
    assert main(argv) == 2  # never replace a prior review
    assert out.read_bytes() == original
