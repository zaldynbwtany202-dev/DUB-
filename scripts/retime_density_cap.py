#!/usr/bin/env python3
"""Push aligned word times apart where they are impossibly dense.

The narration sits on a continuous music bed, so whisper misses words unevenly:
in chunk 30 it heard 109 words and the alignment still had to place 217 of the
transcript's words inside that same 45 seconds, because the word list is longer
than the token list and the surplus has to go somewhere. The result is stretches
of ~24 chars/s with single words allotted 0.09 s, which is faster than anyone
speaks. Planned directly, those stretches demand atempo 3.9x.

So no word may be allotted less time than it takes to say it at MAX_RATE. Where
the aligned times are denser than that, the surplus propagates forward into the
following (genuinely sparser) stretch, which is where those words were actually
spoken. Word order and word count are untouched; only t0/t1 move, and never
earlier than the alignment claimed.

If the push leaves the last word past the end of the video, everything is scaled
back by one factor so the narration still finishes with the picture.

Usage
  .venv/bin/python scripts/retime_density_cap.py --words <spoken-fixed.json> \
      --out <spoken-capped.json> [--max-rate 19.0] [--duration 2218.9]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-rate", type=float, default=19.0,
                    help="chars per second; no word is allotted less time than this implies")
    ap.add_argument("--duration", type=float, default=None)
    a = ap.parse_args()

    payload = json.loads(a.words.read_text(encoding="utf-8"))
    words = payload["words"] if isinstance(payload, dict) else payload
    if not words:
        print("لا توجد كلمات", file=sys.stderr)
        return 2
    duration = a.duration or max(w["t1"] for w in words)

    before = [w["t1"] - w["t0"] for w in words]
    needs = [(len(w["w"]) + 1) / a.max_rate for w in words]
    spans = [max(w["t1"] - w["t0"], 0.0) for w in words]

    # Stretching the dense stretches makes the narration longer than the video,
    # and the surplus has to come from somewhere. Shrinking EVERYTHING by one
    # factor would undo the fix exactly where it was needed and distort the
    # stretches that were already right. Instead shrink only the spare time in
    # the loose stretches: find the shrink s where
    #     sum(max(need_i, span_i * s)) == duration
    # so dense words sit at the cap and loose words give up their slack.
    def total(s: float) -> float:
        return sum(max(n, sp * s) for n, sp in zip(needs, spans))

    lo, hi = 0.0, 1.0
    if total(1.0) <= duration:
        s = 1.0
    else:
        for _ in range(60):
            mid = (lo + hi) / 2
            if total(mid) > duration:
                hi = mid
            else:
                lo = mid
        s = lo

    dur_i = [max(n, sp * s) for n, sp in zip(needs, spans)]

    cursor = words[0]["t0"]
    pushed = 0
    for w, d in zip(words, dur_i):
        if cursor > w["t0"] + 1e-6:
            pushed += 1
        w["t0"] = cursor
        w["t1"] = cursor + d
        cursor = w["t1"]

    end = words[-1]["t1"]
    scale = 1.0
    if end > duration + 0.01:
        scale = duration / end
        for w in words:
            w["t0"] *= scale
            w["t1"] *= scale

    after = [w["t1"] - w["t0"] for w in words]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload) if isinstance(payload, dict) else {}
    out["words"] = [{**{k: v for k, v in w.items() if k != "how"},
                     "how": w.get("how", "capped")} for w in words]
    out["retime"] = {
        "max_rate_chars_per_s": a.max_rate,
        "words_pushed_later": pushed,
        "loose_shrink_applied": round(s, 6),
        "scale_applied": round(scale, 6),
        "end_s": round(words[-1]["t1"], 2),
        "duration_s": round(duration, 2),
        "span_median_before_s": round(statistics.median(before), 3),
        "span_median_after_s": round(statistics.median(after), 3),
    }
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    r = out["retime"]
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\nكتبت: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
