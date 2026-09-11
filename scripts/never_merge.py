#!/usr/bin/env python3
"""Refuse to merge the Arena session branch. Exit 1 if a merge is requested."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_auto_dub.source_sync import forbid_merge, load_policy


def current_branch() -> str:
    out = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("target", help="Branch that would receive the merge")
    p.add_argument("--source", help="Defaults to the current git branch")
    args = p.parse_args()
    source = args.source or current_branch()
    policy = load_policy()
    try:
        forbid_merge(source, args.target, policy)
    except PermissionError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
