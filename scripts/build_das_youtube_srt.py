#!/usr/bin/env python3
"""Build YouTube-auto SRT for Das recap NB2YcTh_L6k.

Words come from the unique YouTube watch-page transcript (not Whisper).
Times come from YouTube caption-window anchors (~30s) with per-word
interpolation inside each window. json3 word offsets are blocked (HTTP 500).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YT = ROOT / "library/das-full/youtube"
UNIQUE = YT / "unique.txt"
ANCHORS = YT / "anchors.tsv"
PHRASE_SRT = YT / "NB2YcTh_L6k.srt"
WORD_SRT = YT / "NB2YcTh_L6k.words.srt"
WORDS_JSON = YT / "NB2YcTh_L6k.words.json"
VIDEO_END = 3142.38  # source.mp4 duration
WORDS_PER_CUE = 8
SKIP = {"[موسيقى]", "موسيقى"}


def ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_words() -> list[str]:
    raw = UNIQUE.read_text(encoding="utf-8")
    raw = raw.replace("[موسيقى]", " ")
    words = [w for w in raw.split() if w and w not in SKIP]
    return words


def load_anchors() -> list[tuple[float, list[str]]]:
    out = []
    for line in ANCHORS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sec_s, phrase = line.split("\t", 1)
        toks = [w for w in phrase.split() if w not in SKIP]
        if toks:
            out.append((float(sec_s), toks))
    out.sort(key=lambda x: x[0])
    return out


def find_subseq(words: list[str], needle: list[str], start: int) -> int:
    n = len(needle)
    if n == 0:
        return start
    limit = len(words) - n
    # Prefer exact window near proportional position, then scan forward.
    for i in range(start, limit + 1):
        if words[i : i + n] == needle:
            return i
    # Relaxed: first 4 tokens
    n4 = min(4, n)
    for i in range(start, len(words) - n4 + 1):
        if words[i : i + n4] == needle[:n4]:
            return i
    return -1


def word_times(words: list[str], anchors: list[tuple[float, list[str]]]) -> list[float]:
    idx = [None] * len(anchors)
    pos = 0
    for ai, (_t, needle) in enumerate(anchors):
        hit = find_subseq(words, needle, pos)
        if hit < 0:
            continue
        idx[ai] = hit
        pos = hit + 1

    # Fill missing anchors by time-proportional guess between neighbors.
    known = [(i, idx[i], anchors[i][0]) for i in range(len(anchors)) if idx[i] is not None]
    if not known:
        raise SystemExit("no anchors matched unique transcript")

    times = [0.0] * len(words)
    # Map word index -> time via known (word_index, time) knots
    knots: list[tuple[int, float]] = [(known[0][1], known[0][2])]
    for _, wi, t in known[1:]:
        if wi > knots[-1][0]:
            knots.append((wi, t))
        elif wi == knots[-1][0]:
            knots[-1] = (wi, t)
    if knots[0][0] != 0:
        # start of speech
        knots.insert(0, (0, min(4.0, knots[0][1])))
    last_i, last_t = knots[-1]
    if last_i < len(words) - 1:
        knots.append((len(words) - 1, VIDEO_END - 1.2))

    for k in range(len(knots) - 1):
        i0, t0 = knots[k]
        i1, t1 = knots[k + 1]
        span = max(i1 - i0, 1)
        dt = max(t1 - t0, 0.01)
        for i in range(i0, i1 + 1):
            times[i] = t0 + (i - i0) / span * dt
    return times


def write_word_srt(words: list[str], times: list[float], path: Path) -> None:
    lines = []
    n = len(words)
    cue = 1
    for i, w in enumerate(words):
        t0 = times[i]
        t1 = times[i + 1] if i + 1 < n else min(t0 + 0.28, VIDEO_END)
        if t1 <= t0:
            t1 = t0 + 0.08
        # leave a tiny gap so cues don't stack
        if i + 1 < n and times[i + 1] - t0 > 0.12:
            t1 = min(t1, times[i + 1] - 0.02)
        lines.append(f"{cue}\n{ts(t0)} --> {ts(t1)}\n{w}\n")
        cue += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_phrase_srt(words: list[str], times: list[float], path: Path) -> None:
    lines = []
    n = len(words)
    cue = 1
    i = 0
    while i < n:
        j = min(i + WORDS_PER_CUE, n)
        t0 = times[i]
        t1 = times[j] if j < n else min(times[j - 1] + 0.35, VIDEO_END)
        if t1 <= t0:
            t1 = t0 + 0.4
        text = " ".join(words[i:j])
        lines.append(f"{cue}\n{ts(t0)} --> {ts(t1)}\n{text}\n")
        cue += 1
        i = j
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    words = load_words()
    anchors = load_anchors()
    times = word_times(words, anchors)
    write_word_srt(words, times, WORD_SRT)
    write_phrase_srt(words, times, PHRASE_SRT)
    payload = {
        "source": "https://www.youtube.com/watch?v=NB2YcTh_L6k",
        "lang": "ar-auto",
        "video_end": VIDEO_END,
        "word_count": len(words),
        "timing": "youtube-caption-window-anchors + per-word interpolation",
        "words": [
            {"i": i, "t": round(times[i], 3), "w": w} for i, w in enumerate(words)
        ],
    }
    WORDS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"words={len(words)} phrase_cues={len(words)//WORDS_PER_CUE+1}")
    print(f"wrote {PHRASE_SRT}")
    print(f"wrote {WORD_SRT}")
    print(f"wrote {WORDS_JSON}")
    print("first", words[:12])
    print("last", words[-8:])
    print("t0", times[0], "t_last", times[-1])


if __name__ == "__main__":
    main()
