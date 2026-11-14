#!/usr/bin/env python3
"""Record a human listening verdict for one exact Arabic take.

This does not listen for the user. Run it only after manually hearing the
recording and reviewing the generated checklist.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from youtube_auto_dub.listener_review import create_listener_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checklist", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--pronunciation", choices=("pass", "needs_work"), required=True)
    parser.add_argument("--dialect", choices=("pass", "needs_work", "not_applicable"), required=True)
    parser.add_argument("--naturalness", choices=("pass", "needs_work"), required=True)
    parser.add_argument("--timing", choices=("pass", "needs_work"), required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--attest-listened", action="store_true", required=True,
                        help="attest that you listened to the exact audio named by this checklist")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        review = create_listener_review(
            args.checklist, args.audio, args.text_file,
            reviewer=args.reviewer, pronunciation=args.pronunciation,
            dialect=args.dialect, naturalness=args.naturalness, timing=args.timing,
            notes=args.notes, attested_listened=args.attest_listened,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(review, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"listener review failed: {exc}", file=sys.stderr)
        return 2
    print(f"{review['decision']}: {args.out} (bound to exact audio/script/checklist hashes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
