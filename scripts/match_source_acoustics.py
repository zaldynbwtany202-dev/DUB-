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


def pause_spans(path: Path, *, max_mean_seconds: float = 1.0) -> tuple[list[list[float]], str, dict]:
    """Pick the breaths, and prove the pick is not the speech.

    ``vocal-pauses.json`` keeps the original's speech units under ``spans`` and the real
    pauses under ``pause_list``. Reading the wrong key pours the narrator's own voice
    under the dub - the exact defect this whole project exists to remove - so the key is
    chosen by shape (short mean duration) and the result is checked against the ASR word
    intervals before anything is cut out of the recording.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    # Several of these keys are counts rather than lists in this file (``pauses: 124``),
    # so every candidate is taken only when it really is a list of spans.
    def pairs(value, keys=None):
        if not isinstance(value, list):
            return []
        rows = []
        for row in value:
            if isinstance(row, dict) and keys:
                if all(k in row for k in keys):
                    rows.append([float(row[keys[0]]), float(row[keys[1]])])
            elif isinstance(row, (list, tuple)) and len(row) == 2 and all(
                    isinstance(v, (int, float)) for v in row):
                rows.append([float(row[0]), float(row[1])])
        return rows

    candidates = {"pause_list": pairs(doc.get("pause_list"), ("start", "end")),
                  "pauses": pairs(doc.get("pauses"))}
    chosen = None
    for key, rows in candidates.items():
        if len(rows) >= 8:
            mean = sum(hi - lo for lo, hi in rows) / len(rows)
            if mean <= max_mean_seconds:
                chosen, spans = key, rows
                break
    if chosen is None:
        raise SystemExit("no usable pause spans in " + str(path) + " - refusing to guess: ambience "
                         "must come from real silence, never from speech")
    words_file = path.parent / "words.json"
    check = {"key_used": chosen, "count": len(spans),
             "total_seconds": round(sum(hi - lo for lo, hi in spans), 3),
             "mean_seconds": round(sum(hi - lo for lo, hi in spans) / len(spans), 3),
             "max_seconds": round(max(hi - lo for lo, hi in spans), 3)}
    if words_file.is_file():
        words = json.loads(words_file.read_text(encoding="utf-8")).get("words", [])
        # ASR envelopes are generous: they cover the breath before and after a word by
        # 60-100 ms, so an unshrunk interval makes every real breath look like speech. Only
        # the core of each word counts, which still catches a tile cut out of the speech units.
        shrink, overlap = 0.06, 0.0
        for lo, hi in spans:
            for row in words:
                w_lo = float(row.get("start", 0)) + shrink
                w_hi = float(row.get("end", 0)) - shrink
                if w_hi <= w_lo or w_hi < lo or w_lo > hi:
                    continue
                overlap += max(0.0, min(hi, w_hi) - max(lo, w_lo))
        check["words_file"] = str(words_file.name)
        check["seconds_overlapping_word_cores"] = round(overlap, 3)
        check["overlap_fraction"] = round(overlap / max(1e-6, check["total_seconds"]), 4)
        # Recorded rather than fatal above a fraction: on this source the narrator's
        # energy-VAD pauses still sit inside generous ASR word tails (63%), and the physical
        # level test below is what actually separates air from voice. This number only has
        # to stop a tile taken wholesale out of the speech units.
        if check["overlap_fraction"] > 0.9:
            raise SystemExit(f"the pause list overlaps {check['overlap_fraction']:.0%} of ASR words; "
                             "this is not silence and will not be used as room tone")
    return spans, chosen, check


def source_room_tone(source: Path, background: Path, pauses: list[list[float]], *,
                     seconds: float = 12.0, min_gap_db: float = 10.0) -> tuple[np.ndarray, float, dict]:
    """Air taken from the original's own pauses, at the level those pauses actually sit at."""
    voice = decode(source, SR)
    bed = decode(background, SR)
    count = min(len(voice), len(bed))
    scale = float(np.dot(voice[:count], bed[:count]) / max(1e-12, float(np.dot(bed[:count], bed[:count]))))
    residual = voice[:count] - scale * bed[:count]
    want = int(seconds * SR)
    fade = int(0.008 * SR)
    pieces: list[np.ndarray] = []
    taken = 0
    for start, end in pauses:
        if taken >= want:
            break
        low, high = int(start * SR), int(min(end * SR, count))
        if high - low < int(0.06 * SR):
            continue
        piece = residual[low:high].astype(np.float32)
        if taken and fade * 2 < len(piece):  # equal-power joins, so the tile has no clicks
            edge = np.hanning(2 * fade + 1)[fade + 1:fade + 1 + fade]
            pieces[-1][-fade:] *= np.sqrt(1 - edge ** 2)
            piece = piece.copy()
            piece[:fade] *= np.sqrt(edge ** 2)
            pieces[-1] = np.concatenate([pieces[-1], piece])
        else:
            pieces.append(piece)
        taken = sum(len(x) for x in pieces)
    if not pieces:
        raise SystemExit("no usable pauses to lift room tone from")
    air = np.concatenate(pieces)[:want]
    mask = np.ones(count, bool)
    for start, end in pauses:
        mask[int(start * SR):int(min(end * SR, count))] = False
    # This source is a phone recording whose pauses sit only ~13 dB under its speech, so a
    # "silence must be very quiet" rule in absolute dB would be nonsense. What is checked
    # instead is that the lifted material sits at the recording's own floor and clearly under
    # its typical speech - which is what fails when a tile is cut out of speech by mistake.
    size = int(0.05 * SR)
    frames = np.sqrt((residual[: (count // size) * size].reshape(-1, size) ** 2).mean(1))
    frame_mask = mask[: (count // size) * size].reshape(-1, size).any(1)
    speech_db = float(20 * math.log10(float(np.median(frames[frame_mask])) + 1e-12))
    floor_db = float(20 * math.log10(float(np.percentile(frames[~frame_mask], 5)) + 1e-12))
    quiet_db = 20 * math.log10(max(float(np.sqrt((air ** 2).mean())), 1e-9))
    gap_db = speech_db - quiet_db
    if gap_db < min_gap_db:
        raise SystemExit(f"the lifted material is only {gap_db:.1f} dB below typical speech "
                         f"(needs {min_gap_db:.0f}); "
                         "that is voice, not room tone - refusing to lay it under the dub")
    if quiet_db > floor_db + 3.0:
        raise SystemExit(f"the lifted tile ({quiet_db:.1f} dBFS) is louder than the recording's own "
                         f"floor ({floor_db:.1f} dBFS); it is not ambience")
    return air, quiet_db, {"tile_seconds": round(len(air) / SR, 3), "tile_level_dbfs": round(quiet_db, 2),
                           "level_below_speech_db": round(gap_db, 2),
                           "recording_floor_dbfs": round(floor_db, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True, help="dry dub master (wav)")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True,
                        help="restored neural bed, subtracted to isolate the original vocals")
    parser.add_argument("--pauses", type=Path, required=True, help="source-analysis/vocal-pauses.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--air-source", choices=("pauses", "none"), default="pauses",
                        help="'pauses' lifts the room tone out of the original's own breaths and "
                             "refuses it if those breaths are not quiet enough; 'none' matches "
                             "only the band and the slope and adds nothing at all")
    parser.add_argument("--air-fallback", choices=("reject", "none"), default="none",
                        help="what to do when the lifted air fails the quietness test: give up, "
                             "or publish the EQ-only match and say so in the report")
    parser.add_argument("--air-min-gap-db", type=float, default=10.0,
                        help="the lifted tile must sit at least this far under the source's "
                             "typical speech level, or it is voice and gets refused")
    parser.add_argument("--air-gain-db", type=float, default=0.0,
                        help="extra trim on the lifted room tone, dB")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    dub = decode(args.master, SR)
    pauses, pause_key, pause_check = pause_spans(args.pauses)
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

    air, air_target, air_info = None, None, {"added": False, "requested": args.air_source,
                                              "reason": "no ambience requested"}
    if args.air_source == "pauses":
        try:
            air, air_target, air_info = source_room_tone(args.source, args.background, pauses,
                                                         min_gap_db=args.air_min_gap_db)
            air_info["added"] = True
        except SystemExit as error:
            if args.air_fallback == "reject":
                raise
            air_info = {"added": False, "requested": "pauses", "refused": str(error)}
    voice = shaped
    length = len(voice)
    if air is None:
        mixed, floor_now, gain_db = np.clip(voice, -1.0, 1.0), floor_dbfs(voice), float("nan")
        sf.write(args.output, mixed.astype(np.float32), SR, subtype="PCM_24")
        after = measure(mixed)
        report = {"rule": "the dub is filtered to the original's measured band and slope; nothing "
                          "was added under it, because this recording has no quiet air to lift "
                          "without lifting the narrator's voice with it",
                  "filters": {"lowpass_hz": corner, "low_shelf_db": shelf_db,
                              "presence_shelf_db": shelf_high,
                              "chain_applied": shelf_chain + ([f"lowpass=f={corner:.0f}"] if corner else []),
                              "room_tone": air_info, "pause_spans": pause_check,
                              "bed_scale_fitted": round(scale, 5)},
                  "before": before, "after": after,
                  "deltas_vs_source": {key: round(after[key] - source[key], 4) for key in after},
                  "takes_untouched": True,
                  "output": str(args.output), "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
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
                          "room_tone": {**air_info, "added": True, "gain_db": round(gain_db, 2),
                                        "target_dbfs": round(float(air_target), 2),
                                        "fitted_floor_dbfs": round(float(floor_now), 2)},
                          "room_tone_source": "source vocals residual, between its own pauses",
                          "pause_spans": pause_check, "room_tone_measured": air_info,
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
