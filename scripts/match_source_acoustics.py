#!/usr/bin/env python3
"""Make the dub sit in the same recording as the original, instead of on top of it.

A voice sounds synthetic in a dub less often because of the timbre and more often because the
acoustics do not match the picture it is laid over. Three numbers say it, measured on the
original's own vocals (source minus its fitted neural bed) against the delivered dub master:

* band: the original has ~0.1% of its energy above 6 kHz (it is a band-limited phone/YouTube
  recording) while the synthesis carries ~5%; that bright, hiss-free-to-16k edge is exactly what
  no camera microphone produces, and the ear reads it as "studio paste";
* proximity: the original keeps ~18% of energy below 200 Hz, the deep male dub 45% - too much
  chest for the same microphone, which sounds boxed-in and processed;
* room tone: the original's floor is -32 dBFS of continuous air, the dub's is -67 dBFS with
  digital silence between phrases. Real rooms never go to zero; silence between words is the
  single clearest tell of an inserted track.

This tool measures those on the source, applies the matching filters to the dub master, and
fills the floor with the room tone taken from the source's own pauses - not invented ambience.
It writes a new master plus a report; it never edits the approved takes and never re-synthesises.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_takes_to_source_timeline import SR, decode  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402


def spectrum_share(audio: np.ndarray, lo: float, hi: float) -> float:
    length = min(len(audio), 90 * SR)
    window = np.asarray(audio[:length], dtype=np.float64)
    power = np.abs(np.fft.rfft(window)) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(length, 1 / SR)
    return float(power[(freqs >= lo) & (freqs < hi)].sum() / power.sum())


def floor_dbfs(audio: np.ndarray, *, frame_s: float = 0.05) -> float:
    size = max(1, int(round(SR * frame_s)))
    usable = (len(audio) // size) * size
    rms = np.sqrt((np.asarray(audio[:usable], float).reshape(-1, size) ** 2).mean(axis=1))
    return float(20 * math.log10(float(np.percentile(rms, 5)) + 1e-12))


def tilt(audio: np.ndarray) -> float:
    length = min(len(audio), 90 * SR)
    power = np.abs(np.fft.rfft(np.asarray(audio[:length], float))) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(length, 1 / SR)
    inside = (freqs >= 250) & (freqs <= 5000)
    return float(np.polyfit(np.log10(freqs[inside]), 10 * np.log10(power[inside]), 1)[0])


def source_room_tone(source: Path, background: Path, pauses: list[list[float]], *,
                     seconds: float = 12.0) -> tuple[np.ndarray, float]:
    """Air taken from the original's own pauses, at the original's own floor."""
    voice = decode(source, SR)
    bed = decode(background, SR)
    count = min(len(voice), len(bed))
    scale = float(np.dot(voice[:count], bed[:count]) / max(1e-12, float(np.dot(bed[:count], bed[:count]))))
    residual = voice[:count] - scale * bed[:count]
    want = int(seconds * SR)
    pieces: list[np.ndarray] = []
    taken = 0
    for start, end in pauses:
        if taken >= want:
            break
        low, high = int(start * SR), int(min(end * SR, count))
        if high - low < int(0.06 * SR):
            continue
        piece = residual[low:high]
        pieces.append(piece[: want - taken] if len(piece) > want - taken else piece)
        taken += len(piece)
    if not pieces:
        raise SystemExit("no usable pauses to lift room tone from")
    air = np.concatenate(pieces)
    return air, floor_dbfs(residual)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True, help="dry dub master (wav)")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True,
                        help="restored neural bed, subtracted to isolate the original vocals")
    parser.add_argument("--pauses", type=Path, required=True, help="source-analysis/vocal-pauses.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--air-gain-db", type=float, default=0.0,
                        help="extra trim on the lifted room tone, dB")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    dub = decode(args.master, SR)
    pauses = json.loads(args.pauses.read_text(encoding="utf-8"))["spans"]
    reference = decode(args.source, SR)
    bed = decode(args.background, SR)
    count = min(len(reference), len(bed))
    scale = float(np.dot(reference[:count], bed[:count]) / max(1e-12, float(np.dot(bed[:count], bed[:count]))))
    reference = reference[:count] - scale * bed[:count]

    def f95(audio: np.ndarray) -> float:
        """Frequency under which 95% of the energy lies - the recording's real bandwidth."""
        length = min(len(audio), 90 * SR)
        power = np.abs(np.fft.rfft(np.asarray(audio[:length], float))) ** 2
        freqs = np.fft.rfftfreq(length, 1 / SR)
        cumulative = np.cumsum(power) / float(power.sum())
        return float(freqs[int(np.searchsorted(cumulative, 0.95))])

    def measure(audio: np.ndarray) -> dict[str, float]:
        return {"hf_share": round(spectrum_share(audio, 6000, 20000), 5),
                "low_share": round(spectrum_share(audio, 0, 200), 5),
                "tilt": round(tilt(audio), 2), "floor_dbfs": round(floor_dbfs(audio), 2),
                "bandwidth_95pct_hz": round(f95(audio), 1)}

    before = {"source": measure(reference), "dub": measure(dub)}
    source, dub_before = before["source"], before["dub"]

    # Proximity: match the sub-200 Hz share with a shelf, never a highpass that removes the chest.
    ratio_low = dub_before["low_share"] / max(1e-6, source["low_share"])
    shelf_db = -round(min(6.0, 10.0 * math.log10(ratio_low)), 2) if ratio_low > 1.3 else 0.0

    def render(extra: list[str]) -> tuple[np.ndarray, dict[str, float]]:
        """Apply the chain to a temp file and measure it, so decisions follow the real output."""
        chain = list(extra) or ["anull"]
        temp = work_dir / "trial.wav"
        done = subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(args.master), "-af",
                               ",".join(chain), "-ar", str(SR), "-ac", "1", str(temp)],
                              capture_output=True)
        if done.returncode:
            raise SystemExit((done.stderr or done.stdout).decode()[-400:])
        data = decode(temp, SR)
        return data, measure(data)

    work_dir = args.output.parent / ".air-tmp"
    work_dir.mkdir(parents=True, exist_ok=True)
    shelf_chain = ([f"bass=g={shelf_db:.2f}:f=180:t=s"] if shelf_db else [])
    # Presence: a deep voice plus a low shelf ends up duller than the microphone beside it, and
    # "muffled" reads as amateur as fast as "bright". Correct the slope, capped.
    tilt_gap = round(render(shelf_chain)[1]["tilt"] - source["tilt"], 2)
    shelf_high = round(min(3.0, -tilt_gap), 2) if tilt_gap < -1.0 else 0.0
    if shelf_high:
        shelf_chain.append(f"treble=g={shelf_high:.2f}:f=3200:t=s")
    shaped, after_shaped = render(shelf_chain)
    # Band: only cut when the processed dub is genuinely brighter than the original recording.
    corner = None
    if after_shaped["hf_share"] > 2.0 * max(1e-5, source["hf_share"]):
        for candidate in (14000.0, 12000.0, 10000.0, 8000.0, 6500.0, 5000.0):
            data, stats = render(shelf_chain + [f"lowpass=f={candidate:.0f}"])
            if stats["hf_share"] <= 2.0 * max(1e-5, source["hf_share"]):
                corner, shaped, after_shaped = candidate, data, stats
                break

    air, air_target = source_room_tone(args.source, args.background, pauses)
    # Presence: a deep voice plus a low shelf ends up duller than the microphone it sits beside,
    # and "muffled" is read as amateur just as fast as "bright". Correct the slope, capped.
    tilt_gap = round(dub_before["tilt"] - source["tilt"], 2)
    shelf_high = 0.0
    if tilt_gap < -1.0:
        shelf_high = round(min(3.0, -tilt_gap), 2)

    voice = shaped
    length = len(voice)
    tile = np.tile(air, int(math.ceil(length / len(air))))[:length]
    level_now = float(np.sqrt(np.mean(tile ** 2))) or 1e-9

    def with_air(target_db: float) -> tuple[np.ndarray, float, float]:
        gain = 10 ** ((target_db + args.air_gain_db) / 20.0) / level_now
        candidate = np.clip(voice + tile * gain, -1.0, 1.0)
        return candidate, floor_dbfs(candidate), 20.0 * math.log10(max(1e-9, gain))

    mixed, floor_now, gain_db = with_air(air_target)
    if abs(floor_now - air_target) > 1.5:  # monotone in the added air: one correction is enough
        mixed, floor_now, gain_db = with_air(air_target + (air_target - floor_now))

    sf.write(args.output, mixed.astype(np.float32), SR, subtype="PCM_24")

    after = measure(mixed)  # measured on the finished master, air included
    after["floor_dbfs"] = round(floor_now, 2)

    report = {"rule": ("the dub is filtered and floored to the original's measured acoustics; "
                       "the ambience under it is the original's own pause air, not composed sound"),
              "filters": {"lowpass_hz": corner, "low_shelf_db": shelf_db,
                          "presence_shelf_db": shelf_high, "air_gain_applied_db": round(gain_db, 2),
                          "chain_applied": shelf_chain + ([f"lowpass=f={corner:.0f}"] if corner else []),
                          "room_tone_source": "source vocals residual, pauses only",
                          "room_tone_tile_seconds": round(len(air) / SR, 2),
                          "room_tone_target_dbfs": round(air_target + args.air_gain_db, 2),
                          "bed_scale_fitted": round(scale, 5)},
              "before": before, "after": after,
              "deltas_vs_source": {key: round(after[key] - source[key], 5)
                                   for key in ("hf_share", "low_share", "tilt", "floor_dbfs",
                                               "bandwidth_95pct_hz")},
              "output": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
              "takes_untouched": True}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
