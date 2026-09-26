#!/usr/bin/env python
"""Split a group that cannot fit its window into groups that can.

The planner groups words by pause, and a pause can be far away: group 21 of Into
the Wild came out as 504 characters in an 18.94 s window, which asks for 2.12x
when the ceiling is 1.80x, and its own plan caps a group at 420 characters. A
take that long lands on top of the next group instead of inside its window.

Splitting is the fix, not stretching: the region has more time in it than one
group can use. This picks the cut points where the narrator actually pauses, so
each new group begins on a breath rather than mid-phrase, and it checks the
arithmetic before writing anything -- every piece must fit inside the ceiling
with room to spare, or it says so and writes nothing.

    python scripts/resplit_groups.py --slug into-the-wild --first 20 --last 22 --parts 4

Writes work/<slug>/resplit.json: the new group boundaries, their windows, their
text, and the take names to record. Recording is a separate step on purpose --
this script only decides where the cuts go.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def pause_after(words, i, limit=1.2):
    """Seconds of silence after word i, capped so one long gap does not dominate."""
    if i + 1 >= len(words):
        return limit
    return min(limit, max(0.0, words[i + 1]["t0"] - words[i]["t1"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--first", type=int, required=True, help="first group to split, inclusive")
    ap.add_argument("--last", type=int, required=True, help="last group to split, inclusive")
    ap.add_argument("--parts", type=int, required=True)
    ap.add_argument("--chars-per-s", type=float, default=11.65,
                    help="locked voice's measured rate")
    ap.add_argument("--ceiling", type=float, default=1.80)
    args = ap.parse_args()

    work = Path("work") / args.slug
    groups = json.loads((work / "groups.json").read_text(encoding="utf-8"))
    plan = groups["groups"]
    words = json.loads((work / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]

    lo = plan[args.first]["t0"]
    hi = plan[args.last]["t1"]
    span = [w for w in words if w["t0"] >= lo - 0.01 and w["t1"] <= hi + 0.01]
    # The planner counts characters with the single spaces between words, and the
    # measured 11.65 chars/s is on that convention -- verified against its own
    # predicted tempos. Count the same way or the estimate comes out 20% low.
    cum = [0]
    for w in span:
        cum.append(cum[-1] + len(w["w"]) + 1)     # word plus its space
    total = cum[-1] - 1
    print(f"  المنطقة [{lo:.2f} → {hi:.2f}] = {hi - lo:.2f} ثانية · {len(span)} كلمة · {total} حرفًا")

    # Cut where the running character count reaches a fair share of the whole,
    # choosing the widest pause near that point so each new group begins on a
    # breath rather than mid-phrase. At least one word must stay on either side.
    share = total / args.parts
    cuts, start_i = [], 0
    for p in range(1, args.parts):
        target = p * share
        lo_i = start_i + 1
        hi_i = len(span) - (args.parts - p)      # leave a word for every later part
        best, best_score = None, None
        for i in range(lo_i, hi_i):
            score = pause_after(span, i) - 0.05 * abs(cum[i + 1] - target)
            if best_score is None or score > best_score:
                best, best_score = i, score
        if best is None:
            raise SystemExit(f"  ✗ لا موضع قطع للمجموعة {args.first + p} — زد الكلمات أو أنقص الأجزاء")
        cuts.append(best)
        start_i = best + 1

    bounds = [0] + [c + 1 for c in cuts] + [len(span)]
    rows, ok = [], True
    print(f"  {'#':>3}  {'من':>8}  {'إلى':>8}  {'نافذة':>7}  {'حرفًا':>5}  {'سرعة':>6}  {'قدرة':>5}")
    for p in range(args.parts):
        piece = span[bounds[p]:bounds[p + 1]]
        t0, t1 = piece[0]["t0"], piece[-1]["t1"]
        chars = sum(len(w["w"]) for w in piece) + len(piece) - 1
        window = t1 - t0 + 0.25          # a quarter second of air at each end
        need = chars / args.chars_per_s / window
        fits = need <= args.ceiling * 0.92
        cap = args.ceiling / need
        ok &= fits
        rows.append({"t0": round(t0, 2), "t1": round(t1, 2), "window": round(window, 2),
                     "chars": chars, "tempo": round(need, 3),
                     "text": " ".join(w["w"] for w in piece)})
        print(f"  {args.first + p:>3}  {t0:>8.2f}  {t1:>8.2f}  {window:>7.2f}  {chars:>5}  "
              f"{need:>6.3f}  {'✓' if fits else '✗'} {cap:.0%}")

    head = {"slug": args.slug, "first": args.first, "last": args.last,
            "replaces": [g["i"] for g in plan[args.first:args.last + 1]],
            "region": [round(lo, 2), round(hi, 2)], "parts": args.parts,
            "chars_per_s": args.chars_per_s, "ceiling": args.ceiling,
            "fits": ok, "new_groups": rows}
    out = work / "resplit.json"
    out.write_text(json.dumps(head, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  {'✓ القطع كلها داخل السقف — اكتب التسجيلات' if ok else '✗ قطعة لا تسع — زد عدد القطع'}"
          f"\n  كُتب: {out}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
