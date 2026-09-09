#!/usr/bin/env python3
"""Re-time approved agent takes against the original speech timeline, pause by pause.

This never synthesizes speech, never time-stretches it, and never re-encodes the
picture. It measures where each approved recording breathes, then re-lays those
complete spoken chunks so the dub speaks whenever the source speaks.

Safety properties, all asserted rather than assumed:

* every cut lands on a local minimum inside measured silence, so no syllable is clipped;
* the speech of two chunks never overlaps;
* a part whose recording is longer than its window borrows from the next part instead
  of being truncated or sped up;
* the narration master ends inside the picture duration.

Outputs:
  narration wav   one continuous 44.1 kHz dry narration master for the mixer
  plan json       per-part and per-chunk placement plus the measured coverage report
  srt             subtitles rebuilt from the same chunk placement
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
from youtube_auto_dub.pause_sync import (align_sentences, allocate_part_spans, coverage,  # noqa: E402
                                         overlap_seconds, plan_gaps)

SR = 44100
VAD_SR = 16000
SAMPLE_TOLERANCE = 3  # samples: rounding of a placement to the sample grid
SENTENCE = re.compile(r"(?<=[.!?؟])\s+")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compare_with_archive(audio: Path, archive: dict) -> np.ndarray | None:
    """Return the take samples when they match the archived FLAC exactly, else None.

    The approved recordings live in the repository as FLAC. A working copy may be a
    different container; matching samples is the strongest statement the ear supports.
    """
    if not archive.get("sha256") or not archive.get("path"):
        return None
    cached = ROOT / ".cache" / "voice-archive-downloads" / f"{archive['sha256']}.flac"
    if not cached.is_file() or sha256(cached) != archive["sha256"]:
        return None
    archived, working = decode(cached, SR), decode(audio, SR)
    if len(archived) != len(working) or not np.array_equal(archived, working):
        return None
    return working


def decode(path: Path, sample_rate: int) -> np.ndarray:
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", str(sample_rate), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def silero_spans(audio: np.ndarray, sample_rate: int, *, min_silence_ms: int) -> list[list[float]]:
    """Speech spans in seconds from Silero VAD, or [] when the wheel is unavailable."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:
        return []
    mono = audio if sample_rate == VAD_SR else _resample(audio, sample_rate, VAD_SR)
    options = VadOptions(threshold=0.5, min_speech_duration_ms=90,
                         min_silence_duration_ms=min_silence_ms, speech_pad_ms=20,
                         max_speech_duration_s=30)
    return [[row["start"] / VAD_SR, row["end"] / VAD_SR]
            for row in get_speech_timestamps(mono, options, sampling_rate=VAD_SR)]


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    import io
    buffer = io.BytesIO()
    sf.write(buffer, audio, source_rate, format="WAV", subtype="FLOAT")
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", "pipe:0", "-ac", "1",
                          "-ar", str(target_rate), "-f", "f32le", "-"],
                         input=buffer.getvalue(), capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def energy_spans(audio: np.ndarray, sample_rate: int) -> list[list[float]]:
    """Deterministic fallback detector so the tool runs without the VAD wheel."""
    frame = max(1, round(sample_rate * 0.01))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    peak = float(np.max(np.abs(audio))) or 1.0
    loud = rms > peak * 10 ** (-42 / 20)
    edges = np.diff(np.r_[False, loud, False].astype(np.int8))
    return [[low * frame / sample_rate, high * frame / sample_rate]
            for low, high in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
            if (high - low) * frame / sample_rate >= 0.12]


def measure(audio: np.ndarray, sample_rate: int, *, min_gap: float,
            max_pad: float) -> tuple[list[dict[str, float]], list[float]]:
    """Split a take into spoken chunks with the silence that belongs to each side.

    Returns one row per chunk (``speech_start``/``speech_end`` inside ``piece_start``
    /``piece_end``, all in seconds of the take) plus the recorded pause length between
    consecutive chunks. Cuts sit on a local minimum near the middle of each pause.
    """
    spans = silero_spans(audio, sample_rate, min_silence_ms=max(60, int(min_gap * 1000) - 20))
    if not spans:
        spans = energy_spans(audio, sample_rate)
    if not spans:
        raise ValueError("no speech measured in take")
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] < min_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    duration = len(audio) / sample_rate
    window = round(max_pad, 6)
    cuts = [0.0]
    for previous, following in zip(merged, merged[1:]):
        gap_start, gap_end = previous[1], following[0]
        middle = (gap_start + gap_end) / 2
        cuts.append(_quietest(audio, sample_rate, middle, window, gap_start, gap_end))
    cuts.append(duration)
    chunks = []
    for index, speech in enumerate(merged):
        piece_start, piece_end = cuts[index], cuts[index + 1]
        chunks.append({"piece_start": round(piece_start, 6), "piece_end": round(piece_end, 6),
                       "speech_start": round(max(speech[0], piece_start), 6),
                       "speech_end": round(min(speech[1], piece_end), 6),
                       "duration": round(speech[1] - speech[0], 6)})
    pauses = [round(merged[i + 1][0] - merged[i][1], 6) for i in range(len(merged) - 1)]
    return chunks, pauses


def _quietest(audio: np.ndarray, sample_rate: int, at: float, window: float,
              not_before: float, not_after: float) -> float:
    """Lowest-energy point near ``at``, clamped inside the measured pause.

    The clamp is what guarantees that every chunk still holds all of its speech: the
    edit point can never drift into a word, only sit where the recorder was silent.
    """
    centre = int(round(at * sample_rate))
    low = max(int(not_before * sample_rate), centre - int(window * sample_rate))
    high = min(len(audio), min(int(not_after * sample_rate), centre + int(window * sample_rate)))
    if high - low < 8:
        return at
    frame = max(1, int(0.004 * sample_rate))
    segment = audio[low:high]
    blocks = len(segment) // frame or 1
    power = [float(np.max(np.abs(segment[i * frame:(i + 1) * frame]))) for i in range(blocks)]
    best = int(np.argmin(power))
    return round((low + (best + 0.5) * frame) / sample_rate, 6)


def build(args: argparse.Namespace) -> dict:
    doc = json.loads(args.script.read_text(encoding="utf-8"))
    if doc.get("voice_backend") != "agent":
        raise ValueError("only agent-recorded takes are accepted")
    source_audio = decode(args.source, SR)
    source_duration = len(source_audio) / SR
    source_spans = [[row[0], row[1]] for row in
                    _merge(silero_spans(source_audio, SR, min_silence_ms=60)
                           or energy_spans(source_audio, SR), args.min_gap)]

    parts = []
    for take in sorted(doc["takes"], key=lambda item: item["index"]):
        audio = (args.script.parent / take["audio"]).resolve()
        if not audio.is_file():
            raise FileNotFoundError(f"approved recording is missing: {audio}")
        digest = sha256(audio)
        integrity = "byte-identical to the approved recording"
        if digest not in {take.get("sha256"), take.get("tool_wav_sha256")}:
            # A restored working copy can differ in container while holding the same
            # samples, which is all the ear can tell. Prove it against the archive.
            archive = take.get("lossless_archive") or {}
            restored = compare_with_archive(audio, archive)
            if restored is None:
                raise ValueError(f"take {take['index']} audio changed since it was approved")
            voice, integrity = restored, ("sample-identical to the approved FLAC archive "
                                          f"{archive.get('sha256', '')[:12]}…; container differs")
        else:
            voice = decode(audio, SR)
        chunks, pauses = measure(voice, SR, min_gap=args.min_gap, max_pad=args.edge_pad)
        durations = [chunk["duration"] for chunk in chunks]
        sentences = align_sentences(SENTENCE.split(str(take["text"])), durations)
        parts.append({"index": int(take["index"]), "window_start": float(take["start"]),
                      "window_end": float(take["end"]), "text": take["text"],
                      "audio": str(audio), "audio_sha256": digest, "integrity": integrity,
                      "take_duration": round(len(voice) / SR, 3), "voice": voice,
                      "chunks": chunks, "durations": durations, "recorded_pauses": pauses,
                      "chunk_texts": sentences,
                      "speech_seconds": round(sum(durations), 3),
                      "pause_seconds": round(sum(pauses), 3)})
    for part in parts:
        pauses = len(part["durations"]) - 1
        part["min_span"] = round(sum(part["durations"]) + args.lead + max(pauses, 0) * args.min_gap
                                 + args.tail, 3)
        part["max_span"] = round(sum(part["durations"]) + args.lead + max(pauses, 0) * args.max_gap
                                 + args.tail, 3)
    windows = []
    for position, part in enumerate(parts):
        stop = parts[position + 1]["window_start"] if position + 1 < len(parts) else source_duration - args.hold_back
        windows.append(round(stop - part["window_start"], 6))
    spans = allocate_part_spans(windows, [p["min_span"] for p in parts],
                                [p["max_span"] for p in parts], round(sum(windows), 6))

    buffer_length = round((source_duration + args.hold_back) * SR)
    narration = np.zeros(buffer_length, dtype=np.float32)
    timeline: list[dict] = []
    specs: list[dict] = []
    cursor = parts[0]["window_start"] if parts else 0.0
    for part, span in zip(parts, spans):
        gaps = plan_gaps(part["recorded_pauses"],
                         span - part["speech_seconds"] - args.lead - args.tail,
                         min_gap=args.min_gap, max_gap=args.max_gap)
        at = cursor + args.lead
        for chunk, gap, text in zip(part["chunks"], gaps + [0.0], part["chunk_texts"]):
            speech_lo = int(round(chunk["speech_start"] * SR))
            speech_hi = int(round(chunk["speech_end"] * SR))
            specs.append({"part": part["index"], "voice": part["voice"],
                          "begin": int(round((at + chunk["piece_start"] - chunk["speech_start"]) * SR)),
                          # integer bounds may sit one sample inside the speech; widen to cover it
                          "lo": min(int(chunk["piece_start"] * SR), speech_lo),
                          "hi": max(int(chunk["piece_end"] * SR), speech_hi),
                          "speech_lo": speech_lo, "speech_hi": speech_hi,
                          "at": at, "duration": chunk["duration"], "text": text})
            if len(specs) > 1 and at < specs[-2]["at"] + specs[-2]["duration"] - 1e-6:
                raise ValueError("spoken chunks overlap "
                                 f"(part {part['index']} chunk {len(specs)}: {at:.3f} vs previous end "
                                 f"{specs[-2]['at'] + specs[-2]['duration']:.3f}, "
                                 f"span {span:.3f}, speech {part['speech_seconds']:.3f}, "
                                 f"gaps {sum(gaps):.3f} of {len(gaps)})")
            at += chunk["duration"] + gap
        part["start"] = round(cursor, 3)
        part["end"] = round(cursor + span, 3)
        part["span"] = round(span, 3)
        part["anchor_drift"] = round(cursor - part["window_start"], 3)
        part["gaps"] = [round(gap, 3) for gap in gaps]
        part["pauses_planned_seconds"] = round(sum(gaps), 3)
        part["pause_seconds_source"] = part["pause_seconds"]
        cursor += span
    # Pieces may butt against each other when a recorded pause is shortened. Resolve that by
    # trimming silence only: first the head of the later piece, then the tail of the earlier
    # one, and refuse the plan if any speech would still be lost.
    for part in parts:  # the take arrays are referenced by every spec, so drop them last
        part.pop("voice", None)
    trims: list[float] = []
    for previous, current in zip(specs, specs[1:]):
        end_previous = previous["begin"] + previous["hi"] - previous["lo"]
        need = end_previous - current["begin"]
        if need <= 0:
            continue
        head_room = current["speech_lo"] - current["lo"]
        take = min(need, max(0, head_room))
        current["lo"] += take
        current["begin"] += take
        need -= take
        if need > 0:
            tail_room = previous["hi"] - previous["speech_hi"]
            take = min(need, max(0, tail_room))
            previous["hi"] -= take
            need -= take
        if need > 0:
            if need > SAMPLE_TOLERANCE:
                raise ValueError(f"parts {previous['part']}→{current['part']}: "
                                 f"{need / SR * 1000:.1f} ms of speech would be lost to a "
                                 "shortened pause")
            current["lo"] += need  # one or two samples at a silence edge, inaudible
            current["begin"] += need
        trims.append((end_previous - (previous["begin"] + previous["hi"] - previous["lo"])) / SR * 1000)
    max_trim = round(max(trims, default=0.0), 3)
    if specs:  # buffer edges: a leading breath may hang before zero, a trailing one past the end
        head = specs[0]
        drop = min(max(0, -head["begin"]), head["speech_lo"] - head["lo"])
        head["lo"] += drop
        head["begin"] += drop
        if head["begin"] < 0:
            raise ValueError("the opening breath is longer than the recording's leading silence")
        tail = specs[-1]
        overhang = tail["begin"] + tail["hi"] - tail["lo"] - buffer_length
        room = tail["hi"] - tail["speech_hi"]
        if overhang > 0:
            if overhang > room:
                raise ValueError("the closing breath would cut the last words; shorten the text")
            tail["hi"] -= int(overhang)
    previous_end = 0
    for spec in specs:
        begin, lo, hi = spec["begin"], spec["lo"], spec["hi"]
        if begin < previous_end:
            raise ValueError(f"collision left after trimming: part {spec['part']} begin={begin} "
                             f"previous_end={previous_end} lo={lo} hi={hi} speech_lo={spec['speech_lo']} "
                             f"speech_hi={spec['speech_hi']} at={spec['at']}")
        if lo > spec["speech_lo"] or hi < spec["speech_hi"] or hi - lo < 1:
            raise ValueError(f"part {spec['part']} chunk is missing its speech tail")
        if begin < 0 or begin + (hi - lo) > buffer_length:
            raise ValueError(f"part {spec['part']} chunk falls outside the picture")
        narration[begin:begin + hi - lo] += spec["voice"][lo:hi]
        previous_end = begin + (hi - lo)
        timeline.append({"part": spec["part"], "start": round(spec["at"], 6),
                         "end": round(spec["at"] + spec["duration"], 6), "text": spec["text"],
                         "take_speech_start": spec["speech_lo"] / SR,
                         "take_piece": [lo / SR, hi / SR],
                         "written_begin": begin / SR,
                         "overlap_seconds": round(overlap_seconds(spec["at"], spec["at"] + spec["duration"],
                                                                  begin / SR, previous_end / SR), 6)})
    narration = narration[:buffer_length]
    if args.narration:
        args.narration.parent.mkdir(parents=True, exist_ok=True)
        sf.write(args.narration, narration, SR, subtype="PCM_24")
        sf.write(args.narration.with_name("source-speech.wav"), source_audio, SR, subtype="PCM_16")

    placed = [[row["start"], row["end"]] for row in timeline]
    report = coverage(source_spans, placed, source_duration, tolerance=args.tolerance,
                      min_gap=args.report_gap)
    report.update({
        "method": "Measured Silero speech chunks re-laid on the original timeline; "
                  "speech speed untouched and every cut inside silence",
        "source": str(args.source), "source_duration": round(source_duration, 3),
        "voice_id": doc.get("voice_id"), "language": doc.get("language"),
        "chunks": len(placed), "cue_count": len([row for row in timeline if row["text"]]),
        "tempo_applied": 1.0, "speech_speed_changed": False,
        "speech_cut": False, "overlapping_speech": 0,
        "pieces_trimmed": len(trims), "max_piece_trim_ms": max_trim,
        "dub_speech_seconds_total": round(sum(stop - start for start, stop in placed), 3),
        "planned_pause_mean_seconds": round(float(np.mean([gap for part in parts for gap in part["gaps"]]))
                                            if any(part["gaps"] for part in parts) else 0.0, 3),
        "max_anchor_drift": round(max((abs(part["anchor_drift"]) for part in parts), default=0.0), 3),
        "parts": [{key: value for key, value in part.items() if key not in {"voice", "chunks"}}
                  for part in parts],
        "timeline": timeline,
        "source_silence_seconds": round(source_duration - sum(stop - start for start, stop in source_spans), 3),
    })
    if report["uncovered_source_speech_seconds"] > args.max_uncovered:
        raise ValueError(f"uncovered original speech {report['uncovered_source_speech_seconds']}s "
                         f"exceeds the budget {args.max_uncovered}s")
    if args.plan_json:
        args.plan_json.parent.mkdir(parents=True, exist_ok=True)
        args.plan_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.srt:
        args.srt.parent.mkdir(parents=True, exist_ok=True)
        args.srt.write_text(srt(timeline), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"parts", "timeline", "gaps"}}, ensure_ascii=False, indent=2))
    print("longest uncovered gaps:", json.dumps(report["gaps"][:8], ensure_ascii=False))
    return report


def _merge(spans: list[list[float]], gap: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] < gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def srt_stamp(seconds: float) -> str:
    total, ms = divmod(round(seconds * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def srt(timeline: list[dict]) -> str:
    blocks = []
    for index, row in enumerate([row for row in timeline if row["text"]], 1):
        text = row["text"]
        if len(text) > 170:
            middle = text.rfind("، ", 0, 130)
            if middle > 60:
                text = text[:middle + 1] + "\n" + text[middle + 2:]
        blocks.append(f"{index}\n{srt_stamp(row['start'])} --> {srt_stamp(row['end'])}\n{text}")
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--narration", type=Path)
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--min-gap", type=float, default=0.12)
    parser.add_argument("--max-gap", type=float, default=0.7)
    parser.add_argument("--lead", type=float, default=0.06)
    parser.add_argument("--tail", type=float, default=0.12)
    parser.add_argument("--hold-back", type=float, default=0.12)
    parser.add_argument("--edge-pad", type=float, default=0.1)
    parser.add_argument("--tolerance", type=float, default=0.15)
    parser.add_argument("--report-gap", type=float, default=0.3)
    parser.add_argument("--max-uncovered", type=float, default=20.0,
                        help="fail the run if original speech stays uncovered beyond this budget")
    args = parser.parse_args()
    for name in ("min_gap", "max_gap", "lead", "tail", "hold_back", "edge_pad", "tolerance",
                 "report_gap", "max_uncovered"):
        value = getattr(args, name)
        if not np.isfinite(value) or value < 0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    if not 0.02 <= args.min_gap <= args.max_gap <= 2.0:
        raise ValueError("pauses must satisfy 0.02 <= --min-gap <= --max-gap <= 2.0")
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
