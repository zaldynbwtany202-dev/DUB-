"""Phrase completeness and word-timing quality checks for generated speech."""
from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

_TOKEN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)?", re.UNICODE)
_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_ARABIC_TEXT = re.compile(r"[\u0621-\u064a]")
_ALEF_VARIANTS = str.maketrans("أإآٱ", "اااا")
_NON_SPEECH = {
    "music", "silence", "noise", "applause", "laughter", "intro", "outro",
    "موسيقى", "صمت", "ضوضاء", "تصفيق", "ضحك",
}


def normalize_tokens(text: str) -> list[str]:
    """Compare words, not vowel marks: Arabic harakat must not split tokens.

    This lexical normalization cannot distinguish homographs with different
    spoken vowels. Those remain pronunciation hazards for a listener to hear.
    """
    cleaned = unicodedata.normalize("NFKC", text or "")
    cleaned = _ARABIC_MARKS.sub("", cleaned).translate(_ALEF_VARIANTS)
    return [token.lower().replace("’", "'") for token in _TOKEN.findall(cleaned)]



def is_non_speech_text(text: str) -> bool:
    tokens = normalize_tokens(text)
    return bool(tokens) and len(tokens) <= 2 and all(token in _NON_SPEECH for token in tokens)

def validate_spoken_content(
    expected: str,
    observed: str,
    *,
    min_recall: float = 0.70,
    min_sequence_ratio: float = 0.58,
) -> dict[str, Any]:
    expected_tokens = normalize_tokens(expected)
    observed_tokens = normalize_tokens(observed)
    matcher = SequenceMatcher(a=expected_tokens, b=observed_tokens, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    recall = matched / max(len(expected_tokens), 1)
    sequence_ratio = matcher.ratio()
    length_ratio = len(observed_tokens) / max(len(expected_tokens), 1)
    missing = []
    for tag, a0, a1, _b0, _b1 in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            missing.extend(expected_tokens[a0:a1])
    ok = bool(expected_tokens) and recall >= min_recall and sequence_ratio >= min_sequence_ratio and length_ratio >= 0.65
    return {
        "ok": ok,
        "expected_words": len(expected_tokens),
        "observed_words": len(observed_tokens),
        "matched_words": matched,
        "recall": round(recall, 4),
        "sequence_ratio": round(sequence_ratio, 4),
        "length_ratio": round(length_ratio, 4),
        "missing_words": missing,
        "expected_text": expected,
        "observed_text": observed,
        "evidence": "ASR lexical comparison only; identical text does not prove pronunciation or dialect",
        "listener_review_required": bool(_ARABIC_TEXT.search(expected or "")),
        "pronunciation_verified": False,
    }


def word_timing_report(
    observed_words: list[dict], speech_start: float, speech_end: float, *,
    reference: dict[str, Any] | None = None, tolerance_seconds: float = 0.15,
) -> dict[str, Any]:
    """Report measured ASR word times without inventing linearly-scaled targets.

    ``ok`` means only that ASR timing data is structurally usable. Actual
    word-by-word sync is verified only when a human-reviewed reference with
    the same spoken words and local word boundaries is supplied.
    """
    try:
        speech_start, speech_end = float(speech_start), float(speech_end)
    except (TypeError, ValueError):
        raise ValueError("speech window must be numeric")
    if not all(math.isfinite(t) for t in (speech_start, speech_end)) or speech_end <= speech_start:
        raise ValueError("speech window must be finite and have positive duration")
    if tolerance_seconds < 0 or not math.isfinite(tolerance_seconds):
        raise ValueError("tolerance_seconds must be finite and non-negative")

    rows: list[dict[str, Any]] = []
    flags: set[str] = set()
    prior_start = -1.0
    for index, word in enumerate(observed_words):
        if not isinstance(word, dict):
            flags.add("invalid_word_record")
            rows.append({"index": index, "word": "", "start": None, "end": None,
                         "timing_status": "invalid_record"})
            continue
        text = str(word.get("word", "")).strip()
        try:
            start, end = float(word.get("start")), float(word.get("end"))
            valid = math.isfinite(start) and math.isfinite(end) and 0 <= start < end
        except (TypeError, ValueError):
            start = end = None
            valid = False
        if not valid:
            flags.add("missing_or_invalid_word_timing")
            rows.append({"index": index, "word": text, "start": None, "end": None,
                         "timing_status": "missing_or_invalid"})
            continue
        if start + 1e-6 < prior_start:
            flags.add("out_of_order_word_timing")
        prior_start = start
        if start > speech_end - speech_start + tolerance_seconds or end > speech_end - speech_start + tolerance_seconds:
            flags.add("word_timing_exceeds_target_window")
        rows.append({
            "index": index, "word": text,
            "start": round(start, 3), "end": round(end, 3),
            "actual_start": round(speech_start + start, 3),
            "actual_end": round(speech_start + end, 3),
            "start_offset_from_target": round(start, 3),
            "end_offset_from_target": round(end - (speech_end - speech_start), 3),
            "timing_status": "asr_estimate",
        })

    timed = [row for row in rows if row.get("start") is not None]
    report: dict[str, Any] = {
        "ok": bool(timed) and not flags.intersection({"missing_or_invalid_word_timing", "out_of_order_word_timing"}),
        "ok_scope": "structural_timing_only_not_sync_approval",
        "status": "timings_available_unverified" if timed else "missing_word_timestamps",
        "word_count": len(rows),
        "timed_word_count": len(timed),
        "speech_window": {"start": speech_start, "end": speech_end,
                          "duration": round(speech_end - speech_start, 3)},
        "review_flags": sorted(flags),
        "sync_verified": False,
        "reference_available": False,
        "evidence": "ASR-estimated word times only; no source/reference word alignment was supplied",
        "words": rows,
    }

    if reference is None:
        return report
    report["reference_available"] = True
    if (not isinstance(reference, dict) or reference.get("reference_verified") is not True
            or not str(reference.get("reviewer", "")).strip()
            or not isinstance(reference.get("words"), list)):
        report["status"] = "reference_not_human_verified"
        report["evidence"] = "Reference timings were supplied but lack an explicit human reviewer attestation"
        return report

    expected = reference["words"]
    if len(expected) != len(rows) or len(rows) != len(timed):
        report["status"] = "reference_word_count_mismatch"
        return report
    errors: list[float] = []
    per_word = []
    for row, ref in zip(rows, expected):
        if not isinstance(ref, dict):
            report["status"] = "invalid_reference_word"
            return report
        obs_tokens = normalize_tokens(row["word"])
        ref_tokens = normalize_tokens(str(ref.get("word", "")))
        try:
            ref_start, ref_end = float(ref["start"]), float(ref["end"])
        except (KeyError, TypeError, ValueError):
            report["status"] = "invalid_reference_timing"
            return report
        if (len(obs_tokens) != 1 or obs_tokens != ref_tokens or not math.isfinite(ref_start)
                or not math.isfinite(ref_end) or ref_start < 0 or ref_end <= ref_start):
            report["status"] = "reference_text_or_timing_mismatch"
            return report
        start_error, end_error = abs(row["start"] - ref_start), abs(row["end"] - ref_end)
        errors.extend((start_error, end_error))
        per_word.append({**row,
                         "reference_start": ref_start, "reference_end": ref_end,
                         "start_error_seconds": round(start_error, 4),
                         "end_error_seconds": round(end_error, 4)})
    max_error = max(errors, default=0.0)
    report.update({
        "status": "human_reference_alignment_pass" if max_error <= tolerance_seconds else "human_reference_alignment_fail",
        "reference_reviewer": str(reference["reviewer"]).strip(),
        "reference_verified": True,
        "tolerance_seconds": tolerance_seconds,
        "mean_abs_boundary_error_seconds": round(sum(errors) / len(errors), 4) if errors else 0.0,
        "max_abs_boundary_error_seconds": round(max_error, 4),
        "sync_verified": max_error <= tolerance_seconds,
        "evidence": "Word-by-word boundary error against a human-reviewed reference in the same target-language audio",
        "words": per_word,
    })
    return report
