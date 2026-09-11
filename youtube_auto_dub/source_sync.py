"""Source-soundtrack synchronous dubbing policy. No picture/lip claims.

Original speech windows come from ASR. Dubbed lines start at those times.
Never slow a take to fill a hole. Never cut spoken words. If a take is too
long, rewrite or re-record; if too short, add meaning rather than stretching.
Merging the session branch is forbidden by policy, not by preference.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/dub-policy.json"

_WORD = re.compile(r"[\u0600-\u06FFa-zA-Z0-9]+")

# Spellings that this session's agent voice mis-stressed. Meaning is kept.
TTS_SAFE = (
    ("بيشرب", "يشرب"),
    ("مية طينية", "مية من الطين"),
    ("مية تنية", "مية من الطين"),
    ("أخيراً", "أخيرا"),
    ("أخيرة.", "أخيرة."),
)


def load_policy(path: Path | None = None) -> dict[str, Any]:
    data = json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))
    if data.get("merge") != "forbidden":
        raise ValueError("dub policy must forbid merging the session branch")
    if float(data["min_tempo"]) < 1.0:
        raise ValueError("min_tempo < 1.0 would slow speech to fill gaps")
    if not 1.0 <= float(data["max_tempo"]) <= 1.12:
        raise ValueError("max_tempo must stay a slight speedup, not a squeeze")
    return data


def forbid_merge(source_branch: str, target_branch: str, policy: dict[str, Any] | None = None) -> None:
    """Refuse to merge the session branch into any other branch."""
    policy = policy or load_policy()
    session = policy["session_branch"]
    if source_branch == session:
        raise PermissionError(
            f"Merging {source_branch!r} into {target_branch!r} is forbidden. "
            f"All work stays on {session}."
        )


def count_words(text: str) -> int:
    return len(_WORD.findall(text or ""))


def tts_safe_egyptian(text: str) -> str:
    out = text
    for src, dst in TTS_SAFE:
        out = out.replace(src, dst)
    return out


def word_budget(window_seconds: float, *, words_per_second: float = 1.7, safety: float = 0.08) -> int:
    if not math.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError("window must be a positive duration")
    budget = window_seconds - min(safety, window_seconds * 0.05)
    return max(1, math.floor(budget * words_per_second))


def evaluate_take(
    natural_seconds: float,
    window_seconds: float,
    *,
    min_tempo: float = 1.0,
    max_tempo: float = 1.08,
    safety: float = 0.08,
) -> dict[str, Any]:
    """Decide whether a recorded take may be placed on a source-speech window."""
    if min_tempo < 1.0:
        raise ValueError("refusing to slow speech; rewrite the line instead")
    if not all(math.isfinite(x) and x > 0 for x in (natural_seconds, window_seconds)):
        raise ValueError("durations must be finite and positive")
    budget = window_seconds - min(safety, window_seconds * 0.05)
    needed = natural_seconds / budget
    if needed > max_tempo:
        return {
            "status": "too_long",
            "tempo": needed,
            "action": "shorten_or_rerecord",
            "slowed": False,
        }
    tempo = 1.0 if needed <= 1.0 else needed
    uncovered = max(0.0, budget - natural_seconds) if tempo == 1.0 else 0.0
    status = "too_short" if uncovered > 0.4 else "fit"
    return {
        "status": status,
        "tempo": tempo,
        "uncovered_seconds": round(uncovered, 3),
        "action": "add_meaning_words" if status == "too_short" else "place_at_source_start",
        "slowed": False,
    }


def plan_from_asr(
    segments: list[dict[str, Any]],
    *,
    words_per_second: float = 1.7,
    min_tempo: float = 1.0,
    max_tempo: float = 1.08,
    audio_dir: str = "agent_voice",
) -> dict[str, Any]:
    """One cue per original soundtrack utterance, with a word budget."""
    cues = []
    previous_end = 0.0
    for i, raw in enumerate(segments):
        start, end = float(raw["start"]), float(raw["end"])
        if not all(math.isfinite(t) for t in (start, end)) or end <= start:
            raise ValueError(f"invalid source speech interval {start}..{end}")
        if start < previous_end - 0.001:
            raise ValueError("source speech windows overlap")
        source = str(raw.get("text") or raw.get("source_text") or "").strip()
        if not source:
            continue
        window = end - start
        cues.append({
            "index": len(cues),
            "start": round(start, 3),
            "end": round(end, 3),
            "source_text": source,
            "text": tts_safe_egyptian(str(raw.get("dub_text") or source)),
            "word_budget": word_budget(window, words_per_second=words_per_second),
            "source_word_count": count_words(source),
            "audio": f"{audio_dir}/seg-{len(cues):04d}.mp3",
            "speaker": raw.get("speaker") or "NARRATOR",
        })
        previous_end = end
    if not cues:
        raise ValueError("no source speech to dub")
    return {
        "voice_backend": "agent",
        "timing_mode": "source_audio",
        "min_tempo": min_tempo,
        "max_tempo": max_tempo,
        "never_slow_to_fill": True,
        "never_truncate_speech": True,
        "merge": "forbidden",
        "segments": cues,
    }


def coverage_from_placements(
    source_windows: list[tuple[float, float]],
    placements: list[dict[str, Any]],
) -> dict[str, Any]:
    """How much original speech time has no dubbed speech, without slowing."""
    uncovered = 0.0
    longest = 0.0
    holes = []
    by_start = {round(float(p["start"]), 3): p for p in placements}
    for start, end in source_windows:
        window = end - start
        placed = by_start.get(round(start, 3))
        spoken = float(placed["fitted_seconds"]) if placed else 0.0
        gap = max(0.0, window - spoken)
        uncovered += gap
        longest = max(longest, gap)
        if gap >= 0.15:
            holes.append({"start": start, "end": end, "uncovered": round(gap, 3)})
    return {
        "source_windows": len(source_windows),
        "uncovered_seconds": round(uncovered, 3),
        "longest_uncovered_seconds": round(longest, 3),
        "holes": holes,
        "slowed": False,
    }


def assert_coverage(report: dict[str, Any], policy: dict[str, Any] | None = None) -> None:
    policy = policy or load_policy()
    limit = float(policy["uncovered_source_speech_fail_seconds"])
    if report["longest_uncovered_seconds"] > limit:
        raise ValueError(
            f"source speech uncovered {report['longest_uncovered_seconds']}s "
            f"(limit {limit}s); add words, do not slow the take"
        )
