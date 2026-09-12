"""Export an SRT that follows spoken islands, not padded cue windows."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_SPLIT = re.compile(r"(?<=[.،!?؟])\s+")


def srt_stamp(seconds: float) -> str:
    total, ms = divmod(round(max(0.0, seconds) * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def split_phrases(text: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT.split(text or "") if p.strip()]
    return parts or [str(text or "").strip()]


def _merge_count(items: list, n: int) -> list:
    """Shrink ``items`` to ``n`` by joining the tail."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    head, tail = items[: n - 1], items[n - 1 :]
    if isinstance(items[0], str):
        return head + [" ".join(tail)]
    return head + [(tail[0][0], tail[-1][1])]


def cues_to_entries(cues: dict[str, Any]) -> list[dict[str, Any]]:
    """One subtitle per spoken phrase, timed to island placements when present."""
    entries: list[dict[str, Any]] = []
    conversions = {int(c["index"]): c for c in cues.get("conversions") or [] if "index" in c}
    for seg in cues.get("segments") or []:
        base = float(seg["start"])
        phrases = split_phrases(str(seg.get("text") or ""))
        conv = conversions.get(int(seg["index"]))
        islands = conv.get("placements") if conv else None
        if islands:
            spans = [(base + float(p["start"]), base + float(p["end"])) for p in islands]
            n = min(len(phrases), len(spans))
            phrases = _merge_count(phrases, n)
            spans = _merge_count(spans, n)
            for text, (start, end) in zip(phrases, spans):
                if end <= start:
                    end = start + 0.2
                entries.append({"start": start, "end": end, "text": text})
        else:
            entries.append({"start": float(seg["start"]), "end": float(seg["end"]), "text": " ".join(phrases)})
    entries.sort(key=lambda e: e["start"])
    for i in range(1, len(entries)):
        if entries[i]["start"] < entries[i - 1]["end"]:
            entries[i]["start"] = entries[i - 1]["end"]
        if entries[i]["end"] <= entries[i]["start"]:
            entries[i]["end"] = entries[i]["start"] + 0.2
    return entries


def render_srt(entries: list[dict[str, Any]]) -> str:
    blocks = []
    for i, e in enumerate(entries, 1):
        blocks.append(f"{i}\n{srt_stamp(e['start'])} --> {srt_stamp(e['end'])}\n{e['text']}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(cues: dict[str, Any], dest: Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_srt(cues_to_entries(cues)), encoding="utf-8")
    return dest
