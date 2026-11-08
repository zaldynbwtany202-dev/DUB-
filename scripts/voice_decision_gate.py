"""Fail-closed production preflight for the paused Patrick Jane dub.

Call this before any production assembly. It cannot intercept direct external
speech-generation tools; the caller must still respect the listener's decision.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "patrick-jane-return"


class VoiceDecisionError(RuntimeError):
    pass


def require_production_ready(slug: str, root: Path = ROOT) -> str | None:
    """Other film projects are unaffected; this one needs two explicit approvals."""
    if slug != PROJECT:
        return
    path = Path(root) / "config/patrick-jane-voice-status.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VoiceDecisionError("Patrick Jane: voice decision missing/invalid; production paused") from exc
    if (not isinstance(state, dict) or state.get("project") != PROJECT
            or state.get("status") != "approved"
            or state.get("approved_for_production") is not True
            or state.get("transcript_approved") is not True
            or not isinstance(state.get("approved_voice_id"), str)
            or not state["approved_voice_id"].strip()):
        raise VoiceDecisionError(
            "Patrick Jane: voice and transcript need explicit approval; production paused"
        )
    return state["approved_voice_id"].strip()


if __name__ == "__main__":
    import sys
    try:
        require_production_ready(sys.argv[1] if len(sys.argv) > 1 else PROJECT)
    except VoiceDecisionError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc
    print("Production approval preflight passed (independent audio QA still required).")
