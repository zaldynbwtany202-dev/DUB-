#!/usr/bin/env python3
"""Restore the Das recap source video into `.cache/` (never into git).

The 151 MB source lives on `arena/01a07c69-dub` as nine browser-split parts under
`inbox/`, with a manifest of per-part SHA-256 digests. `raw.githubusercontent.com`
is unreachable from this sandbox, so parts are fetched through the GitHub API.
Every part is verified before the join, and the joined file must match the
manifest size exactly — a silently short video would surface three stages later.

    python scripts/restore_das_source.py            # fetch, verify, join
    python scripts/restore_das_source.py --check    # report only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".cache/das-full/source.mp4"
PARTS = ROOT / ".cache/das-full/inbox"
REPO = "zaldynbwtany202-dev/DUB-"
BRANCH = "arena/01a07c69-dub"
NAME = ("مغامر فقير انتقل لقريه الوحوش وفكروه ضعيف لكن صدمهم بقوته المرعبة 🔥"
        "- حكاية داس - ملخص انمى كامل.mp4")


def api(path: str, raw: bool = False) -> bytes:
    cmd = ["gh", "api"]
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw"]
    cmd.append(f"repos/{REPO}/contents/{path}?ref={BRANCH}")
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()

    if DEST.exists():
        size = DEST.stat().st_size
        print(json.dumps({"status": "present", "path": str(DEST.relative_to(ROOT)), "bytes": size}))
        return 0
    if args.check:
        print(json.dumps({"status": "missing", "path": str(DEST.relative_to(ROOT))}))
        return 1

    manifest = json.loads(api(urllib.parse.quote(f"inbox/{NAME}.parts.json", safe=""), raw=True))
    PARTS.mkdir(parents=True, exist_ok=True)
    for i, want in enumerate(manifest["sha256"], start=1):
        part = PARTS / f"part{i:02d}.bin"
        if not part.exists() or hashlib.sha256(part.read_bytes()).hexdigest() != want:
            blob = api(urllib.parse.quote(f"inbox/{NAME}.part{i:02d}of{manifest['parts']:02d}", safe=""), raw=True)
            part.write_bytes(blob)
        got = hashlib.sha256(part.read_bytes()).hexdigest()
        if got != want:
            raise SystemExit(f"part {i} SHA mismatch: {got} != {want}")

    with DEST.open("wb") as out:
        for i in range(1, manifest["parts"] + 1):
            out.write((PARTS / f"part{i:02d}.bin").read_bytes())
    size = DEST.stat().st_size
    if size != manifest["size"]:
        raise SystemExit(f"joined size {size} != manifest {manifest['size']}")
    print(json.dumps({"status": "restored", "path": str(DEST.relative_to(ROOT)), "bytes": size,
                      "parts": manifest["parts"], "all_sha_verified": True,
                      "sha256": hashlib.sha256(DEST.read_bytes()).hexdigest()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
