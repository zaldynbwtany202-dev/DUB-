#!/usr/bin/env python
"""Redistribute the film's time so every group fits the locked tempo band.

Found by measurement, not by theory: the assembler clamps at the locked ceiling
(1.80x) and refuses to slow below natural (1.00x). A group whose take is too long
for its window therefore talks over its neighbour; a group whose take is too short
leaves dead air inside its window. In the delivered run, three groups of twenty
were broken exactly this way -- g021 overran by 4.79 s, g025 by 4.09 s, and g020
left 4.80 s of silence.

This moves the *boundaries between groups* -- never the voice -- borrowing seconds
from a neighbour that has room, and snapping every moved boundary onto a real pause
in the original narration so the words of each group stay where they were.

    python3 scripts/time_budget.py --slug into-the-wild --takes work/into-the-wild/takes-voice13 \
        --start 6 --end 26 --target 1.50 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def take_seconds(path: Path) -> float:
    return float(sf.info(str(path)).duration)


def pauses(words: list[dict], min_gap: float = 0.18) -> list[float]:
    """Midpoints of the real silences in the original narration."""
    out = []
    for a, b in zip(words, words[1:]):
        gap = b["t0"] - a["t1"]
        if gap >= min_gap:
            out.append((a["t1"] + b["t0"]) / 2.0)
    return out


def snap(t: float, holes: list[float], limit: float = 0.9) -> tuple[float, float]:
    if not holes:
        return t, 0.0
    near = min(holes, key=lambda h: abs(h - t))
    d = near - t
    if abs(d) <= limit:
        return near, d
    return t, 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--takes", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--tempo-max", type=float, default=1.80)
    ap.add_argument("--tempo-min", type=float, default=1.05)
    ap.add_argument("--target", type=float, default=1.50)
    ap.add_argument("--max-move", type=float, default=6.0)
    ap.add_argument("--aim", type=float, default=1.75,
                    help="السرعة التي تُستهدف في الحلّ، فيبقى هامش تحت السقف الصارم")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    work = Path("work") / a.slug
    plan = json.loads((work / "groups.json").read_text(encoding="utf-8"))
    groups = plan["groups"]
    words = json.loads((work / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
    holes = pauses(words)
    takes = Path(a.takes)

    idx = [g for g in range(a.start, min(a.end, len(groups)))]
    nat = {}
    for g in idx:
        p = takes / f"g{g:03d}.mp3"
        if not p.exists():
            print(f"  ✗ لا أخذة للمجموعة {g} — أخرج")
            return 1
        nat[g] = take_seconds(p)

    before = {g: groups[g]["t1"] - groups[g]["t0"] for g in idx}
    tempo = {g: nat[g] / before[g] for g in idx}
    moved = {g: 0.0 for g in idx[:-1]}

    def room(g: int) -> float:
        """How much this group's window can shrink and still stay under the ceiling."""
        return max(0.0, groups[g]["t1"] - groups[g]["t0"] - nat[g] / a.aim)

    def need(g: int) -> float:
        return max(0.0, nat[g] / a.aim - (groups[g]["t1"] - groups[g]["t0"]))

    def win(g: int) -> float:
        return groups[g]["t1"] - groups[g]["t0"]

    def move_edge(edge: int, shift: float) -> float:
        """Move the boundary between group `edge` and `edge+1`; returns what it moved."""
        allowed = a.max_move - abs(moved[edge])
        shift = float(np.clip(shift, -allowed, allowed))
        want = groups[edge]["t1"] + shift
        new, _ = snap(want, holes)
        shift = float(np.clip(new - groups[edge]["t1"], -allowed, allowed))
        if abs(shift) < 0.05:
            return 0.0
        groups[edge]["t1"] = round(groups[edge]["t1"] + shift, 3)
        groups[edge + 1]["t0"] = round(groups[edge]["t1"], 3)
        # `window` is a field the assembler reads directly; a plan that moves t0/t1
        # without it leaves the assembler working from the old slot. Lesson learned
        # the expensive way: the assembler clamped at 1.8x and ran 4.08 s late
        # while this tool believed the windows were fine.
        for g_i in (edge, edge + 1):
            groups[g_i]["window"] = round(groups[g_i]["t1"] - groups[g_i]["t0"], 3)
        moved[edge] += shift
        return abs(shift)

    # Pass 1: a group that overruns its window borrows seconds from whichever
    # neighbour has the most room. Extending group g means moving its start edge
    # earlier (donor on the left) or its end edge later (donor on the right).
    for _ in range(400):
        viol = [g for g in idx if need(g) > 0.02]
        if not viol:
            break
        g = max(viol, key=need)
        options = []
        if g - 1 in nat and room(g - 1) > 0.05:
            options.append((room(g - 1), (g - 1), -1))     # donor left: edge g-1 moves left
        if g + 1 in nat and room(g + 1) > 0.05:
            options.append((room(g + 1), g, +1))           # donor right: edge g moves right
        if not options:
            print(f"  ! لا مانح متاح للمجموعة {g} (نقص {need(g):.2f} ث)")
            break
        _, edge, sign = max(options)
        amount = min(need(g), room(edge if sign < 0 else edge + 1), a.max_move - abs(moved[edge]))
        got = move_edge(edge, sign * amount)
        if got < 0.05:
            print(f"  ! الحدّ {edge} لم يتحرك (لا وقفة قريبة)")
            break

    # Pass 2: a group that ends early leaves dead air; hand that time to the
    # tightest neighbour so the gap goes where it is needed.
    for _ in range(200):
        slack = [g for g in idx if win(g) - nat[g] > 0.15]
        if not slack:
            break
        g = max(slack, key=lambda x: win(x) - nat(x))
        nb = max((n for n in (g - 1, g + 1) if n in nat), default=None,
                 key=lambda n: nat[n] / win(n))
        if nb is None or nat[nb] / win(nb) <= 1.0 or win(g) - nat[g] <= 0.15:
            break
        edge = g - 1 if nb == g - 1 else g
        # Giving time from g: to a left neighbour the edge at g's start moves
        # later; to a right neighbour the edge at g's end moves earlier.
        sign = +1 if nb == g - 1 else -1
        give = min(win(g) - nat[g], a.max_move - abs(moved[edge]), 5.0)
        if give < 0.05:
            break
        if move_edge(edge, sign * give) < 0.05:
            break

    # Keep the assembler's own window field in step for every group in the range,
    # not just the ones whose edges moved: a stale window makes the assembler clamp
    # at the ceiling while this tool reports a healthy plan.
    for g in idx:
        groups[g]["window"] = round(groups[g]["t1"] - groups[g]["t0"], 3)
    after = {g: groups[g]["t1"] - groups[g]["t0"] for g in idx}
    stale = [g for g in idx if abs(groups[g]["window"] - after[g]) > 0.01]
    if stale:
        print(f"  ✗ نوافذ غير متسقة بعد الكتابة: {stale}")
        return 2

    print(f"{'مجموعة':>8}{'نافذة قبل':>11}{'نافذة بعد':>11}{'سرعة قبل':>10}{'سرعة بعد':>10}   الحكم")
    broken_before = broken_after = 0
    rows = []
    for g in idx:
        t0 = nat[g] / before[g]
        t1 = nat[g] / after[g]
        flags = []
        if t0 > a.tempo_max + 1e-6 or (before[g] - nat[g]) > 0.15:
            broken_before += 1
        if t1 > a.tempo_max + 1e-6 or (after[g] - nat[g]) > 0.15:
            broken_after += 1
            flags.append("✗")
        print(f"{g:>8}{before[g]:>11.2f}{after[g]:>11.2f}{t0:>10.2f}{t1:>10.2f}   {' '.join(flags)}")
        rows.append({"group": g, "natural": round(nat[g], 2),
                     "window_before": round(before[g], 3), "window_after": round(after[g], 3),
                     "tempo_before": round(t0, 3), "tempo_after": round(t1, 3)})
    print(f"\n  معطوب قبل: {broken_before} · بعد: {broken_after} · "
          f"متوسط السرعة بعد: {np.mean([r['tempo_after'] for r in rows]):.3f}×")

    if a.apply and broken_after == 0:
        backup = work / "groups.before-timeplan.json"
        if not backup.exists():
            backup.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (work / "groups.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        print(f"  ✓ groups.json محدَّث (النسخة السابقة في {backup.name})")
    elif a.apply:
        print("  ✗ لم أُطبّق: بقيت مجموعات خارج المدى")
    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"slug": a.slug, "range": [idx[0], idx[-1]],
                                   "rows": rows, "broken_before": broken_before,
                                   "broken_after": broken_after},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {out}")
    return 0 if broken_after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
