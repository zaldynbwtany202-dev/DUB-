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
import sys
import time
from pathlib import Path
from typing import Any


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
    words = [
        {
            "word": word.word,
            "start": round(float(word.start), 3),
            "end": round(float(word.end), 3),
            "probability": float(word.probability),
        }
        for word in (segment.words or [])
    ]
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "text": segment.text.strip(),
        "avg_logprob": float(segment.avg_logprob),
        "no_speech_prob": float(segment.no_speech_prob),
        "words": words,
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
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, text in contents.items():
        path = output_dir / name
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
            "language_probability": float(info.language_probability),
            "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_sha": os.environ.get("GITHUB_SHA"),
        },
        "duration": float(info.duration),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "segments": segments,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/asr"))
    parser.add_argument("--model", default="medium", help="Whisper name or a local CTranslate2 model directory")
    parser.add_argument("--language", default="", help="Leave empty to detect the source language (not the dub language)")
    parser.add_argument("--download-root", type=Path, default=Path(".cache/models/whisper"))
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--no-vad", action="store_true")
    args = parser.parse_args(argv)
    if args.cpu_threads < 1:
        parser.error("--cpu-threads must be positive")
    try:
        result = transcribe_file(
            args.source, model_name=args.model, language=args.language.strip() or None,
            download_root=args.download_root, cpu_threads=args.cpu_threads, use_vad=not args.no_vad,
        )
        paths = write_results(result, args.output_dir)
    except Exception as exc:
        print(f"ASR FAILED ({type(exc).__name__}): {exc}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps({"outputs": {name: str(path) for name, path in paths.items()}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
