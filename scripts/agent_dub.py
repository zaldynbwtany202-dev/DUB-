#!/usr/bin/env python3
"""Sync agent-generated speech onto a video. No third-party TTS."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_auto_dub.agent_tts import assemble, cues_from_segments, load_cues, write_cues


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

    args = p.parse_args()
    if args.cmd == "plan":
        raw = json.loads(args.segments.read_text(encoding="utf-8"))
        segs = raw["segments"] if isinstance(raw, dict) else raw
        sheet = cues_from_segments(
            args.video, segs, voice_id=args.voice_id, language=args.language, audio_dir=args.audio_dir,
        )
        write_cues(args.out, sheet)
        print(json.dumps({"cues": str(args.out), "lines": len(sheet["segments"])}, ensure_ascii=False))
        return 0
    cues = load_cues(args.cues)
    # Resolve audio paths relative to the cue file.
    root = args.cues.parent
    for seg in cues["segments"]:
        audio = Path(seg["audio"])
        if not audio.is_absolute():
            cand = (root / audio).resolve()
            if cand.exists() or not Path(seg["audio"]).exists():
                seg["audio"] = str(cand)
    out = assemble(
        cues,
        work_dir=args.work_dir,
        output=args.output,
        mix_background=not args.no_background,
        video=args.video,
    )
    print(json.dumps({"output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
