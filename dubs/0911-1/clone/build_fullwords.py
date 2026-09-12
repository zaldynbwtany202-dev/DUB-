#!/usr/bin/env python3
"""Rebuild 0911-1 from the complete 4-take script, island-synced to the original."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
from youtube_auto_dub.island_sync import layout_islands
from youtube_auto_dub.voice_profile import convert, measure

SR = 24000
OUT = ROOT / "dubs/0911-1/clone/fullwords"
VIDEO = ROOT / "library/0911-1/source.mp4"
VOCALS = ROOT / "dubs/0911-1/clone/original/vocals.wav"
MUSIC = ROOT / "dubs/0911-1/clone/original/music.wav"
TAKES = ROOT / "dubs/0911-1/voice17"

# Combined soundtrack windows (DTW onset → next group onset) matching the
# 4 complete takes that still have the story words the 8-line cut deleted.
LINES = [
    {
        "index": 0,
        "start": 0.90,
        "end": 9.86,
        "audio_src": TAKES / "seg-0000.mp3",
        "text": "فضل يضحك ويبتسم. بعد سنين طويلة وهو يشرب مية من الطين في أرض المعركة، أخيرا لقى مية نضيفة.",
    },
    {
        "index": 1,
        "start": 9.86,
        "end": 18.04,
        "audio_src": TAKES / "seg-0001.mp3",
        "text": "والدفعة دي كانت رفاهية على البطن. وعلى ما الليل جه، أخد قراره إنه يعمل كل اللي يقدر عليه.",
    },
    {
        "index": 2,
        "start": 18.04,
        "end": 27.94,
        "audio_src": TAKES / "seg-0002.mp3",
        "text": "ويدي المكان فرصة أخيرة. لكن لو الدنيا فضلت مقفلة، بعد يومين هيسيب المكان قبل ما يموت هنا.",
    },
    {
        "index": 3,
        "start": 27.94,
        "end": 32.25,
        "audio_src": TAKES / "seg-0003.mp3",
        "text": "ويدور على قرية تانية، ومعاه وصية أهله.",
    },
]


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty decode {path}")
    return audio


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vocals = decode(VOCALS)
    target = measure(vocals, SR)
    if target is None:
        raise SystemExit("cannot measure original narrator")
    print("target", target, "reliable", target.reliable)

    cues_segments = []
    reports = []
    for line in LINES:
        take = decode(line["audio_src"])
        source = measure(take, SR)
        converted, crep = convert(take, SR, source, target, max_semitones=5.0, max_formant_ratio=0.20)
        if len(converted) < len(take):
            converted = np.pad(converted, (0, len(take) - len(converted)))
        else:
            converted = converted[: len(take)]
        peak_t = float(np.max(np.abs(take)) or 1.0)
        peak_c = float(np.max(np.abs(converted)) or 1.0)
        converted = (converted * (peak_t / peak_c)).astype(np.float32)

        start_i = round(line["start"] * SR)
        end_i = round(line["end"] * SR)
        orig_win = vocals[start_i:end_i]
        laid, lrep = layout_islands(converted, orig_win, SR, min_tempo=1.0, max_tempo=1.08)
        dest = OUT / f"seg-{line['index']:04d}.wav"
        sf.write(dest, laid, SR, subtype="PCM_24")
        print(
            f"line {line['index']} {line['start']:.2f}-{line['end']:.2f} "
            f"tempo={lrep['tempo']:.3f} islands {lrep['dub_islands']}->{lrep['original_islands']} "
            f"speech={lrep['speech_seconds']}s pitch={crep['pitch_semitones']:.2f}st",
            flush=True,
        )
        reports.append({"index": line["index"], **crep, **lrep, "file": dest.name})
        cues_segments.append({
            "index": line["index"],
            "start": line["start"],
            "end": line["end"],
            "text": line["text"],
            "audio": dest.name,
            "speaker": "NARRATOR",
            "pre_aligned": True,
        })

    cues = {
        "video": "../../../library/0911-1/source.mp4",
        "background_audio": "../original/music.wav",
        "voice_backend": "agent",
        "voice_id": "clone-original-fullwords",
        "language": "ar-EG",
        "timing_mode": "source_audio",
        "min_tempo": 1.0,
        "max_tempo": 1.08,
        "never_slow_to_fill": True,
        "never_truncate_speech": True,
        "merge": "forbidden",
        "sample_rate": SR,
        "align": "original_speech_islands",
        "segments": cues_segments,
        "conversions": reports,
    }
    (OUT / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    serial = [{k: (str(v) if isinstance(v, Path) else v) for k, v in line.items()} for line in LINES]
    (OUT / "script.json").write_text(
        json.dumps({"lines": serial, "note": "fuller story than the 8-line cut; last line still misses طريقة/قبل ما يموت"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
