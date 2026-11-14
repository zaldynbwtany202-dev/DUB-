"""Non-destructive Arabic pronunciation and dialect review.

Lexicons offer possible readings; they never edit an approved script or claim
that ASR has proved pronunciation or dialect. Unsupported dialects are called
out instead of being silently treated as Egyptian or Modern Standard Arabic.
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
DIALECT_PROFILES = ROOT / "config/pronunciation"


def _locale_key(locale: str) -> str:
    """Normalize only locale spelling; do not infer a dialect from Arabic."""
    return "-".join(part.lower() for part in (locale or "").strip().replace("_", "-").split("-") if part)


def _normalized_lexicon(entries: Any) -> dict[str, list[str]]:
    if not isinstance(entries, dict):
        return {}
    result: dict[str, list[str]] = {}
    for spelling, readings in entries.items():
        tokens = normalize_tokens(str(spelling))
        if len(tokens) != 1:
            continue
        values = readings if isinstance(readings, list) else [readings]
        for reading in values:
            if isinstance(reading, str) and reading.strip():
                result.setdefault(tokens[0], []).append(reading.strip())
    return result


def _load_dialect_profile(dialect: str, egyptian_path: Path) -> tuple[dict | None, str | None]:
    locale = _locale_key(dialect)
    path = Path(egyptian_path) if locale == "ar-eg" else DIALECT_PROFILES / f"{locale}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(profile, dict) or not isinstance(profile.get("lexicon", {}), dict):
        return None, None
    if locale != "ar-eg":
        provenance = profile.get("provenance")
        if not isinstance(provenance, dict):
            return None, None
        sources = provenance.get("sources")
        if (_locale_key(str(profile.get("locale", ""))) != locale
                or not str(provenance.get("reviewer", "")).strip()
                or not isinstance(sources, list) or not sources
                or not all(isinstance(source, str) and source.strip() for source in sources)):
            return None, None
    return profile, str(path)


def review_pronunciation(
    expected: str, observed_asr: str | None = None, *, dialect: str = "ar-EG",
    hazards_path: Path = HAZARDS, egyptian_path: Path = EGYPTIAN,
) -> dict[str, Any]:
    """Produce a listening checklist, NOT an automatic pronunciation verdict."""
    words = normalize_tokens(expected)
    if not words or not any(re.search(r"[\u0621-\u064a]", word) for word in words):
        raise ValueError("Expected nonempty Arabic script for pronunciation review")
    rules_doc = json.loads(Path(hazards_path).read_text(encoding="utf-8"))
    rules = rules_doc.get("hazards", [])
    hazards = []
    for i, word in enumerate(words):
        # Egyptian script attaches و/ف/ب/ل to the next word. Compare complete
        # tokens, then at most two known clitics; never search within a token.
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

    profile, profile_path = _load_dialect_profile(dialect, egyptian_path)
    lexicon = _normalized_lexicon(profile.get("lexicon", {})) if profile else {}
    variants = _normalized_lexicon(
        profile.get("regional_variants", profile.get("qaf_as_hamza", {}))
    ) if profile else {}
    suggestions = []
    regional = []
    matched_words: set[str] = set()
    for i, word in enumerate(words):
        if word in lexicon:
            matched_words.add(word)
            suggestions.append({
                "word_index": i, "word": word,
                "possible_readings": lexicon[word],
                "status": "suggestion_not_approved",
            })
        if word in variants:
            matched_words.add(word)
            regional.append({
                "word_index": i, "word": word,
                "possible_readings": variants[word],
                "status": "regional_option_not_automatic",
            })

    comparison = (validate_spoken_content(expected, observed_asr)
                  if observed_asr is not None else None)
    available = profile is not None
    return {
        "dialect_requested": dialect,
        "dialect_detected": None,
        "dialect_verified": False,
        "dialect_profile_available": available,
        "dialect_profile_path": profile_path,
        "dialect_profile_status": (
            "suggestions_only" if available and matched_words
            else "profile_available_no_lexicon_match" if available
            else "unsupported_no_vetted_profile"
        ),
        "dialect_lexicon_coverage": {
            "matched_unique_words": len(matched_words),
            "total_unique_words": len(set(words)),
            "ratio": round(len(matched_words) / max(len(set(words)), 1), 4),
        },
        "expected_text": expected,
        "observed_asr_text": observed_asr,
        "homonym_hazards": hazards,
        "dialect_suggestions": suggestions,
        "regional_variants": regional,
        "lexical_asr_check": comparison,
        "asr_disagreement": bool(comparison and comparison["missing_words"]),
        "status": "listener_review_required",
        "script_modified": False,
        "warning": (
            "No vetted pronunciation profile exists for this dialect; do not infer it from Arabic or apply another dialect's rules."
            if not available else
            "Lexicon entries are suggestions only. ASR can miss wrong vowels or invent words; compare the exact audio with the script."
        ),
    }
