#!/usr/bin/env python3
"""Build a source-audio cue sheet. Does not generate voice or merge branches."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.source_sync import load_policy, plan_from_asr
from youtube_auto_dub.agent_tts import write_cues


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--segments", type=Path, required=True, help="ASR JSON with segments[]")
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--voice-id", default="voice-00")
    p.add_argument("--audio-dir", default="agent_voice")
    args = p.parse_args()
    policy = load_policy()
    raw = json.loads(args.segments.read_text(encoding="utf-8"))
    segs = raw["segments"] if isinstance(raw, dict) else raw
    sheet = plan_from_asr(
        segs,
        words_per_second=float(policy["words_per_second"]),
        min_tempo=float(policy["min_tempo"]),
        max_tempo=float(policy["max_tempo"]),
        audio_dir=args.audio_dir,
    )
    sheet["video"] = os.path.relpath(args.video.resolve(), args.out.parent.resolve())
    sheet["voice_id"] = args.voice_id
    sheet["language"] = "ar-EG"
    write_cues(args.out, sheet)
    print(json.dumps({
        "cues": str(args.out),
        "lines": len(sheet["segments"]),
        "min_tempo": sheet["min_tempo"],
        "merge": "forbidden",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
