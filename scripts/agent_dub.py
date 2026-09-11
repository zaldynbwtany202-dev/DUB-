#!/usr/bin/env python3
"""Sync agent-generated speech onto a video. No third-party TTS."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_auto_dub.agent_tts import assemble, cues_from_segments, load_cues, resolve_cue_paths, write_cues


def main() -> int:
    p = argparse.ArgumentParser(description="Place Arena agent voice takes on the source timeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="Write a cue sheet from a segments JSON")
    plan.add_argument("--video", type=Path, required=True)
    plan.add_argument("--segments", type=Path, required=True, help="JSON list or {segments:[...]}")
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--voice-id", default="voice-00")
    plan.add_argument("--language", default="ar-EG")
    plan.add_argument("--audio-dir", type=Path, default=Path("agent_voice"))

    sync = sub.add_parser("sync", help="Tempo-fit agent takes, mix bed, mux video")
    sync.add_argument("--cues", type=Path, required=True)
    sync.add_argument("--output", type=Path, required=True)
    sync.add_argument("--work-dir", type=Path, default=Path("output/agent-work"))
    sync.add_argument("--video", type=Path)
    sync.add_argument("--no-background", action="store_true")
    sync.add_argument("--until", type=float, help="Explicit incomplete prefix preview ending at a cue boundary")

    args = p.parse_args()
    if args.cmd == "plan":
        raw = json.loads(args.segments.read_text(encoding="utf-8"))
        segs = raw["segments"] if isinstance(raw, dict) else raw
        from youtube_auto_dub.source_sync import load_policy, plan_from_asr
        policy = load_policy()
        sheet = plan_from_asr(
            segs,
            words_per_second=float(policy["words_per_second"]),
            min_tempo=float(policy["min_tempo"]),
            max_tempo=float(policy["max_tempo"]),
            audio_dir=str(args.audio_dir),
        )
        sheet["voice_id"] = args.voice_id
        sheet["language"] = args.language
        sheet["video"] = os.path.relpath(args.video.resolve(), args.out.parent.resolve())
        write_cues(args.out, sheet)
        print(json.dumps({"cues": str(args.out), "lines": len(sheet["segments"])}, ensure_ascii=False))
        return 0
    cues = resolve_cue_paths(load_cues(args.cues), args.cues.parent)
    out = assemble(
        cues,
        work_dir=args.work_dir,
        output=args.output,
        mix_background=not args.no_background,
        video=args.video,
        end_time=args.until,
    )
    print(json.dumps({"output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
