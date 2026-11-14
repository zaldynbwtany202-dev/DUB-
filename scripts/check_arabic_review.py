#!/usr/bin/env python3
"""Fail-closed check for a hash-bound human approval before release."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from youtube_auto_dub.listener_review import require_listener_approval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--checklist", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--text-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        review = require_listener_approval(args.review, args.audio, args.text_file, args.checklist)
    except (OSError, ValueError) as exc:
        print(f"release blocked: {exc}", file=sys.stderr)
        return 2
    print(f"listener approval verified: reviewer={review['reviewer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
