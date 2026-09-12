#!/usr/bin/env python3
"""Transfer the original narrator's F0/formants onto the Arabic takes.

XTTS-v2 weights cannot be fetched from this sandbox (HuggingFace TLS reset),
so this is acoustic speaker matching, not a neural clone.
"""
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
from youtube_auto_dub.voice_profile import convert, measure

OUT = ROOT / "dubs/0911-1/clone"
VOCALS = OUT / "original/vocals.wav"
SRC_DIR = ROOT / "dubs/0911-1/voice17/system"
CUES_IN = SRC_DIR / "cues.json"
SR = 24000


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty decode: {path}")
    return audio


def main() -> int:
    vocals, v_sr = sf.read(str(VOCALS), dtype="float32")
    if vocals.ndim > 1:
        vocals = vocals.mean(axis=1)
    if v_sr != SR:
        # measure() only needs consistent analysis; resample via ffmpeg if needed
        vocals = decode(VOCALS, SR)
        v_sr = SR
    target = measure(vocals, v_sr)
    if target is None or not target.reliable:
        print("original profile unusable", target)
        return 2
    print("target", target)

    cues = json.loads(CUES_IN.read_text(encoding="utf-8"))
    reports = []
    for seg in cues["segments"]:
        src = SRC_DIR / Path(seg["audio"]).name
        take = decode(src, SR)
        source = measure(take, SR)
        converted, report = convert(take, SR, source, target)
        if len(converted) < len(take):
            converted = np.pad(converted, (0, len(take) - len(converted)))
        else:
            converted = converted[: len(take)]
        dest = OUT / f"seg-{int(seg['index']):04d}.wav"
        peak_t = float(np.max(np.abs(take)) or 1.0)
        peak_c = float(np.max(np.abs(converted)) or 1.0)
        converted = (converted * (peak_t / peak_c)).astype(np.float32)
        sf.write(dest, converted, SR, subtype="PCM_24")
        seg["audio"] = dest.name
        reports.append({"index": seg["index"], "file": dest.name, **report, "seconds": round(len(converted) / SR, 3)})
        print(f"converted {dest.name} {report}", flush=True)

    cues["video"] = "../../../library/0911-1/source.mp4"
    cues["voice_id"] = "clone-original"
    cues["voice_backend"] = "agent"
    cues["clone_method"] = "praat_f0_formant_from_original_vocals"
    cues["original_profile"] = {
        "f0_median": target.f0_median,
        "f0_iqr": target.f0_iqr,
        "f1": target.f1,
        "f2": target.f2,
        "f3": target.f3,
    }
    cues["conversions"] = reports
    (OUT / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT / "cues.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
