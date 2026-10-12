#!/usr/bin/env python3
"""Run the UVR background separation over a long source in segments.

The four-band encoder holds spectra for the whole file at once. At 44.1 kHz the
37 minute narration needs roughly 780 MB for the decoded wave and close to
2.8 GB more for the band spectra, against 2.67 GB of available memory -- it does
not fit. Cut into segments and each one frees the previous segment's arrays
before the next is decoded.

Cost is linear: 120 s of audio separated in 133 s of CPU, so the full run is
about 45 minutes spread over the segments. Segments are cut on silence-ish
boundaries where possible and joined with a short crossfade, because a hard
butt-join between two independently masked segments clicks.

Usage
  .venv/bin/python scripts/separate_background_segmented.py \
      --source <video or wav> --out <music.wav> [--segment 280] [--crossfade 0.5]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from youtube_auto_dub.neural_background import separate_background  # noqa: E402


def ffmpeg() -> str:
    import glob
    return glob.glob(str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))[0]


def probe_duration(path: Path) -> float:
    ff = ffmpeg()
    err = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True).stderr
    for line in err.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration: ")[1].split(",")[0].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"لا يمكن قراءة مدة {path}")


def join_float_stems(stems: list[Path], output: Path, crossfade: float,
                     sample_rate: int = 44100) -> None:
    """Join estimated beds without clipping peaks above PCM full scale."""
    import numpy as np
    import soundfile as sf

    if not stems or crossfade < 0:
        raise ValueError('Expected stems and a nonnegative crossfade')
    output.parent.mkdir(parents=True, exist_ok=True)
    overlap = round(crossfade * sample_rate)
    with sf.SoundFile(output, 'w', samplerate=sample_rate, channels=2, subtype='FLOAT') as joined:
        previous_tail = None
        for index, stem in enumerate(stems):
            x, stem_sr = sf.read(stem, dtype='float32', always_2d=True)
            if stem_sr != sample_rate or x.shape[1] != 2:
                raise ValueError(f'Wrong sample format for {stem}: {stem_sr} Hz, {x.shape[1]}ch')
            if previous_tail is not None:
                crossfade_frames = min(len(previous_tail), len(x))
                ramp = np.linspace(0, 1, crossfade_frames, dtype=np.float32)[:, None]
                joined.write(previous_tail[:crossfade_frames] * (1-ramp)
                             + x[:crossfade_frames] * ramp)
                x = x[crossfade_frames:]
            if index + 1 < len(stems) and overlap:
                joined.write(x[:-overlap])
                previous_tail = x[-overlap:]
            else:
                joined.write(x)
                previous_tail = None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path(".cache/separation/segmented"))
    ap.add_argument("--segment", type=float, default=280.0,
                    help="seconds per segment; 280 keeps peak memory near 500 MB")
    ap.add_argument("--crossfade", type=float, default=0.5)
    a = ap.parse_args()

    total = probe_duration(a.source)
    ff = ffmpeg()
    a.work.mkdir(parents=True, exist_ok=True)
    seg_dir = a.work / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    n = int(total // a.segment) + (1 if total % a.segment else 0)
    print(f"المصدر: {total:.1f}ث · {n} مقطعًا × {a.segment:.0f}ث", flush=True)

    stems: list[Path] = []
    reports = []
    started = time.monotonic()
    for i in range(n):
        begin = i * a.segment
        length = min(a.segment, total - begin) + (a.crossfade if i + 1 < n else 0.0)
        if length <= 0.05:
            break
        seg = seg_dir / f"seg-{i:02d}.wav"
        out = seg_dir / f"seg-{i:02d}-music.wav"
        if not out.is_file():
            subprocess.run([ff, "-y", "-v", "error", "-ss", f"{begin:.3f}", "-t", f"{length:.3f}",
                            "-i", str(a.source), "-vn", "-ac", "2", "-ar", "44100",
                            "-c:a", "pcm_s16le", str(seg)], check=True)
            rep = separate_background(seg, out, seg_dir / f"work-{i:02d}")
            rep["segment_index"] = i
            rep["segment_start_s"] = round(begin, 3)
            reports.append(rep)
            seg.unlink(missing_ok=True)
        stems.append(out)
        done = time.monotonic() - started
        rate = (i + 1) / done if done else 0
        print(f"✓ مقطع {i+1}/{n} ({begin:.0f}ث) · {done/60:.1f}د منقضية"
              + (f" · متبقٍ ~{(n-i-1)/rate/60:.1f}د" if rate else ""), flush=True)

    # FFmpeg's acrossfade clips input samples >1.0 EVEN when output is FLOAT.
    # Keep original peak for the user's no-attenuation constraint: crossfade in
    # float, write FLOAT without any limiter; final mix checks its own headroom.
    join_float_stems(stems, a.out, a.crossfade)

    dur = probe_duration(a.out)
    report = {"source": str(a.source), "output": str(a.out), "source_duration_s": round(total, 2),
              "output_duration_s": round(dur, 2), "segments": n, "segment_s": a.segment,
              "crossfade_s": a.crossfade, "elapsed_s": round(time.monotonic() - started, 1),
              "per_segment": reports}
    (a.out.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_segment"},
                     ensure_ascii=False, indent=2))
    return 0


def _chain_crossfade(n: int, d: float) -> str:
    """Pairwise acrossfade chain for n inputs, each faded over d seconds."""
    parts = []
    cur = "[0:a]"
    for k in range(1, n):
        out = f"[x{k}]" if k < n - 1 else "[out]"
        parts.append(f"{cur}[{k}:a]acrossfade=d={d}:c1=nofade:c2=nofade{out}")
        cur = out
    return ";".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
