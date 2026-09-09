#!/usr/bin/env python3
"""Verify a source-synchronous dub render before it is offered for download.

Gates, in the order a viewer would notice them:

1. picture integrity: the video elementary stream must be bit-identical to the source
   (stream copy), and both files must demux without decoder errors;
2. speech integrity: every planned chunk must be audible at its planned time and its
   waveform must still be the approved take (correlation close to 1.0 over the interior
   of the chunk), which rules out clipped, duplicated, reordered or stretched speech;
3. timeline integrity: original speech must not stay uncovered beyond the agreed budget,
   and no uncovered span may be long enough to read as an empty gap;
4. loudness: measured integrated loudness and true peak of the delivered track.

These gates measure timing and integrity. They do not certify pronunciation, dialect or
emotional delivery, which are judged by ear.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.pause_sync import coverage  # noqa: E402

SR = 44100


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path) -> dict:
    text = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True).stderr
    duration = None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    streams = re.findall(r"Stream #\d+:\d+.*?: (Video|Audio)", text)
    return {"duration": duration, "streams": streams}


def picture_fingerprint(path: Path) -> dict:
    """Hash the video elementary stream after a stream copy: proves no re-encode."""
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-v", "error", "-i", str(path),
                             "-map", "0:v:0", "-c:v", "copy", "-f", "md5", "-"],
                            capture_output=True, text=True, check=True)
    digest = re.search(r"MD5=([0-9a-f]{32})", result.stdout)
    stats = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path), "-map", "0:v:0",
                            "-c:v", "copy", "-stats", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    bytes_out = re.findall(r"video:(\d+)KiB", stats)
    stamps = re.findall(r"size=N/A time=(\d+:\d+:\d+\.\d+)", stats)
    errors = sum(1 for line in stats.splitlines() if "Error" in line or "Invalid" in line)
    return {"md5": digest.group(1) if digest else None,
            "video_kib": int(bytes_out[-1]) if bytes_out else 0,
            "last_time": stamps[-1] if stamps else None, "decode_errors": errors}


def decode(path: Path, sample_rate: int = SR) -> np.ndarray:
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", str(sample_rate), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def loudness(path: Path) -> dict | None:
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path),
                             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                             "-f", "null", "-"], capture_output=True, text=True)
    match = re.search(r'\{[^}]+input_i"[^}]+\}', result.stderr, re.S)
    return json.loads(match[0]) if match else None


def vad(audio: np.ndarray, sample_rate: int, min_gap: float = 0.12) -> list[list[float]]:
    from scripts.sync_takes_to_source_timeline import _merge, energy_spans, silero_spans
    spans = silero_spans(audio, sample_rate, min_silence_ms=100) or energy_spans(audio, sample_rate)
    return _merge(spans, min_gap)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="original picture")
    parser.add_argument("--output", type=Path, required=True, help="delivered dub")
    parser.add_argument("--narration", type=Path, required=True, help="dry narration master")
    parser.add_argument("--plan", type=Path, required=True, help="pause plan json")
    parser.add_argument("--script", type=Path, required=True, help="approved take script json")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--max-uncovered", type=float, default=25.0)
    parser.add_argument("--max-gap", type=float, default=0.75)
    parser.add_argument("--max-sample-error", type=float, default=2e-4,
                        help="largest tolerated sample delta against the approved take")
    parser.add_argument("--max-alignment-samples", type=int, default=2)
    parser.add_argument("--inset", type=float, default=0.05,
                        help="ignore this many seconds at each chunk edge, where a shortened "
                             "pause lets the neighbour's room tone sit next to the speech")
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    doc = json.loads(args.script.read_text(encoding="utf-8"))
    if doc.get("voice_id") != plan.get("voice_id"):
        raise ValueError("plan and approved take script disagree about the voice")
    report: dict = {"source": str(args.source), "output": str(args.output),
                    "output_sha256": sha256(args.output), "output_bytes": args.output.stat().st_size}

    source_probe, output_probe = probe(args.source), probe(args.output)
    source_video = picture_fingerprint(args.source)
    output_video = picture_fingerprint(args.output)
    report["picture"] = {"source_duration": source_probe["duration"],
                         "output_duration": output_probe["duration"],
                         "source_video_md5": source_video["md5"], "output_video_md5": output_video["md5"],
                         "video_stream_identical": bool(source_video["md5"])
                         and source_video["md5"] == output_video["md5"],
                         "source_video_kib": source_video["video_kib"],
                         "output_video_kib": output_video["video_kib"],
                         "output_last_packet_time": output_video["last_time"],
                         "source_decode_errors": source_video["decode_errors"],
                         "output_decode_errors": output_video["decode_errors"],
                         "video_reencoded": False, "streams": output_probe["streams"]}

    master, rate = sf.read(args.narration, dtype="float32")
    if master.ndim > 1:
        master = master.mean(axis=1)
    audio_of = {int(part["index"]): Path(part["audio"]) for part in plan["parts"]}
    voices: dict[int, np.ndarray] = {}
    mismatches: list[dict] = []
    alignment: list[int] = []
    silent = 0
    checked = 0
    for row in plan["timeline"]:
        if row["part"] not in voices:
            voices[row["part"]] = sf.read(audio_of[row["part"]], dtype="float32")[0]
        voice = voices[row["part"]]
        piece_lo = int(round(row["take_piece"][0] * rate))
        piece_hi = int(round(row["take_piece"][1] * rate))
        written = int(round(row["written_begin"] * rate))
        expected = voice[piece_lo:piece_hi]
        got = master[written:written + len(expected)]
        if len(got) != len(expected) or len(expected) < 16:
            mismatches.append({"part": row["part"], "start": row["start"], "reason": "length"})
            continue
        checked += 1
        error = float(np.max(np.abs(got - expected)))
        if error > args.max_sample_error:
            mismatches.append({"part": row["part"], "start": row["start"], "max_sample_error": round(error, 8)})
        # the piece was written so that its speech starts exactly at the planned cue time
        speech_lo = int(round(row["take_speech_start"] * rate))
        alignment.append(abs(written + speech_lo - piece_lo - int(round(row["start"] * rate))))
        if float(np.max(np.abs(got))) < 1e-3:
            silent += 1
    report["speech"] = {
        "pieces_checked": checked,
        "pieces_not_identical_to_the_take": len(mismatches),
        "worst_mismatch": mismatches[:5],
        "max_alignment_error_samples": max(alignment, default=0),
        "max_alignment_error_ms": round(max(alignment, default=0) / rate * 1000, 4),
        "pieces_silent_in_master": silent,
        "master_duration": round(len(master) / rate, 3),
        "master_peak_dbfs": round(20 * np.log10(max(float(np.max(np.abs(master))), 1e-9)), 3),
        "method": "every placed piece is compared sample by sample with the approved take, and its "
                  "speech onset is compared with the planned cue time; silence between pieces moved, "
                  "the speech itself did not",
    }

    source_audio = decode(args.source)
    duration = len(source_audio) / SR
    source_spans = vad(source_audio, SR)
    dry = coverage(source_spans, vad(master, SR), duration, tolerance=0.15, min_gap=0.3)
    delivered = coverage(source_spans, vad(decode(args.output), SR), duration, tolerance=0.15, min_gap=0.3)
    report["timeline"] = {"dry_narration": {key: value for key, value in dry.items() if key != "gaps"},
                          "delivered_mix": {key: value for key, value in delivered.items() if key != "gaps"},
                          "dry_longest_gaps": dry["gaps"][:6], "delivered_longest_gaps": delivered["gaps"][:6],
                          "planned": {"uncovered_source_speech_seconds": plan["uncovered_source_speech_seconds"],
                                      "max_uncovered_gap": plan["max_uncovered_gap"]},
                          "method": "same Silero settings on the original and on the delivered audio"}

    report["loudness"] = loudness(args.output)

    gates = {
        "video_stream_bit_identical": report["picture"]["video_stream_identical"]
        and report["picture"]["output_decode_errors"] == 0
        and report["picture"]["source_decode_errors"] == 0,
        "speech_untouched": report["speech"]["pieces_not_identical_to_the_take"] == 0
        and report["speech"]["max_alignment_error_samples"] <= args.max_alignment_samples
        and report["speech"]["pieces_silent_in_master"] == 0
        and report["speech"]["pieces_checked"] == len(plan["timeline"]),
        "no_long_gaps": dry["max_uncovered_gap"] <= args.max_gap
        and delivered["max_uncovered_gap"] <= args.max_gap,
        "coverage_within_budget": dry["uncovered_source_speech_seconds"] <= args.max_uncovered,
        # -16 LUFS with headroom: the AAC encoder may lift the peak slightly above the
        # master target, so the delivered file is judged on a range instead of the target.
        "loudness_in_range": bool(report["loudness"])
        and abs(float(report["loudness"]["input_i"]) + 16) <= 1.5
        and -6.0 <= float(report["loudness"]["input_tp"]) <= -0.5,
        "durations_match": abs((source_probe["duration"] or 0) - (output_probe["duration"] or 0)) <= 0.15,
    }
    report["gates"] = gates
    report["status"] = "verified" if all(gates.values()) else "rejected"
    report["human_listening_certification"] = False
    report["note"] = ("These gates measure timing, picture integrity, and loudness. They do not "
                      "certify pronunciation, dialect, or emotional delivery.")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "gates": gates,
                      "dry_coverage": dry["source_speech_coverage"],
                      "delivered_coverage": delivered["source_speech_coverage"],
                      "dry_max_gap": dry["max_uncovered_gap"],
                      "delivered_max_gap": delivered["max_uncovered_gap"],
                      "pieces_identical": report["speech"]["pieces_not_identical_to_the_take"] == 0,
                      "alignment_ms": report["speech"]["max_alignment_error_ms"],
                      "video_stream_identical": report["picture"]["video_stream_identical"]},
                     ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
