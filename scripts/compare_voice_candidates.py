#!/usr/bin/env python3
"""Rank candidate narrator voices against the human narrator of the source clip.

The ear decides; this only shortlists. It measures, for every candidate clip, the
things that separate a professional human recording from a synthetic one on this
project - pitch micro-variation, level dynamics, the band the microphone carries,
spectral tilt, and the silence floor - and reports each as a signed distance from
the reference. Jitter/shimmer are deliberately NOT the deciding score: on the Hajj
dream the human narrator measured 20.1 % jitter and the synthetic dub 23.1 %, i.e.
the usual "human" proxies were blind here, so a ranking that leaned on them would
pick the wrong voice.

Numbers are never a certification. Voice ids are per-session. Nothing is written
into the delivered dub from this script.

    python scripts/compare_voice_candidates.py \
        --reference .cache/hajj-sync-repair/source-speech.wav \
        --clips '.cache/gallery/*.flac' \
        --passage dubs/hajj-dream-2108415/voice-tests/gallery/passage.txt \
        --target-rate 2.507 --output dubs/.../candidate-ranking.json --markdown
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402

FRAME = 0.02          # 20 ms frames for level/noise statistics
F0_WIN = 0.0468       # 200 ms pitch window
F0_STEP = 0.01        # 10 ms pitch hop
F0_MIN, F0_MAX = 65.0, 400.0


def decode(path: Path, sr: int = 16000) -> np.ndarray:
    """Mono float32 at ``sr`` straight out of ffmpeg (measuring rate, not playback)."""
    done = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True)
    return np.frombuffer(done.stdout, np.float32).copy()


def _rms(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    size = max(1, int(round(FRAME * sr)))
    frames = x[: x.size // size * size].reshape(-1, size)
    return np.sqrt((frames ** 2).mean(1)), size


def _f0_and_period(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Autocorrelation pitch track: (period in samples, normalised correlation).

    ``as_strided`` framing with an integer count so a window that does not divide the
    segment cannot corrupt the reshape (that bug silently returned garbage before).
    """
    win = max(2, int(round(F0_WIN * sr)))
    step = max(1, int(round(F0_STEP * sr)))
    lag_min = max(2, int(round(sr / F0_MAX)))
    lag_max = min(win // 2, int(round(sr / F0_MIN)))
    count = (x.size - win) // step + 1
    if count <= 0 or lag_max <= lag_min:
        return np.zeros(0), np.zeros(0)
    w = np.lib.stride_tricks.as_strided(
        np.ascontiguousarray(x), shape=(count, win), strides=(x.itemsize * step, x.itemsize)).copy()
    w = w - w.mean(1, keepdims=True)
    energy = (w ** 2).sum(1)
    f = np.fft.rfft(w, n=2 * win)
    ac = np.fft.irfft(f * np.conj(f), n=2 * win)[:, : lag_max + 1]
    denom = np.maximum(energy, 1e-9)
    ac = ac / denom[:, None]
    lag = np.arange(lag_min, lag_max + 1)
    best = ac[:, lag_min: lag_max + 1].argmax(1)
    return lag[best].astype(float), ac[np.arange(count), lag_min + best]


def _voiced(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Frames worth measuring: above the 20th-percentile level and with a strong pitch.

    The p20 level filter must be applied before pitch selection, otherwise trailing
    silence makes every following statistic meaningless.
    """
    period, strength = _f0_and_period(x, sr)
    if period.size == 0:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    level = np.zeros(period.size)
    step = max(1, int(round(F0_STEP * sr)))
    for i in range(period.size):
        a = x[i * step: i * step + 2 * int(period[i]) + 1]
        level[i] = float(np.sqrt((a ** 2).mean())) if a.size else 0.0
    if level.size:
        keep = (level > np.percentile(level, 20)) & (strength > 0.30) & (period > 0)
    else:
        keep = np.zeros(0, bool)
    return period[keep], level[keep], x


def _spectrum_stats(x: np.ndarray, sr: int) -> dict[str, float]:
    win = 4096
    if x.size < win:
        win = 1 << max(6, int(math.log2(max(64, x.size))))
        if x.size < win:
            return {"tilt_db_per_decade": float("nan"), "hf_share": float("nan"),
                    "low_share": float("nan"), "bandwidth_95pct_hz": float("nan")}
    count = x.size // win
    seg = x[: count * win].reshape(count, win) * np.hanning(win)
    pw = (np.abs(np.fft.rfft(seg, axis=1)) ** 2).mean(0)
    freq = np.fft.rfftfreq(win, 1 / sr)
    top = min(0.45 * sr, 8000.0)
    band = (freq >= 120.0) & (freq <= top)
    if band.sum() < 8:
        band = freq >= 120.0
    f, p = freq[band], pw[band]
    with np.errstate(divide="ignore"):
        lp, lf = np.log10(np.maximum(p, 1e-20)), np.log10(f)
    ok = np.isfinite(lp) & np.isfinite(lf)
    tilt = float(np.polyfit(lf[ok], lp[ok], 1)[0]) if ok.sum() > 8 else float("nan")
    total = float(p.sum()) or 1e-12
    hf = float(p[(f > 6000.0)].sum() / total)
    low = float(p[(f < 200.0)].sum() / total)
    cum = np.cumsum(p) / total
    idx = int(np.searchsorted(cum, 0.95))
    return {"tilt_db_per_decade": round(tilt, 2), "hf_share": round(hf, 5),
            "low_share": round(low, 4), "bandwidth_95pct_hz": round(float(f[min(idx, f.size - 1)]), 1)}


def measure(x: np.ndarray, sr: int) -> dict[str, float]:
    period, level, _ = _voiced(x, sr)
    rms, _ = _rms(x, sr)
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    # Dynamics must be measured on speech only: a continuous narration track and a clip with
    # long pauses would otherwise differ by their rests, not by how the voice moves.
    speech = db[db > np.percentile(db, 20) + 6.0] if db.size else db
    stats = {
        "f0_median_hz": float("nan"), "jitter_local_pct": float("nan"),
        "shimmer_local_pct": float("nan"), "voiced_fraction": float("nan"),
        "level_sd_db": round(float(np.std(speech)), 2) if speech.size else float("nan"),
        "floor_dbfs": round(float(np.percentile(db, 20)), 2) if db.size else float("nan"),
        "silent_frame_pct": round(float((db < -60.0).mean() * 100), 2) if db.size else float("nan"),
    }
    if period.size > 3:
        stats["f0_median_hz"] = round(float(sr / np.median(period)), 1)
        d = np.diff(period)
        stats["jitter_local_pct"] = round(float(np.mean(np.abs(d)) / np.mean(period) * 100), 2)
        env = np.abs(x)
        amp = np.array([float(env[int(i) * max(1, int(round(F0_STEP * sr))):][: int(round(2 * period[i]))].max())
                        if int(round(2 * period[i])) > 0 else 0.0 for i in range(period.size)])
        if amp.size > 3 and (amp > 0).sum() > 3:
            da = np.diff(amp)
            stats["shimmer_local_pct"] = round(float(np.mean(np.abs(da[amp[1:] > 0])) / max(1e-9, np.mean(amp[amp > 0])) * 100), 2)
        stats["voiced_fraction"] = round(float(period.size / max(1, x.size / (F0_STEP * sr))), 3)
    stats.update(_spectrum_stats(x, sr))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True,
                    help="human narration to compare against (source minus its bed, or an approved take)")
    ap.add_argument("--clips", required=True, help="glob of candidate clips (flac/wav/mp3)")
    ap.add_argument("--passage", type=Path, help="text the candidates spoke, to count words")
    ap.add_argument("--target-rate", type=float, default=None,
                    help="words/second the source window demands (from phrase-budget.json)")
    ap.add_argument("--output", type=Path, help="where to write the JSON ranking")
    ap.add_argument("--markdown", action="store_true", help="print a markdown table too")
    args = ap.parse_args()

    words = 0
    if args.passage and args.passage.exists():
        words = len(args.passage.read_text(encoding="utf-8").split())

    # Both sides are measured through the same 16 kHz window on purpose: a 24 kHz candidate
    # has no energy above 12 kHz, so comparing its band against a 44.1 kHz reference would
    # only rank sample rates. Any file above that rate is down-sampled, never up-sampled.
    reference = measure(decode(args.reference, 16000), 16000)
    rows = []
    for path in sorted(glob.glob(args.clips)):
        p = Path(path)
        data = decode(p, 16000)
        stats = measure(data, 16000)
        seconds = data.size / 16000
        row = {"clip": p.name, "seconds": round(seconds, 2), "words": words,
               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16], **stats}
        if words:
            row["words_per_sec"] = round(words / seconds, 3)
            if args.target_rate:
                row["rate_fit"] = round(row["words_per_sec"] / args.target_rate, 3)
        # Distance from the human reference. Each term is the *relative* deviation from the
        # reference value, so no single metric swamps the score the way raw dB/percentage
        # points did; weights rank how much each difference actually reads in a dub.
        weights = {"jitter_local_pct": 0.4, "shimmer_local_pct": 0.4, "level_sd_db": 0.5,
                   "low_share": 1.0, "tilt_db_per_decade": 1.0}
        parts = []
        for key, weight in weights.items():
            got, want = stats.get(key, float("nan")), reference.get(key, float("nan"))
            if math.isfinite(got) and math.isfinite(want) and abs(want) > 1e-9:
                rel = round((got - want) / abs(want), 4)
                row[f"delta_{key}"] = round(got - want, 4)
                row[f"rel_{key}"] = rel
                parts.append(abs(rel) * weight)
        if math.isfinite(stats.get("silent_frame_pct", float("nan"))):
            extra = max(0.0, stats["silent_frame_pct"] - reference.get("silent_frame_pct", 0.0))
            row["silent_excess_pct"] = round(extra, 2)
            parts.append(extra / 2.0 * 0.5)
        row["distance_to_reference"] = round(sum(parts), 3)
        rows.append(row)
    rows.sort(key=lambda r: r.get("distance_to_reference", 1e9))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
        if args.target_rate and "rate_fit" in row and row["rate_fit"] > 1.15:
            row["warning"] = "faster than the source window allows - will not fit without stretching"
    doc = {"generated_from": "scripts/compare_voice_candidates.py",
           "caution": ("Shortlist only. Pitch micro-variation separated nothing on this project "
                       "(human 20.1 % jitter vs synthetic 23.1 %), silence floor is a property of the "
                       "master chain and not of a raw take, and every clip is measured through one "
                       "shared 16 kHz window so a 24 kHz candidate is not penalised by Nyquist. "
                       "hf_share and bandwidth_95pct are reported but excluded from the distance. "
                       "The listener decides."),
           "target_words_per_sec": args.target_rate,
           "reference": {"file": str(args.reference), **reference},
           "candidates": rows}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        keys = ["rank", "clip", "seconds", "words_per_sec", "f0_median_hz", "jitter_local_pct",
                "shimmer_local_pct", "level_sd_db", "low_share", "tilt_db_per_decade",
                "silent_frame_pct", "floor_dbfs", "distance_to_reference"]
        print("| " + " | ".join(keys) + " |")
        print("|" + "---|" * len(keys))
        for row in rows:
            print("| " + " | ".join("" if k not in row else str(row[k]) for k in keys) + " |")
    print(json.dumps({"reference": {k: reference[k] for k in
                                    ("jitter_local_pct", "shimmer_local_pct", "level_sd_db",
                                     "low_share", "tilt_db_per_decade", "floor_dbfs")},
                      "ranking": [{"rank": r["rank"], "clip": r["clip"],
                                  "distance_to_reference": r.get("distance_to_reference"),
                                  "words_per_sec": r.get("words_per_sec"),
                                  "warning": r.get("warning")} for r in rows]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
