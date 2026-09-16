#!/usr/bin/env python3
"""Repair aligned word times for any project, then write <prefix>.spoken-fixed.*

`spoken_word_srt.py` times words inside 45 s slices (short slices keep Arabic).
That leaves the known artifact: collapsed runs (a dozen words inside 0.1 s) and
absorbing words (one word holding 2.5 s). das-full fixed it with the anchor-run
redistribution in `plan_das_groups_v2.smooth`; this makes that repair reusable
instead of das-only, and reports what it changed.

Words are never touched here: only t0/t1 move, and real silences are preserved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_das_groups_v2 import smooth, srt_stamp  # noqa: E402  (shared repair)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", type=Path, required=True,
                    help="spoken.json produced by spoken_word_srt.py")
    ap.add_argument("--out-prefix", type=Path, required=True,
                    help="writes <prefix>.json and <prefix>.srt next to it")
    ap.add_argument("--duration", type=float, default=None,
                    help="drop aligned words that start past the real audio end")
    a = ap.parse_args()

    payload = json.loads(a.words.read_text(encoding="utf-8"))
    words = payload["words"]
    dropped = 0
    if a.duration:
        kept = [w for w in words if w["t0"] < a.duration]
        dropped = len(words) - len(kept)
        words = kept

    fixed, stats = smooth(words)
    stats["dropped_past_end"] = dropped

    # not with_suffix(): prefixes like "X.spoken-fixed" would lose ".spoken-fixed"
    out_json = Path(str(a.out_prefix) + ".json")
    out_srt = Path(str(a.out_prefix) + ".srt")
    out_json.write_text(json.dumps(
        {"source": a.words.name, "repair": stats, "words": fixed},
        ensure_ascii=False) + "\n", encoding="utf-8")
    with out_srt.open("w", encoding="utf-8") as fh:
        for k, w in enumerate(fixed, 1):
            fh.write(f"{k}\n{srt_stamp(w['t0'])} --> {srt_stamp(w['t1'])}\n{w['w']}\n\n")

    spans = sorted(w["t1"] - w["t0"] for w in fixed)
    gaps = sorted(fixed[i + 1]["t0"] - fixed[i]["t1"] for i in range(len(fixed) - 1))
    print(json.dumps({
        "words": len(fixed),
        "repair": stats,
        "word_span_median_s": round(spans[len(spans) // 2], 3),
        "word_span_p95_s": round(spans[int(len(spans) * 0.95)], 3),
        "collapsed_lt_0.05s": sum(1 for s in spans if s < 0.05),
        "absorbing_gt_1.5s": sum(1 for s in spans if s > 1.5),
        "real_pauses_ge_1s": sum(1 for g in gaps if g >= 1.0),
        "t0": round(fixed[0]["t0"], 3), "t_last": round(fixed[-1]["t1"], 3),
        "out": [str(out_json), str(out_srt)],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
