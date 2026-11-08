#!/usr/bin/env python3
"""Prepare an evidence-bound listening checklist for an Arabic voice take.

Example:
  python3 scripts/review_arabic_take.py --text-file text.txt --audio take.mp3 \\
      --asr-json asr/source.segments.json --dialect ar-EG --out qa/review.json

The tool never synthesizes speech or declares it approved; a person must hear it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youtube_auto_dub.pronunciation_review import review_pronunciation


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_review(text_file: Path, audio: Path, asr_json: Path, dialect: str = "ar-EG") -> dict:
    if not audio.is_file() or audio.stat().st_size == 0:
        raise FileNotFoundError("Missing or empty voice take")
    audio_hash = digest(audio)
    transcript = json.loads(asr_json.read_text(encoding="utf-8"))
    if not isinstance(transcript, dict):
        raise ValueError("ASR transcript must be a JSON object")
    if not isinstance(transcript.get("source"), dict) or transcript["source"].get("sha256") != audio_hash:
        raise ValueError("ASR source hash differs from the take; refusing stale/unrelated transcript")
    if transcript.get("review_required") is not True:
        raise ValueError("ASR evidence must be explicitly marked unreviewed")
    segments = transcript.get("segments")
    if (not isinstance(segments, list) or not segments or not all(isinstance(s, dict) for s in segments)
            or not any(str(s.get("text", "")).strip() for s in segments)):
        raise ValueError("ASR has no speech to compare; listen to the take")
    expected = text_file.read_text(encoding="utf-8").strip()
    observed = " ".join(str(s.get("text", "")).strip() for s in segments).strip()
    review = review_pronunciation(expected, observed, dialect=dialect)
    review.update({"audio": str(audio), "audio_sha256": audio_hash,
                   "script_path": str(text_file),
                   "script_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
                   "asr_path": str(asr_json), "asr_engine": transcript.get("asr", {}).get("engine"),
                   "transcript_review_required": True})
    return review


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--text-file", required=True, type=Path)
    p.add_argument("--audio", required=True, type=Path)
    p.add_argument("--asr-json", required=True, type=Path)
    p.add_argument("--dialect", default="ar-EG")
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args(argv)
    try:
        report = build_review(a.text_file, a.audio, a.asr_json, a.dialect)
        # Never replace a previous listener report or overwrite user corrections.
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with a.out.open("x", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Pronunciation review failed: {exc}", file=sys.stderr)
        return 2
    print(f"Listening checklist: {a.out} (not approved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
