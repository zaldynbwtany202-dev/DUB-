#!/usr/bin/env python3
"""Re-time approved agent takes onto the original narration, sentence by sentence.

This never synthesizes speech, never time-stretches it, and never re-encodes the picture.

Two placement modes share the same safety rules:

* ``--anchor`` (default) pairs each dub clause with the speech unit the *original*
  narrator used for the same content and moves the dub's silence so that the clause starts
  as near as possible to that unit, while every part still fills its window; the dub is
  then in phase with the source at clause level and not only at part level. The clause
  order is shared with the source, the wording is not (an Egyptian rewrite matches a
  transcription only ~35 %), so units are paired by rank over the source's own pauses;
* ``--no-anchor`` reproduces the older behaviour (fill each reviewed window with the
  take's own rhythm). Both are measured, and the report states the sentence-level error
  of each, so "the sync improved" is a number and not a claim.

Safety properties, asserted rather than assumed:

* every cut lands on a local minimum inside measured silence, so no syllable is clipped;
* speech of two chunks never overlaps;
* only silence moves: ``tempo 1.000x`` and no piece is dropped or truncated;
* a pause is never left idle beyond ``--anchor-hole`` while the source keeps talking, so
  the picture is never silent underneath a dub that is waiting for its cue;
* the residual mismatch is measured and reported (``sentence_timing_error``), because a
  dub whose text is shorter than the original cannot match it clause for clause without
  rewriting, and inventing silence or stretching speech is not allowed.

Outputs:
  narration wav   one continuous 44.1 kHz dry narration master for the mixer
  plan json       per-chunk placement, anchors, coverage and timing error
  srt             subtitles rebuilt from the same placement
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
                                         overlap_seconds, plan_gaps, sentence_indices)
from youtube_auto_dub.sentence_anchor import (anchor_errors, source_units,  # noqa: E402
                                               split_sentences, spread_gaps)

SR = 44100
VAD_SR = 16000
SAMPLE_TOLERANCE = 3  # samples: rounding of a placement to the sample grid


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


def _drop_short(spans: list[list[float]], floor: float) -> list[list[float]]:
    """Fold utterances too short to be a clause into the piece before them.

    A detector sometimes reports a 0.1 s island (a plosive, a lip noise) between two real
    phrases. Left alone it becomes a chunk of its own, the placement gives it a pause on
    each side, and the listener hears a hole in which nobody speaks. Folding it into the
    previous piece keeps the same samples and removes the false boundary.
    """
    out: list[list[float]] = []
    for span in spans:
        if out and span[1] - span[0] < floor:
            out[-1][1] = max(out[-1][1], span[1])
            continue
        out.append([span[0], span[1]])
    return out


def _merge(spans: list[list[float]], gap: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] < gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


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


def measure(audio: np.ndarray, sample_rate: int, *, min_gap: float, max_pad: float,
            min_speech: float = 0.0) -> tuple[list[dict[str, float]], list[float]]:
    """Split a take into spoken chunks, each owning the silence that surrounds it."""
    spans = silero_spans(audio, sample_rate, min_silence_ms=max(60, int(min_gap * 1000) - 20))
    if not spans:
        spans = energy_spans(audio, sample_rate)
    if not spans:
        raise ValueError("no speech measured in take")
    merged = _drop_short(_merge(spans, min_gap), min_speech)
    duration = len(audio) / sample_rate
    cuts = [0.0]
    for previous, following in zip(merged, merged[1:]):
        gap_start, gap_end = previous[1], following[0]
        middle = (gap_start + gap_end) / 2
        cuts.append(_quietest(audio, sample_rate, middle, round(max_pad, 6), gap_start, gap_end))
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


def legacy_starts(parts: list[dict], windows: list[float], total: float, *, lead: float,
                  tail: float, min_gap: float, max_gap: float) -> list[float]:
    """The older placement: fill each window with the take's own rhythm. Kept for the
    before/after measurement and for ``--no-anchor`` runs."""
    spans = allocate_part_spans(windows, [p["min_span"] for p in parts],
                                [p["max_span"] for p in parts], total)
    starts: list[float] = []
    cursor = parts[0]["window_start"] if parts else 0.0
    for part, span in zip(parts, spans):
        gaps = plan_gaps(part["recorded_pauses"],
                         span - part["speech_seconds"] - lead - tail,
                         min_gap=min_gap, max_gap=max_gap)
        at = cursor + lead
        for index, duration in enumerate(part["durations"]):
            starts.append(round(at, 6))
            at += duration + (gaps[index] if index < len(gaps) else 0.0)
        part["legacy_span"] = round(span, 3)
        cursor += span
    return starts


def build(args: argparse.Namespace) -> dict:
    doc = json.loads(args.script.read_text(encoding="utf-8"))
    if doc.get("voice_backend") != "agent":
        raise ValueError("only agent-recorded takes are accepted")
    source_audio = decode(args.source, SR)
    source_duration = len(source_audio) / SR
    source_spans = _merge(silero_spans(source_audio, SR, min_silence_ms=100)
                          or energy_spans(source_audio, SR), args.min_gap)
    words_path = args.script.parent / doc.get("source_analysis", "source-analysis/words.json")
    words = json.loads(words_path.read_text(encoding="utf-8"))["words"] if words_path.is_file() else []

    parts = []
    for take in sorted(doc["takes"], key=lambda item: item["index"]):
        audio = (args.script.parent / take["audio"]).resolve()
        if not audio.is_file():
            raise FileNotFoundError(f"approved recording is missing: {audio}")
        digest = sha256(audio)
        integrity = "byte-identical to the approved recording"
        if digest not in {take.get("sha256"), take.get("tool_wav_sha256")}:
            archive = take.get("lossless_archive") or {}
            restored = compare_with_archive(audio, archive)
            if restored is None:
                raise ValueError(f"take {take['index']} audio changed since it was approved")
            voice, integrity = restored, ("sample-identical to the approved FLAC archive "
                                          f"{archive.get('sha256', '')[:12]}…; container differs")
        else:
            voice = decode(audio, SR)
        chunks, pauses = measure(voice, SR, min_gap=args.min_gap, max_pad=args.edge_pad,
                                 min_speech=args.min_speech)
        durations = [chunk["duration"] for chunk in chunks]
        sentences = split_sentences(str(take["text"]))
        parts.append({"index": int(take["index"]), "window_start": float(take["start"]),
                      "window_end": float(take["end"]), "text": take["text"],
                      "audio": str(audio), "audio_sha256": digest, "integrity": integrity,
                      "take_duration": round(len(voice) / SR, 3), "voice": voice,
                      "chunks": chunks, "durations": durations, "recorded_pauses": pauses,
                      "clauses": sentences,
                      "chunk_texts": align_sentences(sentences, durations),
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

    # --- anchors: pair each dub clause with the unit the original narrator spent on it ---
    spans = allocate_part_spans(windows, [part["min_span"] for part in parts],
                                [part["max_span"] for part in parts], round(sum(windows), 6))
    durations_all: list[float] = []
    targets_all: list[float | None] = []
    starts: list[float] = []
    errors: list[float | None] = []
    owner: list[tuple[int, int]] = []
    cursor = parts[0]["window_start"] if parts else 0.0
    for position, (part, span) in enumerate(zip(parts, spans)):
        part["span_allowed"] = round(span, 3)
        units = (source_units(words, len(part["clauses"]), part["window_start"],
                              part["window_end"], min_gap=args.min_gap)
                 if words and part["clauses"] else [])
        first_of_clause: dict[int, int] = {}
        for rank, chunk in enumerate(sentence_indices(part["clauses"], part["durations"])):
            first_of_clause.setdefault(chunk, rank)
        targets: list[float | None] = []
        for index, duration in enumerate(part["durations"]):
            rank = first_of_clause.get(index)
            target = None
            if units and rank is not None and rank < len(units):
                target = round(float(units[rank]["start"]), 3)
            elif index == 0:
                target = round(part["window_start"], 3)
            if target is not None:  # a clause cannot start before the picture or past its window
                target = min(max(target, part["window_start"]),
                             max(part["window_start"], part["window_end"] - duration))
            targets.append(target)
        desired: list[float] = []
        for index in range(1, len(part["durations"])):
            previous, current = targets[index - 1], targets[index]
            if previous is not None and current is not None:
                desired.append(current - previous - part["durations"][index - 1])
            else:
                desired.append(part["recorded_pauses"][index - 1] if index - 1 < len(part["recorded_pauses"])
                               else args.min_gap)
        silence = span - sum(part["durations"]) - args.lead - args.tail
        # a hole the listener hears also contains the two breaths the pieces keep around
        # their speech, so the budget on the *planned* gap is smaller by those pads
        cap = max(args.anchor_min_gap, args.anchor_hole - 2 * args.edge_pad)
        average = silence / max(1, len(part["durations"]) - 1)
        part["source_clauses"] = len(part["clauses"])
        part["source_units"] = len(units)
        part["silence_owned"] = round(silence, 3)
        anchored = [(target, index) for index, target in enumerate(targets) if target is not None]
        # what the original would demand: silence between the first and last anchored clause
        part["anchor_silence_demand"] = round(
            (anchored[-1][0] - anchored[0][0] - sum(part["durations"][anchored[0][1]:anchored[-1][1]]))
            if len(anchored) > 1 else 0.0, 3)
        # anchors that cannot be honoured at all: the previous clause of the dub is longer
        # than the stretch the original spent on its own content, so starting on time would
        # mean talking over it. Only more words (or a shorter clause) can fix that.
        conflicts = sum(1 for (previous, before), (current, after) in zip(anchored, anchored[1:])
                        if current - previous < part["durations"][before:after + 1][0])
        part["anchor_conflicts"] = conflicts
        part["gap_average"] = round(average, 3)
        part["gap_ceiling"] = round(max(cap, average), 3)
        part["text_starved"] = bool(average > cap + 1e-6)
        part["shortest_chunk_seconds"] = min(part["durations"], default=0.0)
        if args.anchor and desired:
            gaps = spread_gaps(desired, max(0.0, silence), min_gap=args.anchor_min_gap,
                               max_gap=cap)
        elif desired:
            gaps = plan_gaps(part["recorded_pauses"], silence, min_gap=args.min_gap,
                             max_gap=args.max_gap)
        else:
            gaps = []
        part["gaps_planned"] = [round(gap, 3) for gap in gaps]
        at = cursor + args.lead
        for index, duration in enumerate(part["durations"]):
            at += gaps[index - 1] if 0 < index <= len(gaps) else 0.0
            starts.append(round(at, 6))
            errors.append(round(at - targets[index], 3) if targets[index] is not None else None)
            durations_all.append(duration)
            targets_all.append(targets[index])
            owner.append((position, index))
            at += duration
        cursor += span
    plan_errors = anchor_errors(starts, errors)
    legacy = legacy_starts([{**part} for part in parts], windows, round(sum(windows), 6),
                           lead=args.lead, tail=args.tail, min_gap=args.min_gap,
                           max_gap=args.max_gap)
    legacy_report = anchor_errors(legacy, [round(placed - target, 3) if target is not None else None
                                           for placed, target in zip(legacy, targets_all)])
    use_anchor = bool(args.anchor and any(target is not None for target in targets_all))

    buffer_length = round((source_duration + args.hold_back) * SR)
    narration = np.zeros(buffer_length, dtype=np.float32)
    specs: list[dict] = []
    for (part_index, chunk_index), begin_time, duration, target, error in zip(
            owner, starts, durations_all, targets_all, errors):
        part = parts[part_index]
        chunk = part["chunks"][chunk_index]
        speech_lo = int(round(chunk["speech_start"] * SR))
        speech_hi = int(round(chunk["speech_end"] * SR))
        specs.append({"part": part["index"], "voice": part["voice"],
                      "begin": int(round((begin_time + chunk["piece_start"]
                                          - chunk["speech_start"]) * SR)),
                      # integer bounds may sit one sample inside the speech; widen to cover it
                      "lo": min(int(chunk["piece_start"] * SR), speech_lo),
                      "hi": max(int(chunk["piece_end"] * SR), speech_hi),
                      "speech_lo": speech_lo, "speech_hi": speech_hi,
                      "at": begin_time, "duration": duration, "target": target, "error": error,
                      "text": part["chunk_texts"][chunk_index]})
        if len(specs) > 1 and begin_time < (starts[len(specs) - 2] + durations_all[len(specs) - 2]
                                            - 1e-6):
            raise ValueError(f"spoken chunks overlap at part {part['index']} chunk {chunk_index}")
    # Pieces may butt against each other when a recorded pause is shortened. Resolve that by
    # trimming silence only: first the head of the later piece, then the tail of the earlier
    # one, and refuse the plan if any speech would still be lost.
    trims: list[float] = []
    for previous, current in zip(specs, specs[1:]):
        need = (previous["begin"] + previous["hi"] - previous["lo"]) - current["begin"]
        if need <= 0:
            continue
        take = min(need, max(0, current["speech_lo"] - current["lo"]))
        current["lo"] += take
        current["begin"] += take
        need -= take
        if need > 0:
            tail_room = previous["hi"] - previous["speech_hi"]
            extra = min(need, max(0, tail_room))
            previous["hi"] -= extra
            need -= extra
        if need > 0:
            if need > SAMPLE_TOLERANCE:
                raise ValueError(f"parts {previous['part']}→{current['part']}: "
                                 f"{need / SR * 1000:.1f} ms of speech would be lost to a "
                                 "shortened pause")
            current["lo"] += need
            current["begin"] += need
        trims.append(need / SR * 1000)
    if specs:  # buffer edges: a leading breath may hang before zero, a trailing one past the end
        head = specs[0]
        drop = min(max(0, -head["begin"]), head["speech_lo"] - head["lo"])
        head["lo"] += drop
        head["begin"] += drop
        if head["begin"] < 0:
            raise ValueError("the opening breath is longer than the recording's leading silence")
        tail = specs[-1]
        overhang = tail["begin"] + tail["hi"] - tail["lo"] - buffer_length
        if overhang > 0:
            room = tail["hi"] - tail["speech_hi"]
            if overhang > room:
                raise ValueError("the closing breath would cut the last words; shorten the text")
            tail["hi"] -= int(overhang)
    previous_end = 0
    for spec in specs:
        begin, lo, hi = spec["begin"], spec["lo"], spec["hi"]
        if begin < previous_end:
            raise ValueError("silence trimming did not remove a piece collision")
        if lo > spec["speech_lo"] or hi < spec["speech_hi"] or hi - lo < 1:
            raise ValueError(f"part {spec['part']} chunk is missing its speech tail")
        if begin < 0 or begin + (hi - lo) > buffer_length:
            raise ValueError(f"part {spec['part']} chunk falls outside the picture")
        narration[begin:begin + hi - lo] += spec["voice"][lo:hi]
        previous_end = begin + (hi - lo)
    placed = [[spec["at"], spec["at"] + spec["duration"]] for spec in specs]
    timeline = [{"part": spec["part"], "start": round(spec["at"], 6),
                 "end": round(spec["at"] + spec["duration"], 6), "text": spec["text"],
                 "take_speech_start": spec["speech_lo"] / SR,
                 "take_piece": [spec["lo"] / SR, spec["hi"] / SR],
                 "anchor_target": spec["target"], "anchor_error": spec["error"],
                 "written_begin": spec["begin"] / SR,
                 "overlap_seconds": round(overlap_seconds(spec["at"], spec["at"] + spec["duration"],
                                                          spec["begin"] / SR,
                                                          spec["begin"] / SR + (spec["hi"] - spec["lo"]) / SR), 6)}
                for spec in specs]
    all_gaps: list[float] = []
    for part in parts:
        rows = [row for row in timeline if row["part"] == part["index"]]
        part["start"] = round(rows[0]["start"] - args.lead, 3) if rows else part["window_start"]
        part["end"] = round(rows[-1]["end"], 3) if rows else part["window_end"]
        part["span"] = round(part["end"] - part["start"], 3)
        part["anchor_drift"] = round(part["start"] - part["window_start"], 3)
        part["gaps"] = [round(current["start"] - previous["end"], 3)
                        for previous, current in zip(rows, rows[1:])]
        all_gaps.extend(gap for gap in part["gaps"] if gap >= 0)
        part["pauses_planned_seconds"] = round(sum(gap for gap in part["gaps"] if gap >= 0), 3)
        part["pause_seconds_source"] = part["pause_seconds"]
        part["dub_clauses"] = part["source_clauses"]
    narration = narration[:buffer_length]
    if args.narration:
        args.narration.parent.mkdir(parents=True, exist_ok=True)
        sf.write(args.narration, narration, SR, subtype="PCM_24")
        sf.write(args.narration.with_name("source-speech.wav"), source_audio, SR, subtype="PCM_16")

    report = coverage(source_spans, placed, source_duration, tolerance=args.tolerance,
                      min_gap=args.report_gap)
    report.update({
        "method": ("clause-level anchoring on the original speech units, silence projected onto "
                   "the anchors and the window; chunks cut inside measured silence; speech speed "
                   "untouched" if use_anchor else
                   "window filling with the take's own rhythm (anchor mode disabled)"),
        "anchored": use_anchor, "anchor_min_gap": args.anchor_min_gap,
        "anchor_hole": args.anchor_hole, "anchor_hole_edge_pads": round(2 * args.edge_pad, 3),
        "min_speech_seconds": args.min_speech,
        "text_starved_parts": [part["index"] for part in parts if part.get("text_starved")],
        "anchor_silence_demand_seconds": round(sum(part.get("anchor_silence_demand", 0.0)
                                                   for part in parts), 3),
        "silence_owned_seconds": round(sum(part["silence_owned"] for part in parts), 3),
        "anchor_conflicts": sum(part.get("anchor_conflicts", 0) for part in parts),
        "anchor_conflicts_note": "anchors whose moment has already passed when the previous dub "
                                 "clause finishes; unreachable without changing the wording",
        "shortest_chunk_seconds": round(min((part["shortest_chunk_seconds"] for part in parts),
                                            default=0.0), 3),
        "source": str(args.source), "source_duration": round(source_duration, 3),
        "voice_id": doc.get("voice_id"), "language": doc.get("language"),
        "source_word_count": len(words), "chunks": len(placed),
        "cue_count": len([row for row in timeline if row["text"]]),
        "tempo_applied": 1.0, "speech_speed_changed": False,
        "speech_cut": False, "overlapping_speech": 0,
        "pieces_trimmed": len(trims), "max_piece_trim_ms": round(max(trims, default=0.0), 3),
        "dub_speech_seconds_total": round(sum(stop - start for start, stop in placed), 3),
        "planned_pause_mean_seconds": round(float(np.mean(all_gaps)), 3) if all_gaps else 0.0,
        "planned_pause_max_seconds": round(float(np.max(all_gaps)), 3) if all_gaps else 0.0,
        "sentence_timing_error": plan_errors,
        "sentence_timing_error_unanchored": legacy_report,
        "max_anchor_drift": round(max((abs(part["anchor_drift"]) for part in parts), default=0.0), 3),
        "source_silence_seconds": round(source_duration - sum(stop - start for start, stop in source_spans), 3),
        "parts": [{key: value for key, value in part.items() if key not in {"voice", "chunks"}}
                  for part in parts],
        "timeline": timeline,
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
    print("sentence timing error (anchored)  :", json.dumps(plan_errors, ensure_ascii=False))
    print("sentence timing error (window fill):", json.dumps(legacy_report, ensure_ascii=False))
    print("longest uncovered gaps:", json.dumps(report["gaps"][:8], ensure_ascii=False))
    return report


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
    parser.add_argument("--anchor", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--anchor-min-gap", type=float, default=0.08,
                        help="shortest breath the dub may keep between two clauses")
    parser.add_argument("--anchor-hole", type=float, default=0.62,
                        help="longest audible hole the dub may leave while the original speaks, "
                             "measured edge to edge, including the breath kept around each piece")
    parser.add_argument("--min-speech", type=float, default=0.3,
                        help="spoken islands shorter than this are folded into the previous piece")
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
                 "report_gap", "max_uncovered", "anchor_min_gap", "anchor_hole", "min_speech"):
        value = getattr(args, name)
        if not np.isfinite(value) or value < 0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    if not 0.02 <= args.min_gap <= args.max_gap <= 2.0:
        raise ValueError("pauses must satisfy 0.02 <= --min-gap <= --max-gap <= 2.0")
    if not 0.02 <= args.anchor_min_gap <= args.anchor_hole <= 5.0:
        raise ValueError("anchors need 0.02 <= --anchor-min-gap <= --anchor-hole <= 5.0")
    if args.anchor_hole < args.min_gap:
        raise ValueError("--anchor-hole must not be shorter than --min-gap")
    if not 0.05 <= args.min_speech <= 2.0:
        raise ValueError("--min-speech belongs in 0.05..2.0 seconds")
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
