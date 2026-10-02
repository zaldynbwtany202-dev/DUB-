#!/usr/bin/env python3
"""Word times straight from the film's own audio, one whisper window at a time.

`work/<slug>/full-timed.json` was built from whisper on the *separated dialogue
stem*. That stem lost whole sentences: 208 script words came back with no time
of their own (measured=False) and were packed into 0.08 s stubs. Whisper on the
film's own mix hears them (checked by hand on Into the Wild 430-448 s), so this
script re-reads the mix window by window and keeps every word it hears with the
time it heard it.

    python scripts/whisper_word_times.py library/into-the-wild/source.local.mp4 \
        --start 0 --end 760 --out work/into-the-wild/asr-mix.json --window 30
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ROOT / ".cache/models/whisper-ggml/ggml-small.bin"


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def clip(video: Path, start: float, dur: float, out: Path) -> None:
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out)], check=True)


def tokens_to_words(doc: dict) -> list[dict]:
    """whisper's sub-word tokens joined back into words (a leading space opens one)."""
    words: list[dict] = []
    cur = None
    for seg in doc.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if text.startswith("[") or not text.strip():
                continue
            off = tok.get("offsets", {})
            t0, t1 = off.get("from", 0) / 1000.0, off.get("to", 0) / 1000.0
            if text.startswith(" ") or cur is None:
                if cur:
                    words.append(cur)
                cur = {"w": text.strip(), "t0": t0, "t1": t1}
            else:
                cur["w"] += text
                cur["t1"] = t1
    if cur:
        words.append(cur)
    return [w for w in words if w["w"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    total = sf.info(str(a.video)).duration if not a.end else a.end
    work = a.out.parent / "asr-tmp"
    work.mkdir(parents=True, exist_ok=True)
    words: list[dict] = []
    t = a.start
    i = 0
    while t < total - 0.2:
        dur = min(a.window, total - t)
        wav = work / f"w{i:03d}.wav"
        js = work / f"w{i:03d}.json"
        if not wav.exists():
            clip(a.video, t, dur, wav)
        if not js.exists():
            subprocess.run([str(CLI), "-m", str(MODEL), "-f", str(wav), "-l", "ar",
                            "-ojf", "-of", str(js.with_suffix("")), "-nt"],
                           check=True, capture_output=True)
        doc = json.loads(js.read_text(encoding="utf-8"))
        for w in tokens_to_words(doc):
            words.append({"w": w["w"], "t0": round(t + w["t0"], 3), "t1": round(t + w["t1"], 3)})
        print(f"  نافذة {i}: {t:7.2f}→{t+dur:7.2f}  كلمات {len(words)}", flush=True)
        t += dur
        i += 1

    # النوافذ قد تكرر كلمة على الحدّ: نُبقي الأولى
    clean: list[dict] = []
    for w in words:
        if clean and w["t0"] < clean[-1]["t1"] - 0.02 and w["w"] == clean[-1]["w"]:
            continue
        clean.append(w)
    a.out.write_text(json.dumps(
        {"slug": a.video.parent.name, "source": str(a.video), "range": [a.start, total],
         "asr": "whisper small on the film's own mix (word offsets)", "words": clean},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {a.out}  ({len(clean)} كلمة)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
