#!/usr/bin/env python
"""Cut the speaker reference for a voice clone out of the original video.

A voice-cloning engine needs one clean, continuous recording of the speaker -- no
music, no other voices. The film gives exactly that once its vocal stem is
isolated, and this picks the best stretch of it: the window with the most speech
and the fewest long silences, trimmed to start and end on speech.

    python scripts/prepare_clone_reference.py --seconds 90

Writes work/<slug>/clone/reference.wav (mono, 22.05 kHz) and reference.txt with
the words spoken in that window, so nothing about the reference is a guess.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imageio_ffmpeg  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
SEG = 280.0


def speech_runs(path: Path, noise: str = "-40dB", min_sil: float = 0.5):
    """Speech spans of a stem, from ffmpeg's own silence detection."""
    p = subprocess.run([FF, "-v", "info", "-i", str(path), "-af",
                        f"silencedetect=noise={noise}:d={min_sil}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts, ends = [], []
    for line in p.stderr.splitlines():
        if "silence_start" in line:
            starts.append(float(line.split("silence_start:")[1].split()[0]))
        elif "silence_end" in line:
            ends.append(float(line.split("silence_end:")[1].split()[0]))
    spans, pos = [], 0.0
    for a, b in zip(starts, ends + [1e9]):
        if a > pos:
            spans.append((pos, a))
        pos = max(pos, b)
    return spans


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--seconds", type=float, default=90.0)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    work = Path("work") / a.slug
    out = a.out or (work / "clone" / "reference.wav")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Score every window of the requested length across the whole film: the most
    # speech inside it wins, so the reference is dense talking, not a quiet scene.
    best = None
    for k in range(7):
        stem = work / "stems" / f"vocal-{k:02d}.mp3"
        if not stem.exists():
            continue
        spans = speech_runs(stem)
        if not spans:
            continue
        total = max(b for _, b in spans)
        step = 5.0
        t = 0.0
        while t + a.seconds <= total:
            inside = sum(max(0.0, min(b, t + a.seconds) - max(x, t)) for x, b in spans)
            if best is None or inside > best[0]:
                best = (inside, k, t, spans)
            t += step
    if best is None:
        raise SystemExit("  ✗ لم أجد كلامًا في المقاطع")

    inside, k, t0, spans = best
    # Snap the edges to speech, so the clip does not open or close on silence.
    # Spans are clipped to the window first: a span can start before it or run
    # past it, and using the raw values put an end before the start.
    win = [(max(x, t0), min(b, t0 + a.seconds)) for x, b in spans
           if b > t0 and x < t0 + a.seconds]
    t0, t1 = min(x for x, _ in win), max(b for _, b in win)
    src = work / "stems" / f"vocal-{k:02d}.mp3"
    x = sf.read(str(src), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    seg = x[int(t0 * SR):int(t1 * SR)].copy()
    peak = float(np.max(np.abs(seg)) + 1e-9)
    seg = seg / peak * 0.89                        # headroom, no clipping
    sf.write(str(out), seg, SR, subtype="PCM_16")

    words = json.loads((work / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    spoken = [w["w"] for w in words if t0 <= w["t0"] <= t1]
    (out.parent / "reference.txt").write_text(
        f"المرجع الصوتي لاستنساخ الراوي — مقتطع من الفيديو الأصلي\n"
        f"المصدر: work/{a.slug}/stems/vocal-{k:02d}.mp3\n"
        f"المدى: {t0:.2f} → {t1:.2f} ثانية ({t1 - t0:.1f} ثانية · كلام {100 * inside / (t1 - t0):.0f}% من المدة)\n"
        f"العينة: 22050 هرتز أحادي · 16 بت · بلا موسيقى\n\n"
        + " ".join(spoken) + "\n", encoding="utf-8")
    print(f"  ✓ {out} — {t1 - t0:.1f} ثانية من vocal-{k:02d} · "
          f"كلام {100 * inside / (t1 - t0):.0f}% · {len(spoken)} كلمة")
    print(f"  ✓ {out.parent / 'reference.txt'}")


if __name__ == "__main__":
    main()
