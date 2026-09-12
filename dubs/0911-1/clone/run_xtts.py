#!/usr/bin/env python3
"""Clone the original 0911-1 narrator with XTTS-v2 (Arabic)."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("COQUI_TOS_AGREED", "1")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.xtts_clone import clone_speak, load_model

OUT = ROOT / "dubs/0911-1/clone"
REF = OUT / "original/reference.wav"
SCRIPT_PATH = ROOT / "dubs/0911-1/voice17/system/script.json"
VIDEO_REL = "../../../library/0911-1/source.mp4"
MAX_TEMPO = 1.08
SAFETY = 0.95


def _duration(path: Path) -> float:
    import soundfile as sf

    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


def main() -> int:
    t0 = time.time()
    script = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    print(
        f"loading XTTS-v2 cpu  ref={REF} exists={REF.is_file()} "
        f"size={REF.stat().st_size if REF.is_file() else 0}",
        flush=True,
    )
    model = load_model("cpu")
    print(f"model={'ok' if model else 'FAILED'} elapsed={time.time() - t0:.1f}s", flush=True)
    if model is None:
        return 2

    lines = script["lines"]
    segments = []
    ok = 0
    for line in lines:
        idx = int(line["index"])
        dest = OUT / f"seg-{idx:04d}.wav"
        window = float(line["end"]) - float(line["start"])
        budget = window * SAFETY
        print(
            f"clone {idx} {line['start']:.2f}-{line['end']:.2f} "
            f"budget={budget:.2f}s {line['text']!r}",
            flush=True,
        )
        t1 = time.time()
        try:
            success = clone_speak(line["text"], REF, dest, language="ar", device="cpu")
        except Exception:
            traceback.print_exc()
            success = False
        dur = _duration(dest) if success and dest.exists() else 0.0
        need = (dur / budget) if budget > 0 else 99.0
        print(
            f"  -> {'OK' if success else 'FAIL'} dur={dur:.2f}s "
            f"tempo_needed={need:.3f} sec={time.time() - t1:.1f}",
            flush=True,
        )
        if not success:
            continue
        if need > MAX_TEMPO:
            print(f"  OVERRUN cap {MAX_TEMPO}; keeping take for inspection", flush=True)
        ok += 1
        segments.append(
            {
                "index": idx,
                "start": float(line["start"]),
                "end": float(line["end"]),
                "text": line["text"],
                "audio": dest.name,
                "speaker": "NARRATOR",
                "natural_seconds": round(dur, 3),
                "tempo_needed": round(need, 3),
            }
        )

    cues = {
        "video": VIDEO_REL,
        "voice_backend": "agent",
        "voice_id": "clone-original",
        "language": "ar",
        "timing_mode": "source_audio",
        "min_tempo": 1.0,
        "max_tempo": MAX_TEMPO,
        "never_slow_to_fill": True,
        "never_truncate_speech": True,
        "merge": "forbidden",
        "sample_rate": 24000,
        "segments": segments,
    }
    (OUT / "cues.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"done {ok}/{len(lines)} total={time.time() - t0:.1f}s", flush=True)
    over = [s for s in segments if float(s["tempo_needed"]) > MAX_TEMPO]
    if over:
        print("OVERRUN_LINES", [s["index"] for s in over], flush=True)
        return 4
    return 0 if ok == len(lines) else 3


if __name__ == "__main__":
    raise SystemExit(main())
