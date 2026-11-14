"""Metrics for ASR drafts against human-verified Arabic transcripts.

WER/CER are text metrics only; neither measures vowel realization, dialect
quality, speaker identity or listener-perceived naturalness.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from youtube_auto_dub.content_validation import normalize_tokens

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    prior = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, start=1):
        current = [i]
        for j, right in enumerate(hypothesis, start=1):
            current.append(min(
                current[-1] + 1,
                prior[j] + 1,
                prior[j - 1] + (left != right),
            ))
        prior = current
    return prior[-1]


def _validated_record(record: Any, index: int) -> tuple[str, str, str, str]:
    if not isinstance(record, dict):
        raise ValueError(f"record {index}: expected a JSON object")
    ident = str(record.get("recording_id", "")).strip()
    dialect = str(record.get("dialect", "")).strip()
    reference = record.get("reference")
    hypothesis = record.get("hypothesis")
    if not ident or not dialect or not isinstance(reference, str) or not reference.strip():
        raise ValueError(f"record {index}: recording_id, dialect and nonempty reference are required")
    if not isinstance(hypothesis, str):
        raise ValueError(f"record {index}: hypothesis must be a string (use empty string for no ASR output)")
    if record.get("reference_verified") is not True or not str(record.get("reference_reviewer", "")).strip():
        raise ValueError(f"record {index}: reference must be human-verified and identify its reviewer")
    audio_hash = str(record.get("audio_sha256", ""))
    if not _SHA256.fullmatch(audio_hash):
        raise ValueError(f"record {index}: a 64-character lowercase audio_sha256 is required")
    return dialect, reference, hypothesis, ident


def _aggregate(records: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    word_errors = reference_words = char_errors = reference_chars = 0
    for _dialect, reference, hypothesis, _ident in records:
        ref_words = normalize_tokens(reference)
        hyp_words = normalize_tokens(hypothesis)
        ref_chars = list("".join(ref_words))
        hyp_chars = list("".join(hyp_words))
        word_errors += _edit_distance(ref_words, hyp_words)
        reference_words += len(ref_words)
        char_errors += _edit_distance(ref_chars, hyp_chars)
        reference_chars += len(ref_chars)
    return {
        "recordings": len(records),
        "reference_words": reference_words,
        "word_errors": word_errors,
        "wer": round(word_errors / reference_words, 6) if reference_words else None,
        "reference_characters_no_spaces_or_marks": reference_chars,
        "character_errors": char_errors,
        "cer": round(char_errors / reference_chars, 6) if reference_chars else None,
    }


def evaluate_asr_records(records: Iterable[dict]) -> dict[str, Any]:
    validated = [_validated_record(record, i) for i, record in enumerate(records, start=1)]
    if not validated:
        raise ValueError("at least one human-verified recording is required")
    by_dialect: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for record in validated:
        by_dialect[record[0]].append(record)
    return {
        "schema_version": 1,
        "metric": "human-reference WER/CER after Arabic lexical normalization",
        "normalization": "NFKC, Arabic marks/tatweel removed, alef variants unified, punctuation and spaces ignored",
        "dialect_detected_by_asr": False,
        "human_references_required": True,
        "pronunciation_verified": False,
        "quality_gate": False,
        "warning": "WER/CER do not measure homograph pronunciation, dialect authenticity, prosody or naturalness; listen to the audio.",
        "overall": _aggregate(validated),
        "by_dialect": {dialect: _aggregate(rows) for dialect, rows in sorted(by_dialect.items())},
    }
