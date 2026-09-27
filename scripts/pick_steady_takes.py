#!/usr/bin/env python
"""Pick the steadiest take per group -- choice, not processing.

Every take the engine generates is an independent sample, and its baseline pitch
wanders from one take to the next (measured: 4.7 semitones across one folder).
That is what the ear reports as "the tone keeps changing". Nothing here moves a
pitch or filters anything: for each group it compares the take in place with the
candidates beside it and keeps the one whose own pitch sits closest to the target,
weighting in how much the take drifts inside itself.

    python3 scripts/pick_steady_takes.py --takes work/into-the-wild/takes-voice13 \
        --candidates work/into-the-wild/takes-voice13/candidates --dry-run
    python3 scripts/pick_steady_takes.py ... --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from measure_voice_pitch import report  # noqa: E402


def measure(path: Path, target: float) -> dict:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    r = report(path.stem, x, sr, target, True)
    r["path"] = str(path)
    r["cost"] = abs(r["semis"]) + 0.35 * r.get("drift_semis", 0.0)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--target", type=float, default=137.2,
                    help="هرتز — نبرة الأخذات التي أجازها المستمع (g000–g005)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--smooth", action="store_true",
                    help="اختر السلسلة الأقل تنقلًا بين المجموعات المتجاورة لا الأقرب للهدف فقط")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    takes, cand = Path(a.takes), Path(a.candidates)
    groups = sorted({int(m.group(1)) for p in cand.glob("g*.mp3")
                     if (m := re.match(r"g(\d+)\.alt\d+$", p.stem))})
    print(f"الهدف {a.target} هرتز · مجموعات لها مرشحون: {groups}\n")
    out = []
    for g in groups:
        base = takes / f"g{g:03d}.mp3"
        options = [measure(base, a.target)] if base.exists() else []
        options += [measure(p, a.target) for p in
                    sorted(cand.glob(f"g{g:03d}.alt*.mp3"))]
        out.append({"group": g, "target": a.target, "options": options,
                    "chosen": min(options, key=lambda r: r["cost"])})

    if a.smooth and len(out) > 1:
        # What the ear calls "the tone keeps changing" is the step from one group to
        # the next, so the choice is made for the whole run at once: the sequence of
        # takes that steps least, with a small pull towards the approved pitch and a
        # small penalty for a take that drifts inside itself. Dynamic programming,
        # because the best take for one group depends on what its neighbours chose.
        seq = [o for o in out if o["group"] == min(groups) - 1 and False]
        by_group = {o["group"]: o for o in out}
        anchor = 12 * np.log2(140.4 / a.target)        # g005, the last approved take
        idx = sorted(by_group)
        costs = {}
        for g in idx:
            costs[g] = [o["semis"] for o in by_group[g]["options"]]
        best = {}
        for j, s in enumerate(costs[idx[0]]):
            best[(idx[0], j)] = (abs(s - anchor) + 0.10 * abs(s)
                                 + 0.15 * by_group[idx[0]]["options"][j].get("drift_semis", 0),
                                 None)
        for i in range(1, len(idx)):
            g, gp = idx[i], idx[i - 1]
            for j, s in enumerate(costs[g]):
                cands = [(best[(gp, k)][0] + abs(s - costs[gp][k]) + 0.10 * abs(s)
                          + 0.15 * by_group[g]["options"][j].get("drift_semis", 0), k)
                         for k in range(len(costs[gp]))]
                best[(g, j)] = min(cands)
        j = min(range(len(costs[idx[-1]])), key=lambda k: best[(idx[-1], k)][0])
        path = {idx[-1]: j}
        for i in range(len(idx) - 1, 0, -1):
            path[idx[i - 1]] = best[(idx[i], path[idx[i]])][1]
        for g in idx:
            by_group[g]["chosen"] = by_group[g]["options"][path[g]]
        print("  اختيار السلسلة الأقل تنقلًا (برمجة ديناميكية على المدى كله):")

    for o in out:
        best_o = o["chosen"]
        print(f"  g{o['group']:03d}: " + " · ".join(
            f"{Path(x['path']).stem} {x['median']:.0f}Hz ({x['semis']:+.2f}، انزياح "
            f"{x.get('drift_semis', 0):.2f}){' ←المختار' if x is best_o else ''}"
            for x in o["options"]))
        if a.apply and Path(best_o["path"]) != takes / f"g{o['group']:03d}.mp3":
            base = takes / f"g{o['group']:03d}.mp3"
            keep = cand / f"g{o['group']:03d}.original.mp3"
            if base.exists() and not keep.exists():
                shutil.copy2(base, keep)          # the old take is never thrown away
            shutil.copy2(best_o["path"], base)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n  " + ("طُبّق الاختيار." if a.apply else "معاينة فقط (--apply للتنفيذ)."))


if __name__ == "__main__":
    main()
