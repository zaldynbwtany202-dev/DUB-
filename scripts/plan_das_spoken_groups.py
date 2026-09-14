#!/usr/bin/env python3
"""Fluent phrase groups from spoken-word SRT. Not one-word TTS."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "library/das-full/youtube/NB2YcTh_L6k.spoken.json"
OUT = ROOT / "dubs/das-full/youtube-v33"
MAX_SPAN = 24.0
MAX_CHARS = 380


def main() -> int:
    words = json.loads(WORDS.read_text(encoding="utf-8"))["words"]
    groups = []
    i = 0
    n = len(words)
    while i < n:
        t0 = words[i]["t0"]
        j = i
        while j + 1 < n:
            span = words[j + 1]["t1"] - t0
            txt = " ".join(w["w"] for w in words[i : j + 2])
            if (span > MAX_SPAN or len(txt) > MAX_CHARS) and j > i:
                break
            j += 1
        text = " ".join(w["w"] for w in words[i : j + 1])
        groups.append(
            {
                "i": len(groups),
                "w0": i,
                "w1": j,
                "t0": round(words[i]["t0"], 3),
                "t1": round(words[j]["t1"], 3),
                "n": j - i + 1,
                "chars": len(text),
                "text": text,
            }
        )
        i = j + 1
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "voice_id": "voice-33",
        "words_from": "youtube unique.txt",
        "times_from": "NB2YcTh_L6k.spoken.srt",
        "processing": "none",
        "min_tempo": 1.0,
        "not_word_by_word": True,
        "groups": groups,
    }
    (OUT / "groups.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for g in groups:
        (OUT / f"text-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")
    print(f"groups={len(groups)} max_chars={max(g['chars'] for g in groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
