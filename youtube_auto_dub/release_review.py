"""Fail-closed project release check for human-reviewed audio assets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from youtube_auto_dub.listener_review import require_listener_approval


def _resolve(base: Path, raw: Any, label: str, asset_id: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"asset {asset_id}: missing {label} path")
    path = Path(raw)
    return path if path.is_absolute() else (base / path).resolve()


def check_release_manifest(manifest_path: Path) -> dict[str, Any]:
    """Require one fresh human approval for every expected release asset."""
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is missing or invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("release manifest must use schema_version 1")
    expected = manifest.get("expected_assets")
    final_asset_id = manifest.get("final_asset_id")
    assets = manifest.get("assets")
    if (not isinstance(expected, list) or not expected
            or not all(isinstance(value, str) and value.strip() for value in expected)
            or not isinstance(assets, list) or not assets
            or not isinstance(final_asset_id, str) or not final_asset_id.strip()):
        raise ValueError("release manifest needs expected_assets, final_asset_id and assets lists")
    expected = [value.strip() for value in expected]
    final_asset_id = final_asset_id.strip()
    if final_asset_id not in expected:
        raise ValueError("final_asset_id must be included in expected_assets")
    if len(set(expected)) != len(expected):
        raise ValueError("expected_assets contains duplicate IDs")
    by_id = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("asset_id"), str):
            raise ValueError("each release asset needs an asset_id")
        asset_id = asset["asset_id"].strip()
        if not asset_id or asset_id in by_id:
            raise ValueError(f"empty or duplicate release asset ID: {asset_id!r}")
        by_id[asset_id] = asset
    if set(by_id) != set(expected):
        missing = sorted(set(expected) - set(by_id))
        unexpected = sorted(set(by_id) - set(expected))
        raise ValueError(f"release asset coverage mismatch: missing={missing}, unexpected={unexpected}")

    base = manifest_path.parent
    verified = []
    for asset_id in expected:
        asset = by_id[asset_id]
        paths = {
            key: _resolve(base, asset.get(key), key, asset_id)
            for key in ("review", "checklist", "audio", "text_file")
        }
        try:
            review = require_listener_approval(
                paths["review"], paths["audio"], paths["text_file"], paths["checklist"],
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"asset {asset_id}: {exc}") from exc
        verified.append({"asset_id": asset_id, "reviewer": review["reviewer"],
                         "audio_sha256": review["audio_sha256"],
                         "script_sha256": review["script_sha256"]})
    return {
        "schema_version": 1,
        "project": str(manifest.get("project", "")).strip(),
        "final_asset_id": final_asset_id,
        "release_ready": True,
        "human_approved_assets": len(verified),
        "assets": verified,
        "warning": "Approval covers only the listed bytes. Include the final mixed/rendered audio as an asset if it differs from the reviewed takes.",
    }
