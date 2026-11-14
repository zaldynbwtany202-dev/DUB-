#!/usr/bin/env python3
"""Measure target-audio word boundaries against human-reviewed timestamps."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from youtube_auto_dub.content_validation import word_timing_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="JSON with speech_start/end, observed_words and a human reference")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        report = word_timing_report(
            data["observed_words"], data["speech_start"], data["speech_end"],
            reference=data.get("reference"),
            tolerance_seconds=float(data.get("tolerance_seconds", 0.15)),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"word alignment evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: report.get(key) for key in (
        "status", "sync_verified", "mean_abs_boundary_error_seconds",
        "max_abs_boundary_error_seconds", "review_flags",
    )}, ensure_ascii=False))
    return 0 if report["sync_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
