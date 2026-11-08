#!/usr/bin/env python3
"""ASR only: transcribe a real media file locally or on a GitHub runner.

No translation, TTS, demo transcript, or dubbing pipeline is invoked. Model
weights belong in the cache; the small JSON/SRT/TXT results can be kept beside
the source video after reviewing them.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def srt_stamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("SRT timestamps must be finite and non-negative")
    total_ms = round(seconds * 1000)
    total_s, ms = divmod(total_ms, 1000)
    total_m, sec = divmod(total_s, 60)
    hours, minute = divmod(total_m, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def serialize_segment(segment: Any) -> dict[str, Any]:
    start, end = float(segment.start), float(segment.end)
    if not all(math.isfinite(t) for t in (start, end)) or start < 0 or end <= start:
        raise ValueError(f"Invalid ASR interval: {start}..{end}")
    words = []
    warnings = []
    for word in (segment.words or []):
        raw_start, raw_end = getattr(word, "start", None), getattr(word, "end", None)
        try:
            t0, t1 = float(raw_start), float(raw_end)
            if (not all(math.isfinite(t) for t in (t0, t1)) or t0 < 0
                    or t1 <= t0 or t0 < start - 0.25 or t1 > end + 0.25):
                raise ValueError("word timing outside spoken interval")
            word_start, word_end = round(t0, 3), round(t1, 3)
        except (TypeError, ValueError):
            # Never distribute untimed words uniformly across a cue. Keep
            # their text and flag the timestamp for independent review.
            word_start = word_end = None
            warnings.append("missing_or_invalid_word_timing")
        try:
            probability = float(getattr(word, "probability", None))
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("invalid word confidence")
        except (TypeError, ValueError):
            probability = None
            warnings.append("missing_or_invalid_word_confidence")
        if probability is not None and probability < 0.45:
            warnings.append("low_confidence_word")
        words.append({"word": word.word, "start": word_start,
                      "end": word_end, "probability": probability})
    if end - start > 8:
        warnings.append("long_segment_check_timing")
    try:
        no_speech = float(segment.no_speech_prob)
        if not math.isfinite(no_speech) or not 0 <= no_speech <= 1:
            raise ValueError("bad no-speech probability")
    except (TypeError, ValueError):
        no_speech = None
        warnings.append("missing_or_invalid_no_speech_probability")
    try:
        logprob = float(segment.avg_logprob)
        if not math.isfinite(logprob):
            raise ValueError("bad segment confidence")
    except (TypeError, ValueError):
        logprob = None
        warnings.append("missing_or_invalid_segment_confidence")
    if no_speech is not None and no_speech >= 0.6:
        warnings.append("possible_hallucination_no_speech")
    if logprob is not None and logprob <= -1.0:
        warnings.append("low_segment_confidence")
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "text": segment.text.strip(),
        "avg_logprob": logprob,
        "no_speech_prob": no_speech,
        "words": words,
        "review_warnings": sorted(set(warnings)),
    }


def write_results(result: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    segments = result.get("segments", [])
    if not segments or not any(s["text"].strip() for s in segments):
        raise ValueError("No speech recognized; refusing to save an empty/demo transcript")
    srt = "\n\n".join(
        f"{i}\n{srt_stamp(s['start'])} --> {srt_stamp(s['end'])}\n{s['text']}"
        for i, s in enumerate(segments, 1)
    ) + "\n"
    contents = {
        "source.segments.json": json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "source.srt": srt,
        "source.txt": "\n".join(s["text"] for s in segments) + "\n",
    }
    # A repeated ASR pass must not replace a listener-corrected transcript.
    # Check *all* outputs first so one conflict cannot leave a partial update.
    for name, text in contents.items():
        existing = output_dir / name
        if existing.exists() and existing.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"Existing transcript differs: {existing}; use a new output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, text in contents.items():
        path = output_dir / name
        if path.exists():
            paths[name] = path
            continue
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temp.write_text(text, encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        paths[name] = path
    return paths


def transcribe_file(
    source: Path,
    *,
    model_name: str = "medium",
    language: str | None = None,
    download_root: Path = Path(".cache/models/whisper"),
    cpu_threads: int = 2,
    use_vad: bool = True,
    dialect_hint: str | None = None,
    model_factory=None,
) -> dict[str, Any]:
    source = Path(source)
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"Source media missing or empty: {source}")
    source_hash = sha256_file(source)
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    started = time.monotonic()
    print(f"Loading {model_name} on CPU (int8); source={source}; sha256={source_hash}", flush=True)
    model = model_factory(
        model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads,
        download_root=str(download_root),
    )
    stream, info = model.transcribe(
        str(source), task="transcribe", language=language, beam_size=5,
        vad_filter=use_vad, word_timestamps=True, condition_on_previous_text=False,
    )
    print(f"Detected language: {info.language} ({info.language_probability:.3f}); duration={info.duration:.3f}s", flush=True)
    segments = []
    for segment in stream:
        if not segment.text.strip():
            continue
        row = serialize_segment(segment)
        segments.append(row)
        print(f"{row['start']:8.3f} -> {row['end']:8.3f}  {row['text']}", flush=True)
    if not segments:
        raise RuntimeError("ASR recognized no speech. No substitute transcript was created.")
    try:
        version = importlib.metadata.version("faster-whisper")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "schema_version": 1,
        "status": "transcribed",
        "review_required": True,
        "source": {"path": str(source), "bytes": source.stat().st_size, "sha256": source_hash},
        "asr": {
            "engine": "faster-whisper", "version": version, "model": model_name,
            "device": "cpu", "compute_type": "int8", "beam_size": 5,
            "word_timestamps": True, "vad_filter": use_vad,
            "requested_language": language, "detected_language": info.language,
            "dialect_hint_for_human_review": dialect_hint,
            "dialect_detected": None,
            "language_probability": float(info.language_probability),
            "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_sha": os.environ.get("GITHUB_SHA"),
        },
        "duration": float(info.duration),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "segments": segments,
        "review_flags": sorted({flag for segment in segments for flag in segment["review_warnings"]}),
    }


def cpp_words(tokens: list[dict], *, multilingual: bool = True) -> list[dict]:
    """Rejoin byte-split BPE pieces before decoding Arabic word text.

    whisper.cpp v1.7.6 emits individual UTF-8 byte pieces inside JSON token
    strings. Read native JSON with surrogateescape, never errors='replace',
    or letters are irreversibly damaged before the pieces can be rejoined.
    """
    groups: list[list[dict]] = []
    eot = 50257 if multilingual else 50256
    for token in tokens:
        if token["id"] >= eot:
            continue
        if not groups or token["text"].startswith((" ", "\n", "\t")):
            groups.append([])
        groups[-1].append(token)
    words = []
    for group in groups:
        data = b"".join(t["text"].encode("utf-8", errors="surrogateescape") for t in group)
        text = data.decode("utf-8").strip()
        if not text:
            continue
        timed = []
        confidences = []
        dtw_points = []
        for token in group:
            offsets = token.get("offsets") or {}
            try:
                a, b = float(offsets["from"]), float(offsets["to"])
                if all(math.isfinite(x) for x in (a, b)) and 0 <= a < b:
                    timed.append((a, b))
            except (KeyError, TypeError, ValueError):
                pass
            try:
                confidences.append(float(token.get("p")))
            except (TypeError, ValueError):
                confidences.append(float("nan"))
            try:
                point = float(token.get("t_dtw", -1))
                if math.isfinite(point) and point >= 0:
                    dtw_points.append(point / 100)
            except (TypeError, ValueError):
                pass
        confidence = min(confidences) if all(math.isfinite(p) and 0 <= p <= 1 for p in confidences) else None
        words.append({
            "word": text,
            "start": min(a for a, _ in timed) / 1000 if timed else None,
            "end": max(b for _, b in timed) / 1000 if timed else None,
            "probability": confidence,
            "dtw_points": dtw_points,
        })
    return words


def cpp_result(native: dict, source: Path, *, duration: float, elapsed: float,
               language_probability: float | None = None) -> dict:
    """Normalize real whisper.cpp output; keep ASR uncertainty explicit."""
    if native.get("params", {}).get("translate"):
        raise ValueError("Expected source transcription, not English translation")
    segments = []
    multilingual = native.get("model", {}).get("multilingual", True)
    for row in native.get("transcription", []):
        if not row["text"].strip():
            continue
        start, end = float(row["offsets"]["from"]) / 1000, float(row["offsets"]["to"]) / 1000
        if not all(math.isfinite(t) for t in (start, end, duration)) or start < 0 or end <= start:
            raise ValueError(f"ASR interval outside source: {start}..{end}")
        # whisper.cpp can slightly overrun the last sample; clamp tiny tail drift.
        if end > duration:
            if start >= duration or end - duration > 0.5:
                raise ValueError(f"ASR interval outside source: {start}..{end}")
            end = duration
        if segments and start < segments[-1]["end"]:
            raise ValueError("Overlapping/out-of-order native ASR segments")
        words = cpp_words(row.get("tokens", []), multilingual=multilingual)
        warnings = []
        if end - start > 8:
            warnings.append("long_segment_check_timing")
        if any(w["probability"] is None for w in words):
            warnings.append("missing_or_invalid_word_confidence")
        if any(w["probability"] is not None and w["probability"] < 0.45 for w in words):
            warnings.append("low_confidence_words")
        if any(w["start"] is None or w["end"] is None or w["end"] <= w["start"]
               or w["start"] < start - 0.25 or w["end"] > end + 0.25 for w in words):
            warnings.append("missing_or_invalid_word_timing")
        segments.append({"start": start, "end": end, "text": row["text"].strip(),
                         "words": words, "review_warnings": warnings})
    if not segments:
        raise RuntimeError("ASR recognized no speech. No substitute transcript was created.")
    return {
        "schema_version": 1, "status": "transcribed", "review_required": True,
        "source": {"path": str(source), "bytes": source.stat().st_size, "sha256": sha256_file(source)},
        "asr": {
            "engine": "whisper.cpp", "model": native["model"]["type"],
            "multilingual": multilingual, "device": "cpu",
            "requested_language": native["params"]["language"],
            "detected_language": native["result"]["language"],
            "language_probability": language_probability,
            "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
            "word_timing_method": "grouped native token boundaries; DTW points retained when present",
        },
        "duration": duration, "elapsed_seconds": round(elapsed, 3), "segments": segments,
        "review_flags": sorted({flag for segment in segments for flag in segment["review_warnings"]}),
    }


def transcribe_cpp(source: Path, *, model: Path, cli: Path, language: str | None,
                   cpu_threads: int, work_dir: Path, dtw_model: str = "small") -> dict:
    from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
    for path in (source, model, cli):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Missing source/model/CLI: {path}; run scripts/bootstrap_local_whisper.py first")
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="whisper-", dir=work_dir) as work:
        wav = Path(work) / "source.wav"
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(source),
                        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        with wave.open(str(wav), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        prefix = Path(work) / "recognized"
        command = [str(cli.resolve()), "--model", str(model), "--file", str(wav),
                   "--language", language or "auto", "--threads", str(cpu_threads), "--no-gpu",
                   "--output-json-full", "--output-file", str(prefix), "--split-on-word", "--max-len", "140"]
        if dtw_model:
            command.extend(["--dtw", dtw_model])
        print(f"Transcribing actual source with local whisper.cpp: {source}", flush=True)
        process = subprocess.run(command, check=True, capture_output=True, text=True, errors="replace")
        native = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8", errors="surrogateescape"))
        probability = re.search(r"auto-detected language: \w+ \(p = ([0-9.]+)\)", process.stderr)
        result = cpp_result(native, source, duration=duration, elapsed=time.monotonic() - started,
                            language_probability=float(probability[1]) if probability else None)
        result["asr"]["model_sha256"] = sha256_file(model)
        result["asr"]["dtw_model"] = dtw_model or None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/asr"))
    parser.add_argument("--backend", choices=["faster-whisper", "whisper-cpp"], default="faster-whisper")
    parser.add_argument("--model", help="Whisper name/local CT2 directory, or a GGML file for whisper-cpp")
    parser.add_argument("--whisper-cli", type=Path, default=ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli")
    parser.add_argument("--dtw-model", default="small", help="whisper-cpp DTW preset; must match the GGML model; empty disables DTW")
    parser.add_argument("--language", default="", help="Leave empty to detect the source language (not the dub language)")
    parser.add_argument("--dialect", default="", help="Requested dialect for human review only (e.g. ar-EG); ASR does not verify dialect")
    parser.add_argument("--download-root", type=Path, default=Path(".cache/models/whisper"))
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--no-vad", action="store_true")
    args = parser.parse_args(argv)
    if args.cpu_threads < 1:
        parser.error("--cpu-threads must be positive")
    try:
        if args.backend == "whisper-cpp":
            result = transcribe_cpp(
                args.source, model=Path(args.model) if args.model else ROOT / ".cache/models/whisper-ggml/ggml-small.bin",
                cli=args.whisper_cli, language=args.language.strip() or None,
                cpu_threads=args.cpu_threads, work_dir=args.output_dir, dtw_model=args.dtw_model,
            )
        else:
            result = transcribe_file(
                args.source, model_name=args.model or "medium", language=args.language.strip() or None,
                download_root=args.download_root, cpu_threads=args.cpu_threads, use_vad=not args.no_vad,
                dialect_hint=args.dialect.strip() or None,
            )
        if args.dialect.strip() and args.backend == "whisper-cpp":
            result["asr"]["dialect_hint_for_human_review"] = args.dialect.strip()
            result["asr"]["dialect_detected"] = None
        paths = write_results(result, args.output_dir)
    except Exception as exc:
        print(f"ASR FAILED ({type(exc).__name__}): {exc}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps({"outputs": {name: str(path) for name, path in paths.items()}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
