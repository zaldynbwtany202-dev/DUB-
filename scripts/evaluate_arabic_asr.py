#!/usr/bin/env python3
"""Compute diagnostic Arabic WER/CER from human-verified JSONL references."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from youtube_auto_dub.asr_evaluation import evaluate_asr_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSONL with one verified recording per line")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        records = []
        for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        report = evaluate_asr_records(records)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError) as exc:
        print(f"ASR evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"overall": report["overall"], "by_dialect": report["by_dialect"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
