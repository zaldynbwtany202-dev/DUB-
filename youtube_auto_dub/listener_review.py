"""Immutable, hash-bound listener approvals for Arabic voice takes.

This stores a human attestation; it cannot prove that a person listened. A
valid approval is bound to one audio file, script and generated checklist.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CRITERIA = ("pronunciation", "dialect", "naturalness", "timing")
_REQUIRED_VALUES = {"pass", "needs_work"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _script_hash(path: Path) -> str:
    # Match the checklist generator's deliberate trim of the text file.
    text = Path(path).read_text(encoding="utf-8").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_listener_review(
    checklist_path: Path, audio_path: Path, text_path: Path, *, reviewer: str,
    pronunciation: str, dialect: str, naturalness: str, timing: str,
    notes: str = "", attested_listened: bool = False,
) -> dict[str, Any]:
    """Create a pass/rework record only after an explicit listener attestation."""
    checklist_path, audio_path, text_path = map(Path, (checklist_path, audio_path, text_path))
    if not attested_listened:
        raise ValueError("explicitly attest that you listened to the exact audio before recording a review")
    if not reviewer.strip():
        raise ValueError("reviewer name/ID is required")
    for path, label in ((checklist_path, "checklist"), (audio_path, "audio"), (text_path, "script")):
        if not path.is_file() or (label == "audio" and path.stat().st_size == 0):
            raise FileNotFoundError(f"Missing or empty {label}: {path}")

    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    if not isinstance(checklist, dict) or checklist.get("status") != "listener_review_required":
        raise ValueError("expected a pronunciation checklist that still requires listener review")
    audio_hash = sha256_file(audio_path)
    script_hash = _script_hash(text_path)
    if checklist.get("audio_sha256") != audio_hash:
        raise ValueError("checklist audio hash is stale; rebuild it for this exact take")
    if checklist.get("script_sha256") != script_hash:
        raise ValueError("checklist script hash is stale; rebuild it for this exact script")

    criteria = {
        "pronunciation": pronunciation,
        "dialect": dialect,
        "naturalness": naturalness,
        "timing": timing,
    }
    for name, value in criteria.items():
        allowed = _REQUIRED_VALUES | ({"not_applicable"} if name == "dialect" else set())
        if value not in allowed:
            raise ValueError(f"{name} must be one of {sorted(allowed)}")
    if dialect == "not_applicable" and checklist.get("dialect_requested") not in (None, "", "ar"):
        raise ValueError("a requested regional dialect cannot be marked not_applicable")
    decision = "approved" if all(value in {"pass", "not_applicable"} for value in criteria.values()) else "needs_revision"
    if decision != "approved" and not notes.strip():
        raise ValueError("record notes describing what needs correction")

    return {
        "schema_version": 1,
        "decision": decision,
        "human_attestation": "listened_to_exact_audio",
        "reviewer": reviewer.strip(),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "checklist_sha256": sha256_file(checklist_path),
        "audio_sha256": audio_hash,
        "script_sha256": script_hash,
        "criteria": criteria,
        "notes": notes.strip(),
    }


def require_listener_approval(
    review_path: Path, audio_path: Path, text_path: Path, checklist_path: Path,
) -> dict[str, Any]:
    """Fail closed unless the current script, audio and checklist were approved."""
    review_path, audio_path, text_path, checklist_path = map(
        Path, (review_path, audio_path, text_path, checklist_path),
    )
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("listener review/checklist is missing or invalid") from exc
    if (not isinstance(review, dict) or review.get("schema_version") != 1
            or review.get("decision") != "approved"):
        raise ValueError("listener approval is absent or the take needs revision")
    if review.get("human_attestation") != "listened_to_exact_audio" or not str(review.get("reviewer", "")).strip():
        raise ValueError("listener identity/attestation is missing")
    try:
        reviewed_at = datetime.fromisoformat(str(review["reviewed_at_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("listener review timestamp is missing or invalid") from exc
    if reviewed_at.tzinfo is None:
        raise ValueError("listener review timestamp must include a timezone")
    criteria = review.get("criteria")
    if (not isinstance(criteria, dict)
            or any(criteria.get(name) != "pass" for name in ("pronunciation", "naturalness", "timing"))
            or criteria.get("dialect") not in {"pass", "not_applicable"}):
        raise ValueError("all listener review criteria must pass")
    if not audio_path.is_file() or not text_path.is_file() or not checklist_path.is_file():
        raise FileNotFoundError("approved audio, script and checklist must still exist")
    current_audio_hash = sha256_file(audio_path)
    current_script_hash = _script_hash(text_path)
    if review.get("audio_sha256") != current_audio_hash:
        raise ValueError("audio changed after listener approval")
    if review.get("script_sha256") != current_script_hash:
        raise ValueError("script changed after listener approval")
    if review.get("checklist_sha256") != sha256_file(checklist_path):
        raise ValueError("checklist changed after listener approval")
    if (not isinstance(checklist, dict) or checklist.get("status") != "listener_review_required"
            or checklist.get("audio_sha256") != current_audio_hash
            or checklist.get("script_sha256") != current_script_hash):
        raise ValueError("checklist does not describe the current audio and script")
    return review
