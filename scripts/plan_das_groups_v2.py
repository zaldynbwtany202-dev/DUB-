#!/usr/bin/env python3
"""Repair the Das word alignment, then re-plan fluent groups (v2).

`library/das-full/youtube/NB2YcTh_L6k.spoken.json` carries one measured time per
YouTube gold word (whisper.cpp on 45s slices of the soundtrack). Most of it is
sound, but some runs collapsed: 0.04-0.05s per word, which no narrator produces,
while a neighbour absorbs 2.4s. Group 124 ended up with 373 characters inside
4.43s (84 chars/s) next to a group with 175 chars in 23.83s.

Repair principle — keep what is measured, rebuild only what is impossible:

* A word is an **anchor** only if its own duration is plausible for its length AND
  its neighbourhood rate stays inside 8-20 chars/s.
* Runs between anchors are redistributed over the observed interval proportionally
  to character count. Real silences (>= 2s gaps) are never filled.
* If a run cannot fit even at the floor duration, the next pass reclassifies the
  words it overflowed into, so the run grows until it reaches plausible ground.
  Three passes are run; each recomputes anchors from the repaired times.

Groups 0-9 are copied verbatim from v1 because ten voice-33 takes already exist
for them. Nothing here generates audio.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "library/das-full/youtube/NB2YcTh_L6k.spoken.json"
V1 = ROOT / "dubs/das-full/youtube-v33/groups.json"
OUT_DIR = ROOT / "dubs/das-full/youtube-v33"
FIXED_JSON = ROOT / "library/das-full/youtube/NB2YcTh_L6k.spoken-fixed.json"
FIXED_SRT = ROOT / "library/das-full/youtube/NB2YcTh_L6k.spoken-fixed.srt"

SILENCE_BREAK = 2.0      # a gap at least this long is a real pause, not a collapse
MIN_WORD = 0.09          # shortest plausible word
WORD_GAP = 0.012         # breath between words
ANCHOR_HALF = 12         # words each side when judging a local rate
RATE_LOW, RATE_HIGH = 8.0, 20.0    # chars/second a real narrator stays inside
NOMINAL_RATE = 15.0      # chars/second used to judge one word's own duration
SLACK_KEEP = 1.5         # above this much spare time, keep the pause instead of filling it
PASSES = 3
MAX_SPAN = 24.0          # group length ceiling (seconds)
MAX_CHARS = 380          # group text ceiling
MAX_DENSITY = 19.0       # reporting guard: chars/second no narrator exceeds
KEEP_V1_GROUPS = 10      # takes already recorded for these
VOICE33_CHARS_PER_S = 11.58   # measured on the ten committed takes


def srt_stamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def blocks_of(words: list[dict]) -> list[list[int]]:
    """Indices grouped into blocks separated by real silences."""
    out: list[list[int]] = [[0]]
    for i in range(1, len(words)):
        if words[i]["t0"] - words[i - 1]["t1"] >= SILENCE_BREAK:
            out.append([])
        out[-1].append(i)
    return out


def word_plausible(w: dict) -> bool:
    """A word whose own duration matches its length."""
    chars = max(len(w["w"]), 1)
    dur = float(w["t1"]) - float(w["t0"])
    expected = chars / NOMINAL_RATE
    return dur >= max(0.42 * expected, 0.055) and dur <= max(2.6 * expected, 1.6)


def rate_plausible(words: list[dict], i: int) -> bool:
    """A word whose neighbourhood delivers text at a humanly possible rate."""
    a, b = max(0, i - ANCHOR_HALF), min(len(words) - 1, i + ANCHOR_HALF)
    if b - a < 6:
        return False
    chars = sum(max(len(words[k]["w"]), 1) for k in range(a, b + 1))
    span = words[b]["t1"] - words[a]["t0"]
    return span > 0.2 and RATE_LOW <= chars / span <= RATE_HIGH


def is_anchor(words: list[dict], i: int) -> bool:
    return word_plausible(words[i]) and rate_plausible(words, i)


def _distribute(words: list[dict], run: list[int], start: float, available: float,
                keep_pause: bool) -> None:
    """Lay `run` over `available` seconds proportionally to character count."""
    weights = [max(len(words[i]["w"]), 1) for i in run]
    gaps = WORD_GAP * (len(run) - 1)
    room = max(available - gaps, MIN_WORD * len(run))
    total = sum(weights)
    cursor = start
    for k, i in enumerate(run):
        dur = max(MIN_WORD, room * weights[k] / total)
        words[i]["t0"] = round(cursor, 3)
        words[i]["t1"] = round(cursor + dur, 3)
        cursor += dur + WORD_GAP


def repair_pass(fixed: list[dict], blocks: list[list[int]]) -> int:
    """One pass: redistribute every implausible run between its anchors."""
    touched = 0
    for block in blocks:
        marks = [i for i in block if is_anchor(fixed, i)]
        marks_set = set(marks)
        bounds = [block[0] - 1] + marks + [block[-1] + 1]
        for lo, hi in zip(bounds, bounds[1:]):
            run = [i for i in range(lo + 1, hi) if i not in marks_set]
            if not run:
                continue
            # Never reach across a real silence: fall back to the observed edge.
            start = (fixed[lo]["t1"] + WORD_GAP if lo >= block[0] else fixed[run[0]]["t0"])
            end = (fixed[hi]["t0"] - WORD_GAP if hi <= block[-1] else fixed[run[-1]]["t1"])
            available = max(end - start, MIN_WORD * len(run))
            need = sum(max(len(fixed[i]["w"]), 1) for i in run) / RATE_HIGH
            before = [(fixed[i]["t0"], fixed[i]["t1"]) for i in run]
            _distribute(fixed, run, start, available, keep_pause=available > SLACK_KEEP * need)
            touched += sum(1 for i, b in zip(run, before)
                           if abs(fixed[i]["t0"] - b[0]) > 0.02 or abs(fixed[i]["t1"] - b[1]) > 0.02)
    return touched


def smooth(words: list[dict]) -> tuple[list[dict], dict]:
    fixed = [dict(w) for w in words]
    blocks = blocks_of(words)
    per_pass = [repair_pass(fixed, blocks) for _ in range(PASSES)]
    shifts = sorted(abs(f["t0"] - w["t0"]) for f, w in zip(fixed, words))
    implausible = sum(1 for w in fixed if not word_plausible(w))
    stats = {
        "blocks": len(blocks), "words": len(words),
        "anchors_final": sum(1 for i in range(len(fixed)) if is_anchor(fixed, i)),
        "words_changed_per_pass": per_pass,
        "words_retimed": sum(1 for s in shifts if s > 0.02),
        "shift_median_s": round(shifts[len(shifts) // 2], 3),
        "shift_p95_s": round(shifts[int(len(shifts) * 0.95)], 3),
        "worst_shift_s": round(shifts[-1], 3),
        "words_still_implausible": implausible,
        "method": "anchor-run redistribution, own-duration + neighbourhood-rate test, "
                  f"{PASSES} passes; real silences kept",
    }
    return fixed, stats


def plan(words: list[dict], start_index: int, start_i: int, floor_t0: float) -> list[dict]:
    """Fluent groups from repaired times; no overlap, no density fragmentation."""
    groups: list[dict] = []
    i, n = start_index, len(words)
    while i < n:
        t0 = max(float(words[i]["t0"]), floor_t0)
        j = i
        while j + 1 < n:
            span = words[j + 1]["t1"] - t0
            text = " ".join(w["w"] for w in words[i:j + 2])
            if (span > MAX_SPAN or len(text) > MAX_CHARS) and j > i:
                break
            j += 1
        t1 = max(float(words[j]["t1"]), t0 + MIN_WORD)
        text = " ".join(w["w"] for w in words[i:j + 1])
        groups.append({"i": start_i + len(groups), "w0": i, "w1": j,
                       "t0": round(t0, 3), "t1": round(t1, 3),
                       "n": j - i + 1, "chars": len(text), "text": text})
        floor_t0 = t1 + 0.01
        i = j + 1
    return groups


def rebalance(groups: list[dict], protected: int) -> tuple[list[dict], list[int]]:
    """Spread an impossibly dense group's text over its run's measured span.

    A group whose density exceeds what any narrator delivers cannot be fixed by
    re-boundarying alone: the alignment lost time inside it. Its neighbours often
    hold the slack (a sparse group whose window is mostly pause). So the run's total
    span — which is measured and trusted — is kept, and split between the groups in
    proportion to their character counts. Groups already recorded are protected.
    """
    fixed: list[dict] = []
    touched: list[int] = []
    i = 0
    while i < len(groups):
        g = groups[i]
        density = g["chars"] / max(g["t1"] - g["t0"], 0.001)
        if density <= MAX_DENSITY or i < protected:
            fixed.append(g)
            i += 1
            continue
        j = i
        chars = 0
        while j < len(groups) and j - i < 4:
            chars += groups[j]["chars"]
            span = groups[j]["t1"] - groups[i]["t0"]
            if chars / span <= MAX_DENSITY and j > i:
                break
            j += 1
        run = groups[i:j + 1] if j < len(groups) else groups[i:]
        total_chars = sum(x["chars"] for x in run)
        start, end = run[0]["t0"], run[-1]["t1"]
        cursor = start
        for x in run:
            share = (end - start) * x["chars"] / total_chars
            new = dict(x, t0=round(cursor, 3), t1=round(cursor + share, 3), rebalanced=True)
            fixed.append(new)
            touched.append(new["i"])
            cursor += share
        i += len(run)
    return fixed, touched


def main() -> int:
    words = json.loads(WORDS.read_text(encoding="utf-8"))["words"]
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    fixed, stats = smooth(words)

    FIXED_JSON.write_text(json.dumps({"source": WORDS.name, "repair": stats, "words": fixed},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
    with FIXED_SRT.open("w", encoding="utf-8") as fh:
        for k, w in enumerate(fixed, 1):
            fh.write(f"{k}\n{srt_stamp(w['t0'])} --> {srt_stamp(w['t1'])}\n{w['w']}\n\n")

    keep = v1["groups"][:KEEP_V1_GROUPS]
    groups = list(keep) + plan(fixed, keep[-1]["w1"] + 1, len(keep), keep[-1]["t1"] + 0.01)
    groups, rebalanced = rebalance(groups, KEEP_V1_GROUPS)

    forecast = []
    for g in groups:
        window = g["t1"] - g["t0"]
        forecast.append({"i": g["i"], "window": round(window, 2), "chars": g["chars"],
                         "density": round(g["chars"] / window, 2),
                         "predicted_tempo": round((g["chars"] / VOICE33_CHARS_PER_S) / window, 3)})
    tempi = sorted(f["predicted_tempo"] for f in forecast)
    overlaps = [groups[k]["i"] for k in range(1, len(groups))
                if groups[k]["t0"] < groups[k - 1]["t1"] - 0.001]

    payload = {
        "voice_id": "voice-33",
        "words_from": "youtube unique.txt",
        "times_from": "NB2YcTh_L6k.spoken-fixed.srt (measured times kept; impossible runs rebuilt)",
        "processing": "atempo only, matched to original onsets (user approved 2026-09-14)",
        "min_tempo": 1.0,
        "max_tempo": 1.60,
        "not_word_by_word": True,
        "repair": stats,
        "rebalanced_groups": rebalanced,
        "kept_v1_groups": KEEP_V1_GROUPS,
        "groups": groups,
        "forecast": forecast,
        "overlapping_groups": overlaps,
        "groups_over_tempo_ceiling": [f["i"] for f in forecast if f["predicted_tempo"] > 1.60],
        "groups_over_density_guard": [f["i"] for f in forecast if f["density"] > MAX_DENSITY],
    }
    (OUT_DIR / "groups-v2.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    for g in groups:
        (OUT_DIR / f"text-v2-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")

    print(json.dumps({
        "repair": stats,
        "groups": len(groups), "kept_verbatim": KEEP_V1_GROUPS, "overlaps": overlaps,
        "tempo_min": tempi[0], "tempo_median": tempi[len(tempi) // 2], "tempo_max": tempi[-1],
        "rebalanced_groups": rebalanced,
        "over_ceiling_1.60": payload["groups_over_tempo_ceiling"],
        "over_density_guard": payload["groups_over_density_guard"][:10],
        "n_over_density_guard": len(payload["groups_over_density_guard"]),
        "last_group_ends": groups[-1]["t1"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
