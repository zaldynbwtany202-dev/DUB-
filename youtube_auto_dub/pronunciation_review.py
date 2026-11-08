"""Non-destructive Arabic pronunciation and dialect review.

A lexicon offers possible readings; it never edits the approved script or
claims that ASR has proved how a word was pronounced.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from youtube_auto_dub.content_validation import normalize_tokens, validate_spoken_content

ROOT = Path(__file__).resolve().parents[1]
HAZARDS = ROOT / "config/homonym-hazards.json"
EGYPTIAN = ROOT / "config/egy-pronunciation.json"


def review_pronunciation(
    expected: str, observed_asr: str | None = None, *, dialect: str = "ar-EG",
    hazards_path: Path = HAZARDS, egyptian_path: Path = EGYPTIAN,
) -> dict[str, Any]:
    """Produce a listening checklist, NOT an automatic pronunciation verdict."""
    words = normalize_tokens(expected)
    if not words or not any(re.search(r"[\u0621-\u064a]", word) for word in words):
        raise ValueError("Expected nonempty Arabic script for pronunciation review")
    rules = json.loads(Path(hazards_path).read_text(encoding="utf-8"))["hazards"]
    hazards = []
    for i, word in enumerate(words):
        # Egyptian script attaches و/ف/ب/ل to the next word. Compare the
        # complete token, then at most two known clitics; never search inside
        # arbitrary text or replace a suffix by substring accident.
        candidates = [word]
        current = word
        for _ in range(2):
            if len(current) <= 2 or current[0] not in "وفبل":
                break
            current = current[1:]
            candidates.append(current)
        for rule in rules:
            matched = next((candidate for candidate in candidates
                            if re.fullmatch(rule["match"], candidate)), None)
            if matched:
                hazards.append({"word_index": i, "word": word, "matched_word": matched,
                                "why": rule["why"],
                                "alternatives_to_review": rule["alternatives"]})
    suggestions = []
    regional = []
    if dialect.lower().startswith("ar-eg"):
        egyptian = json.loads(Path(egyptian_path).read_text(encoding="utf-8"))
        normalized = {normalize_tokens(key)[0]: value for key, value in egyptian["lexicon"].items()
                      if normalize_tokens(key)}
        qaf = {normalize_tokens(key)[0]: value for key, value in egyptian.get("qaf_as_hamza", {}).items()
               if normalize_tokens(key)}
        for i, word in enumerate(words):
            if word in normalized:
                suggestions.append({"word_index": i, "word": word,
                                    "possible_reading": normalized[word],
                                    "status": "suggestion_not_approved"})
            if word in qaf:
                regional.append({"word_index": i, "word": word,
                                 "possible_reading": qaf[word],
                                 "status": "regional_option_not_automatic"})
    comparison = (validate_spoken_content(expected, observed_asr)
                  if observed_asr is not None else None)
    return {
        "dialect_requested": dialect,
        "expected_text": expected,
        "observed_asr_text": observed_asr,
        "homonym_hazards": hazards,
        "dialect_suggestions": suggestions,
        "regional_variants": regional,
        "lexical_asr_check": comparison,
        "asr_disagreement": bool(comparison and comparison["missing_words"]),
        "status": "listener_review_required",
        "script_modified": False,
        "warning": "ASR can miss a wrong vowel or invent a word. Compare the actual audio with the script; do not auto-accept or auto-rewrite.",
    }
