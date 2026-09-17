#!/usr/bin/env python3
"""Phrase-level SRT from a recording plan (any project).

The word-level SRT that `spoken_word_srt.py` emits has one cue per word (2880 cues
for doctor-lecture), which is right for aligning but unreadable for a human
reviewing the dub. This writes one cue per recording group instead, so the SRT
matches what is actually recorded and placed.

Timing choice: a cue runs from the group's onset to `speech_end` — when the
narrator actually stops — not to `t1`. t1 is the placement window and deliberately
runs past speech_end to the next onset so the take can be stretched into the pause
(what keeps atempo near 1.3x). Subtitling to t1 would leave text on screen during
silence. Groups without `speech_end` (older plans) fall back to t1.

Usage
  python scripts/groups_to_srt.py --groups dubs/X/groups-v2.json --out sub.srt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WRAP = 45   # characters per subtitle line; long cues are split, never truncated


def stamp(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap(text: str, width: int = WRAP) -> str:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--groups", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--use-t1", action="store_true",
                    help="subtitle to the end of the placement window instead of speech_end")
    a = ap.parse_args()

    plan = json.loads(a.groups.read_text(encoding="utf-8"))
    groups = plan["groups"] if isinstance(plan, dict) else plan
    out: list[str] = []
    prev_end = 0.0
    for k, g in enumerate(groups, 1):
        t0 = float(g["t0"])
        t1 = float(g["t1"]) if a.use_t1 else float(g.get("speech_end") or g["t1"])
        t0 = max(t0, prev_end)          # cues never overlap
        t1 = max(t1, t0 + 0.4)          # never a zero-length cue
        prev_end = t1
        out.append(f"{k}\n{stamp(t0)} --> {stamp(t1)}\n{wrap(g['text'])}\n")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(out) + "\n", encoding="utf-8")

    print(json.dumps({
        "out": str(a.out), "cues": len(out),
        "first": [round(groups[0]["t0"], 3), round(float(groups[0].get("speech_end") or groups[0]["t1"]), 3)],
        "last_end": round(float(groups[-1].get("speech_end") or groups[-1]["t1"]), 3),
        "timing": "speech_end (when the narrator stops)" if not a.use_t1 else "t1 (placement window)",
        "words_from": plan.get("words_from") if isinstance(plan, dict) else None,
        "voice_id": plan.get("voice_id") if isinstance(plan, dict) else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
