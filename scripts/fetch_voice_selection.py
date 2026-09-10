#!/usr/bin/env python3
"""Read the voice choice the user picked on docs/voices.html.

The page commits the choice from the browser to one well-known file —
``inbox/voice-selection.json`` on the Sendbox branch — through GitHub's data
API (docs/voice-select.js). This script reads that same file back through
``gh api``, so a choice made in the browser reaches the agent without anything
being copy-pasted into chat.

    python scripts/fetch_voice_selection.py            # show the latest choice
    python scripts/fetch_voice_selection.py --json     # machine-readable
    python scripts/fetch_voice_selection.py --save     # keep a copy in dubs/

Exit codes: 0 = found and valid, 3 = nothing there yet, 4 = found but invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO = os.environ.get("GITHUB_REPOSITORY", "zaldynbwtany202-dev/DUB-")
BRANCH = os.environ.get("VOICE_SELECTION_BRANCH", "arena/01a07c69-dub")
SELECTION_PATH = "inbox/voice-selection.json"
# The voice shop (docs/voice-shop.html) and the raw voice library
# (docs/voice-library.html) write a second channel — same branch, one commit
# per click — naming the catalog slot the agent must obey.
SHOP_CHOICE_PATH = "docs/voice-choice.json"

# Kept in lockstep with docs/voice-select.js DIALECT_CODES.
DIALECT_CODES = {"فصحى": "ar", "مصرية": "ar-EG", "شامية": "ar-Levantine"}


def _gh(path: str) -> str:
    res = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, timeout=180
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300] or "gh api failed")
    return res.stdout


def read_remote(repo: str = REPO, branch: str = BRANCH,
                path: str = SELECTION_PATH) -> dict | None:
    """Return the committed selection, or None when nothing was sent yet.

    Read through the blobs endpoint (like fetch_inbox.py) rather than trusting
    the contents endpoint, whose body comes back empty above ~1 MB.
    """
    try:
        meta = json.loads(_gh(f"repos/{repo}/contents/{path}?ref={branch}"))
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
    blob = json.loads(_gh(f"repos/{repo}/git/blobs/{meta['sha']}"))
    import base64

    return json.loads(base64.b64decode(blob["content"]).decode("utf-8"))


def validate_selection(data: object) -> list[str]:
    """Every way a payload could reach the agent and still be unusable.

    Two shapes arrive on this branch: the samples tab writes `kind:
    voice-selection` with one entry per role; the voice shop and the raw
    library write `schema: 1` with a single `sample_id` naming a catalog
    slot. Both are accepted; anything else is refused.
    """
    if not isinstance(data, dict):
        return ["الاختيار ليس كائن JSON"]
    if data.get("schema") == 1 or ("sample_id" in data and "roles" not in data):
        return _validate_shop_choice(data)
    return _validate_roles_choice(data)


def _validate_shop_choice(data: dict) -> list[str]:
    """The shop's contract: sample_id names the slot, source_catalog says
    which catalog to obey. chain is advisory — set_voice_choice.py re-reads
    the catalog itself and never trusts a filter string from the browser."""
    errors: list[str] = []
    if data.get("schema") != 1:
        errors.append(f"schema غير صحيح: {data.get('schema')!r} — المتوقع 1")
    if data.get("sample_id") in (None, ""):
        errors.append("sample_id مفقود — لا خانة في الكتالوج")
    if not str(data.get("source_catalog") or "").strip():
        errors.append("source_catalog مفقود — أي كتالوج؟")
    if not str(data.get("chosen_at") or data.get("created_at") or "").strip():
        errors.append("لا طابع زمني (chosen_at)")
    return errors


def _validate_roles_choice(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("kind") != "voice-selection":
        errors.append(f"kind غير صحيح: {data.get('kind')!r} — المتوقع voice-selection")
    if not data.get("selection_id"):
        errors.append("selection_id مفقود")
    if not data.get("created_at"):
        errors.append("created_at مفقود")

    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        errors.append("roles فارغ — لا صوت في الاختيار")
        return errors
    for i, role in enumerate(roles):
        label = f"الدور {i + 1}"
        if not isinstance(role, dict):
            errors.append(f"{label}: ليس كائناً")
            continue
        for field in ("role", "voice", "dialect"):
            if not str(role.get(field) or "").strip():
                errors.append(f"{label}: الحقل {field} مفقود")
        code = role.get("dialect_code")
        if code is not None and code not in DIALECT_CODES.values():
            errors.append(
                f"{label}: dialect_code ‏{code!r} غير معروف — "
                f"المعروف: {sorted(DIALECT_CODES.values())}"
            )
    if not isinstance(data.get("note", ""), str):
        errors.append("note يجب أن يكون نصاً")
    return errors


def describe(data: dict) -> str:
    """Arabic summary the agent reads aloud to itself at session start."""
    source = str(data.get("_source_path") or "").strip()
    if data.get("schema") == 1 or ("sample_id" in data and "roles" not in data):
        lines = [
            f"اختيار من متجر الأصوات ({data.get('sample_id', '؟')}) — "
            f"{data.get('chosen_at') or data.get('created_at') or '؟'}"
            + (f" — من {source}" if source else ""),
        ]
        for field in ("title", "base_voice", "dialect", "age", "tone"):
            if data.get(field):
                lines.append(f"  • {field}: {data[field]}")
        if data.get("source_catalog"):
            lines.append(f"  • الكتالوج: {data['source_catalog']}")
        if data.get("how_to_apply"):
            lines.append(f"  • التطبيق: {data['how_to_apply']}")
        return "\n".join(lines)

    lines = [
        f"اختيار صوت من الصفحة ({data.get('selection_id', '؟')}) — "
        f"{data.get('created_at', '؟')}"
        + (f" — من {source}" if source else ""),
    ]
    for role in data.get("roles", []):
        code = role.get("dialect_code") or "؟"
        lines.append(
            f"  • {role.get('role', '؟')}: الصوت {role.get('voice', '؟')} — "
            f"لهجة {role.get('dialect', '؟')} ({code}) — العينة {role.get('sample_id', '؟')}"
        )
    note = str(data.get("note") or "").strip()
    if note:
        lines.append(f"  ملاحظة المستخدم: {note}")
    return "\n".join(lines)


def save_local(data: dict, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    latest = dest_dir / "latest.json"
    latest.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sid = str(data.get("selection_id") or data.get("sample_id") or "unknown")
    bad = set('<>:"/\\|?*')
    safe_id = "".join(c for c in sid if c not in bad) or "unknown"
    archived = dest_dir / f"{safe_id}.json"
    if archived != latest:
        archived.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return latest


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Read the voice choice sent from docs/voices.html"
    )
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    ap.add_argument("--save", action="store_true",
                    help="keep a copy under dubs/voice-selections/")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--branch", default=BRANCH)
    args = ap.parse_args()

    try:
        data = read_remote(args.repo, args.branch)
    except Exception as exc:
        raise SystemExit(f"تعذّر القراءة من GitHub: {exc}")

    if data is None:
        print(f"لا يوجد اختيار بعد — {SELECTION_PATH} غير موجود على {args.branch}.")
        print("أرسله من docs/voices.html بالزر «أرسل الاختيار إلى الوكيل».")
        raise SystemExit(3)

    errors = validate_selection(data)
    if errors:
        print("الاختيار الموجود غير صالح:")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(4)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(describe(data))
    if args.save:
        dest = save_local(data, ROOT / "dubs" / "voice-selections")
        print(f"\nحُفظت نسخة محلية: {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
