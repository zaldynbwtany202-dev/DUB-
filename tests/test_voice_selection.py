"""Tests for the voices.html → agent voice-selection channel.

The user picks a voice on docs/voices.html; the page commits the pick to
inbox/voice-selection.json on the Sendbox branch, and the agent reads it back
with scripts/fetch_voice_selection.py. The network half is exercised in the
browser; what must be pinned here is the payload contract — a choice that
arrives malformed would silently dub with the wrong voice, which is the one
failure this channel must never have.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "fetch_voice_selection", ROOT / "scripts" / "fetch_voice_selection.py"
)
fvs = importlib.util.module_from_spec(spec)
sys.modules["fetch_voice_selection"] = fvs
spec.loader.exec_module(fvs)


def valid_selection() -> dict:
    return {
        "kind": "voice-selection",
        "selection_id": "voice-sel-20260910-123456",
        "created_at": "2026-09-10T12:34:56.000Z",
        "page": "voices.html",
        "roles": [
            {
                "role": "راغنار",
                "voice": "voice-00",
                "dialect": "مصرية",
                "dialect_code": "ar-EG",
                "sample_id": "R4_voice00_masri",
                "sample": "samples/R4_voice00_masri.mp3",
                "f0": 108,
                "cv": 0.35,
                "target_f0": 110,
                "phrase": "جملة الاختبار",
            }
        ],
        "note": "هذا صوت الراوي",
    }


# ── payload validation ───────────────────────────────────────────────────

def test_valid_selection_passes():
    assert fvs.validate_selection(valid_selection()) == []


def test_non_dict_payload_is_refused():
    assert fvs.validate_selection([1, 2, 3])
    assert fvs.validate_selection("voice-00")
    assert fvs.validate_selection(None)


def test_wrong_kind_is_refused():
    data = valid_selection()
    data["kind"] = "something-else"
    assert any("kind" in e for e in fvs.validate_selection(data))


def test_empty_roles_is_refused():
    data = valid_selection()
    data["roles"] = []
    errors = fvs.validate_selection(data)
    assert any("roles" in e for e in errors)


def test_role_missing_voice_is_refused():
    data = valid_selection()
    del data["roles"][0]["voice"]
    assert any("voice" in e for e in fvs.validate_selection(data))


def test_unknown_dialect_code_is_flagged():
    data = valid_selection()
    data["roles"][0]["dialect_code"] = "xx-XX"
    assert any("dialect_code" in e for e in fvs.validate_selection(data))


# ── description ──────────────────────────────────────────────────────────

def test_describe_names_voice_dialect_and_note():
    text = fvs.describe(valid_selection())
    assert "voice-00" in text
    assert "مصرية" in text
    assert "ar-EG" in text
    assert "هذا صوت الراوي" in text


def test_describe_omits_absent_note():
    data = valid_selection()
    data["note"] = ""
    assert "ملاحظة" not in fvs.describe(data)


# ── local copy ───────────────────────────────────────────────────────────

def test_save_local_writes_latest_and_archive(tmp_path):
    data = valid_selection()
    latest = fvs.save_local(data, tmp_path)
    assert latest.name == "latest.json"
    assert json.loads(latest.read_text(encoding="utf-8")) == data
    archive = tmp_path / "voice-sel-20260910-123456.json"
    assert archive.exists()
    assert json.loads(archive.read_text(encoding="utf-8")) == data


def test_save_local_sanitizes_id_into_filename(tmp_path):
    data = valid_selection()
    data["selection_id"] = 'bad:id/../with"chars'
    latest = fvs.save_local(data, tmp_path)
    assert latest.exists()
    for p in tmp_path.iterdir():
        assert set(p.name) <= set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.json"
        )


# ── the cross-file contract ──────────────────────────────────────────────
# Page writer and agent reader must agree on branch and path, or a choice is
# sent and never found. The JS side is pinned from here because the python
# suite is the one that always runs.

def test_js_module_commits_to_the_reader_path():
    js = (ROOT / "docs" / "voice-select.js").read_text(encoding="utf-8")
    assert "arena/01a07c69-dub" in js
    assert "inbox/voice-selection.json" in js
    assert 'kind: \'voice-selection\'' in js.replace('"', "'") or \
           'voice-selection' in js


def test_reader_uses_the_same_path():
    assert fvs.SELECTION_PATH == "inbox/voice-selection.json"
    assert fvs.BRANCH == "arena/01a07c69-dub"
    src = (ROOT / "scripts" / "fetch_voice_selection.py").read_text(
        encoding="utf-8"
    )
    assert '"arena/01a07c69-dub"' in src


def test_page_wires_send_to_the_module():
    html = (ROOT / "docs" / "voices.html").read_text(encoding="utf-8")
    assert "voice-select.js" in html
    assert "commitSelection" in html
    assert "buildSelection" in html
    assert "أرسل الاختيار إلى الوكيل" in html


def test_samples_play_from_raw_not_pages():
    """github.io returned HTTP 500 for the sample mp3s (2026-09-10); raw serves
    them. Every player resolves audio through the shared audio-url module, and
    the branch is never percent-encoded (%2F makes raw 404 every URL)."""
    html = (ROOT / "docs" / "voices.html").read_text(encoding="utf-8")
    assert "audio-url.js" in html
    assert "audioCandidates(s.mp3)" in html
    js = (ROOT / "docs" / "auditions-tab.js").read_text(encoding="utf-8")
    assert "audio-url.js" in js
    shared = (ROOT / "docs" / "audio-url.js").read_text(encoding="utf-8")
    assert "raw.githubusercontent.com" in shared
    assert "encodeURIComponent(branch)" not in shared


def test_audio_manifest_names_the_deployment_branch():
    import json
    manifest = json.loads(
        (ROOT / "docs" / "audio-branch.json").read_text(encoding="utf-8")
    )
    js = (ROOT / "docs" / "audio-url.js").read_text(encoding="utf-8")
    assert manifest["branch"] in js, "audio-url.js defaults must include the manifest branch"
    assert manifest["branch"].startswith("arena/"), "deployment branch is an arena session branch"
