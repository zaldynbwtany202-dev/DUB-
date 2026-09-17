#!/usr/bin/env python3
"""Plan recording groups that only ever break inside a real silence.

The defect this fixes, in the user's words: the narrator starts a word, says its
first half, then jumps to the next sentence. Cause: the aligned word times were
gap-free (every gap exactly 0.01 s), so `intake_new_video.plan_groups` had no idea
where the narrator breathed and cut groups wherever the 24 s / 380-char budget ran
out — mid-phrase, e.g. «...بقى لها» | «وقت طويل...». Each take is synthesized on
its own, so it ends with phrase-final prosody on a word that grammatically
continues, and the next take starts 10 ms later. Nothing was truncated in the
audio; the boundaries were simply in the wrong places.

Fix: ffmpeg `silencedetect` gives the real pauses (this video has 280 of them
>= 0.12 s, one every 4.1 s). A group may now end ONLY after a word that is
followed by such a pause, and among the pauses that fit the budget the longest one
wins, so cuts land on the narrator's own breaths.

The placement window keeps running to the next group's onset (minus a small breath
margin), because the builder stretches a take to fill its window: including the
pause is what keeps atempo near 1.3x instead of the ~1.65x that speech-only spans
would demand of a voice that reads 8.69 chars/s against an original 14.3.

Usage
  python scripts/plan_pause_groups.py --words <spoken-fixed.json> \
      --pauses <pauses.json> --slug doctor-lecture --out <groups.json>
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.source_sync import tts_safe_egyptian  # noqa: E402

MIN_GAP = 0.15       # a silence at least this long is a legitimate cut point
BREATH_FRAC = 0.40   # leave this fraction of the boundary pause as real silence
BREATH_MAX = 0.25    # ...but never more than this, it would cost too much tempo
MIN_WORDS = 3        # never emit a group shorter than this
MIN_TAKE = 3.0       # a group shorter than this is merged into its neighbour
MIN_CHARS = 30       # ...or too small to be worth a clip on its own
EDGE_SLACK = 0.08    # a take must land this far inside its window (builder's rule)
TOL = 0.35           # alignment slack when matching a pause to a word boundary


def char_count(words: list[dict], i: int, j: int) -> int:
    return len("".join(w["w"] for w in words[i:j + 1]))


def break_candidates(words: list[dict], pauses: list[dict], min_gap: float) -> list[dict]:
    """Map each silence onto the word it follows: break AFTER that word."""
    starts = [w["t0"] for w in words]
    by_word: dict[int, dict] = {}
    for pz in pauses:
        if pz["dur"] < min_gap:
            continue
        k = bisect.bisect_left(starts, pz["start"]) - 1
        if k < 0 or k + 1 >= len(words):
            continue
        # whisper -sow tiles words back to back, so a silence is absorbed into the
        # span of the word BEFORE it: words[k].t1 reaches words[k+1].t0. The honest
        # test is therefore about onsets, not ends -- the pause must open after word
        # k starts speaking and close before word k+1 starts speaking.
        drift = max(words[k]["t0"] - pz["start"], pz["end"] - words[k + 1]["t0"], 0.0)
        prev = by_word.get(k)
        if prev is None or pz["dur"] > prev["dur"]:
            by_word[k] = {"k": k, "dur": pz["dur"], "start": pz["start"],
                          "end": pz["end"], "drift": round(drift, 3),
                          "clean": drift <= TOL}
    return [by_word[k] for k in sorted(by_word)]


def plan(words: list[dict], cands: list[dict], *, max_span: float, max_span_hard: float,
         max_chars: int, cps: float, ceiling: float) -> tuple[list[dict], dict]:
    n = len(words)
    ci = 0
    floor = 0.0
    groups: list[dict] = []
    forced: list[int] = []

    def make(i: int, k: int, br: dict | None, floor: float = 0.0) -> dict:
        # A word's aligned onset can sit EARLIER than the end of the silence that
        # precedes it (whisper -sow stretches the previous word across the pause and
        # the aligner interpolates). Without this floor, group k+1 would start while
        # group k is still sounding, and the builder mixes additively -> a double
        # voice blip at every such boundary. 14 groups in this video had it.
        t0 = max(words[i]["t0"], floor)
        speech_end = words[k]["t1"]
        if br:
            # the next word starts at br["end"]; keep part of the pause as a real
            # breath so the take does not run into it
            breath = min(BREATH_FRAC * br["dur"], BREATH_MAX)
            t1 = br["end"] - breath
            pause, kind, drift = br["dur"], "silence" if br["clean"] else "silence-drift", br["drift"]
        else:
            breath = 0.0
            t1 = speech_end + EDGE_SLACK
            pause, kind, drift = 0.0, "forced", 0.0
        window = t1 - t0
        chars = char_count(words, i, k)
        room = max(window - EDGE_SLACK, 0.5)
        return {"i": len(groups), "w0": i, "w1": k, "t0": round(t0, 3), "t1": round(t1, 3),
                "window": round(window, 3), "speech_end": round(speech_end, 3),
                "cues": k - i + 1, "chars": chars,
                "predicted_tempo": round((chars / cps) / room, 3),
                "break_pause_s": round(pause, 3), "break_kind": kind,
                "break_drift_s": drift, "breath_kept_s": round(breath, 3),
                "text": tts_safe_egyptian(" ".join(w["w"] for w in words[i:k + 1]).strip())}

    while ci < n:
        feasible: list[dict] = []
        for c in cands:
            if c["k"] < ci + MIN_WORDS - 1:
                continue
            g = make(ci, c["k"], c, floor)
            if g["window"] > max_span_hard or g["chars"] > max_chars:
                break          # both only grow with k, so nothing later can fit
            if g["predicted_tempo"] > ceiling:
                continue       # not monotone: a longer pause ahead can lower it
            feasible.append((g, c))
        if feasible:
            pool = [x for x in feasible if x[0]["window"] >= 0.7 * max_span] or feasible
            # longest breath wins; then the fullest window (fewer, longer takes)
            pick, c = max(pool, key=lambda x: (x[1]["dur"], x[0]["window"]))
            floor = c["end"]
        else:
            # no silence fits the budget: cut at the widest silence inside it, and
            # if there is none, cut on the budget and say so — never silently.
            k = ci
            while k + 1 < n:
                trial = make(ci, k + 1, None, floor)
                if (trial["window"] > max_span_hard or trial["chars"] > max_chars
                        or trial["predicted_tempo"] > ceiling):
                    break
                k += 1
            inner = [c for c in cands if ci + MIN_WORDS - 1 <= c["k"] <= k]
            if inner:
                c = max(inner, key=lambda x: x["dur"])
                pick = make(ci, c["k"], c, floor)
                pick["break_kind"] = "silence-short-group"
                floor = c["end"]
            else:
                # clamp: the tail of the video can hold fewer than MIN_WORDS words
                pick = make(ci, min(max(k, ci + MIN_WORDS - 1), n - 1), None, floor)
                forced.append(pick["i"])
                floor = pick["t1"]
        groups.append(pick)
        ci += pick["cues"]
        if ci >= n:
            break

    # a tail too short to be worth its own clip joins the group before it, rather
    # than being recorded alone at a tempo no narrator should be asked for
    if len(groups) >= 2 and (groups[-1]["window"] < MIN_TAKE or groups[-1]["chars"] < MIN_CHARS):
        tail = groups.pop()
        prev = groups[-1]
        cand = make(prev["w0"], n - 1, None, prev["t0"])
        cand["break_kind"] = "merged-tail"
        cand["break_pause_s"] = prev["break_pause_s"]
        cand["breath_kept_s"] = prev["breath_kept_s"]
        if (cand["window"] <= max_span_hard and cand["chars"] <= max_chars
                and cand["predicted_tempo"] <= ceiling):
            groups[-1] = cand
        else:
            groups.append(tail)
    for k, g in enumerate(groups):
        g["i"] = k

    stats = {
        "groups": len(groups),
        # derived from the FINAL groups: a forced cut that the tail merge absorbed
        # is no longer a forced cut, and reporting the pre-merge index said
        # "infeasible" about a plan that was actually fine
        "forced_cuts_no_silence": [g["i"] for g in groups if g["break_kind"] == "forced"],
        "breaks_on_silence": sum(1 for g in groups if g["break_kind"].startswith("silence")),
        "break_drift_over_tol": [g["i"] for g in groups if g["break_kind"] == "silence-drift"],
        "min_break_pause_s": min((g["break_pause_s"] for g in groups[:-1]), default=0.0),
        "median_break_pause_s": sorted(g["break_pause_s"] for g in groups)[len(groups) // 2],
        "breath_kept_total_s": round(sum(g["breath_kept_s"] for g in groups), 2),
    }
    return groups, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", type=Path, required=True, help="aligned spoken words (times repaired)")
    ap.add_argument("--pauses", type=Path, required=True, help="silencedetect pause map json")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voice-id", default="voice-01")
    ap.add_argument("--chars-per-s", type=float, default=8.69, help="measured rate of the voice")
    ap.add_argument("--max-span", type=float, default=24.0,
                    help="preferred group length; a group may stretch past it to reach a silence")
    ap.add_argument("--max-span-hard", type=float, default=30.0,
                    help="absolute length cap. The opening 26.7 s of this video hold no "
                         "silence at all, so a 24 s cap would force a mid-phrase cut there; "
                         "stretching to the first breath keeps every word whole")
    ap.add_argument("--max-chars", type=int, default=380)
    ap.add_argument("--ceiling", type=float, default=1.60)
    ap.add_argument("--min-gap", type=float, default=MIN_GAP)
    ap.add_argument("--duration", type=float, default=None)
    a = ap.parse_args()

    words = json.loads(a.words.read_text(encoding="utf-8"))["words"]
    pz = json.loads(a.pauses.read_text(encoding="utf-8"))
    pauses = pz["pauses"]
    dur = a.duration or (words[-1]["t1"] if words else 0.0)

    cands = break_candidates(words, pauses, a.min_gap)
    groups, stats = plan(words, cands, max_span=a.max_span, max_span_hard=a.max_span_hard,
                         max_chars=a.max_chars, cps=a.chars_per_s, ceiling=a.ceiling)

    tempi = sorted(g["predicted_tempo"] for g in groups)
    over = [g["i"] for g in groups if g["predicted_tempo"] > a.ceiling]
    covered = sum(g["window"] for g in groups)
    payload = {
        "slug": a.slug, "source": str(a.source),
        "words_from": "YouTube watch page transcript (kOrz0WAb2P8), times repaired",
        "times_from": "whisper.cpp small per-word times, gaps preserved",
        "breaks_from": f"ffmpeg silencedetect {pz['noise_db']}dB d>={pz['min_dur']}s, "
                       f"min_gap {a.min_gap}s -> boundaries only inside real silences",
        "voice_id": a.voice_id,
        "pacing": "matched (per-take atempo onto original onsets), boundary pauses kept as breaths",
        "chars_per_s": a.chars_per_s, "tempo_ceiling": a.ceiling, "min_tempo": 1.0,
        "max_span": a.max_span, "max_span_hard": a.max_span_hard, "min_gap": a.min_gap,
        "cues": len(words), "pause_map_count": len(pauses), "break_candidates": len(cands),
        "groups": groups,
        "stats": stats,
        "tempo_min": tempi[0], "tempo_median": tempi[len(tempi) // 2], "tempo_max": tempi[-1],
        "groups_over_ceiling": over,
        "coverage_ratio": round(covered / dur, 4) if dur else None,
        "feasible": not over and not stats["forced_cuts_no_silence"],
        "clips_needed": len(groups),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for g in groups:
        (a.out.parent / f"text-pause-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")

    summary = {k: payload[k] for k in ("groups", "feasible", "clips_needed", "coverage_ratio",
                                       "tempo_min", "tempo_median", "tempo_max",
                                       "groups_over_ceiling", "break_candidates")}
    summary["cues"] = len(words)
    summary["stats"] = stats
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== أول 4 مجموعات ===")
    for g in groups[:4]:
        print(f"#{g['i']} {g['t0']:.2f}->{g['t1']:.2f} نافذة={g['window']}s كلمة={g['cues']} "
              f"حرف={g['chars']} سرعة={g['predicted_tempo']} سكتة={g['break_pause_s']}s "
              f"نَفَس={g['breath_kept_s']}s [{g['break_kind']}]")
        print(f"   …{g['text'][-60:]} ⟂ {groups[g['i']+1]['text'][:40] if g['i']+1 < len(groups) else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
