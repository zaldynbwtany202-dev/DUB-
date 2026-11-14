from __future__ import annotations

import hashlib
import json

import pytest

from scripts.record_arabic_review import main as record_main
from youtube_auto_dub.listener_review import create_listener_review, require_listener_approval


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    audio = tmp_path / "take.mp3"
    audio.write_bytes(b"exact audio bytes")
    text = tmp_path / "script.txt"
    text.write_text("بقول قوتهم، والسفر.", encoding="utf-8")
    checklist = tmp_path / "checklist.json"
    checklist.write_text(json.dumps({
        "status": "listener_review_required",
        "dialect_requested": "ar-EG",
        "audio_sha256": sha(audio),
        "script_sha256": hashlib.sha256(text.read_text(encoding="utf-8").strip().encode()).hexdigest(),
    }), encoding="utf-8")
    return audio, text, checklist


def approved(checklist, audio, text):
    return create_listener_review(
        checklist, audio, text, reviewer="reviewer-1",
        pronunciation="pass", dialect="pass", naturalness="pass", timing="pass",
        attested_listened=True,
    )


def test_listener_approval_is_hash_bound_and_fail_closed(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    review_path = tmp_path / "listener.json"
    review_path.write_text(json.dumps(approved(checklist, audio, text)), encoding="utf-8")
    result = require_listener_approval(review_path, audio, text, checklist)
    assert result["decision"] == "approved"
    audio.write_bytes(b"different take")
    with pytest.raises(ValueError, match="audio changed"):
        require_listener_approval(review_path, audio, text, checklist)


def test_approval_requires_a_timezone_aware_listener_timestamp(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    report = approved(checklist, audio, text)
    report.pop("reviewed_at_utc")
    review_path = tmp_path / "listener.json"
    review_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="timestamp is missing"):
        require_listener_approval(review_path, audio, text, checklist)


def test_changed_script_or_checklist_invalidates_approval(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    report = approved(checklist, audio, text)
    review_path = tmp_path / "listener.json"
    review_path.write_text(json.dumps(report), encoding="utf-8")
    text.write_text("بقول طعامهم، والسفر.", encoding="utf-8")
    with pytest.raises(ValueError, match="script changed"):
        require_listener_approval(review_path, audio, text, checklist)

    text.write_text("بقول قوتهم، والسفر.", encoding="utf-8")
    checklist.write_text(checklist.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="checklist changed"):
        require_listener_approval(review_path, audio, text, checklist)


def test_needs_work_is_recorded_but_never_passes_gate(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    review = create_listener_review(
        checklist, audio, text, reviewer="listener", pronunciation="needs_work",
        dialect="pass", naturalness="pass", timing="pass", notes="راجع نطق قوتهم.",
        attested_listened=True,
    )
    assert review["decision"] == "needs_revision"
    path = tmp_path / "listener.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="needs revision"):
        require_listener_approval(path, audio, text, checklist)


def test_no_attestation_or_empty_review_notes_is_rejected(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    with pytest.raises(ValueError, match="attest"):
        create_listener_review(
            checklist, audio, text, reviewer="listener", pronunciation="pass",
            dialect="pass", naturalness="pass", timing="pass",
        )
    with pytest.raises(ValueError, match="record notes"):
        create_listener_review(
            checklist, audio, text, reviewer="listener", pronunciation="needs_work",
            dialect="pass", naturalness="pass", timing="pass", attested_listened=True,
        )
    with pytest.raises(ValueError, match="cannot be marked not_applicable"):
        create_listener_review(
            checklist, audio, text, reviewer="listener", pronunciation="pass",
            dialect="not_applicable", naturalness="pass", timing="pass",
            attested_listened=True,
        )


def test_cli_records_immutable_auditable_review(tmp_path):
    audio, text, checklist = fixture(tmp_path)
    out = tmp_path / "review.json"
    args = [
        "--checklist", str(checklist), "--audio", str(audio), "--text-file", str(text),
        "--reviewer", "human-01", "--pronunciation", "pass", "--dialect", "pass",
        "--naturalness", "pass", "--timing", "pass", "--attest-listened", "--out", str(out),
    ]
    assert record_main(args) == 0
    original = out.read_bytes()
    assert json.loads(original)["decision"] == "approved"
    assert record_main(args) == 2
    assert out.read_bytes() == original
