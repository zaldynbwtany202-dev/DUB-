import hashlib
import json

import pytest

from youtube_auto_dub.listener_review import create_listener_review
from youtube_auto_dub.release_review import check_release_manifest


def make_asset(root, asset_id):
    audio = root / f"{asset_id}.wav"
    audio.write_bytes((asset_id + " audio").encode())
    text = root / f"{asset_id}.txt"
    text.write_text("نص مضبوط", encoding="utf-8")
    checklist = root / f"{asset_id}.checklist.json"
    checklist.write_text(json.dumps({
        "status": "listener_review_required",
        "dialect_requested": "ar-EG",
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(text.read_text(encoding="utf-8").strip().encode()).hexdigest(),
    }), encoding="utf-8")
    review = create_listener_review(
        checklist, audio, text, reviewer="listener-1", pronunciation="pass",
        dialect="pass", naturalness="pass", timing="pass", attested_listened=True,
    )
    review_path = root / f"{asset_id}.review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    return {"asset_id": asset_id, "review": review_path.name,
            "checklist": checklist.name, "audio": audio.name, "text_file": text.name}


def test_release_requires_every_expected_reviewed_asset(tmp_path):
    asset = make_asset(tmp_path, "final-mix")
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "project": "film-1", "final_asset_id": "final-mix",
        "expected_assets": ["final-mix"], "assets": [asset],
    }), encoding="utf-8")
    report = check_release_manifest(manifest)
    assert report["release_ready"] is True
    assert report["human_approved_assets"] == 1
    assert report["assets"][0]["asset_id"] == "final-mix"


def test_missing_group_or_final_mix_review_blocks_release(tmp_path):
    asset = make_asset(tmp_path, "group-001")
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "final_asset_id": "final-mix",
        "expected_assets": ["group-001", "final-mix"], "assets": [asset],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing=\['final-mix'\]"):
        check_release_manifest(manifest)


def test_stale_or_unapproved_asset_blocks_project_release(tmp_path):
    asset = make_asset(tmp_path, "final-mix")
    (tmp_path / asset["audio"]).write_bytes(b"changed final mix")
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "final_asset_id": "final-mix",
        "expected_assets": ["final-mix"], "assets": [asset],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="audio changed"):
        check_release_manifest(manifest)
