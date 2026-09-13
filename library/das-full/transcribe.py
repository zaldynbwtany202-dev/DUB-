#!/usr/bin/env python3
"""Draft ASR for das-full. Words are a working draft, not locked."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "source.mp4"
OUT = HERE / "ASR_SMALL.json"


def main() -> int:
    from faster_whisper import WhisperModel

    if not SRC.is_file():
        raise SystemExit(f"missing {SRC}")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, info = model.transcribe(
        str(SRC),
        language="ar",
        vad_filter=True,
        beam_size=5,
        word_timestamps=False,
    )
    segments = []
    full = []
    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        full.append(text)
        segments.append({
            "start": round(float(s.start), 3),
            "end": round(float(s.end), 3),
            "text": text,
        })
        if len(segments) % 20 == 0:
            print(f"progress segs={len(segments)} t={s.end:.1f}", flush=True)
    payload = {
        "slug": "das-full",
        "source": "library/das-full/source.mp4",
        "engine": "faster-whisper",
        "model": "small",
        "status": "draft_not_locked",
        "language": getattr(info, "language", "ar"),
        "duration": round(float(getattr(info, "duration", 0) or 0), 3),
        "text": " ".join(full),
        "segments": segments,
        "voice_id": "voice-33",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DONE segments={len(segments)} chars={len(payload['text'])} -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
