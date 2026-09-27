#!/usr/bin/env python
"""Pick the steadiest take per group -- choice, not processing.

Every take the engine generates is an independent sample, and its baseline pitch
wanders from one take to the next (measured: 4.7 semitones across one folder).
That is what the ear reports as "the tone keeps changing". Nothing here moves a
pitch or filters anything: for each group it compares the take in place with the
candidates beside it and keeps the one whose own pitch sits closest to the target,
weighting in how much the take drifts inside itself.

    python3 scripts/pick_steady_takes.py --takes work/into-the-wild/takes-voice13 \
        --candidates work/into-the-wild/takes-voice13/candidates --apply

The chain is chosen for the whole run at once, not group by group: what the ear
hears is the step from one group to the next, so the sequence is picked by dynamic
programming from the last approved group (the anchor) to the end of the range, and
every group in that range takes part -- including the ones with no candidates, so
no gap is left unmeasured. Cost of a chain =
    sum of steps between neighbours
  + w_dev    * distance from the approved pitch
  + w_drift  * how much a take wanders inside itself
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


CACHE: dict = {}
CACHE_PATH: Path | None = None


def _key(path: Path, target: float) -> str:
    st = path.stat()
    return f"{path}|{st.st_size}|{int(st.st_mtime)}|{target}"


def measure(path: Path, target: float) -> dict:
    k = _key(path, target)
    if k in CACHE:
        r = dict(CACHE[k])
        r["path"] = str(path)
        return r
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    r = report(path.stem, x, sr, target, True)
    r["path"] = str(path)
    r["cost"] = abs(r["semis"]) + 0.35 * r.get("drift_semis", 0.0)
    if CACHE_PATH is not None:
        CACHE[k] = {kk: vv for kk, vv in r.items() if kk != "path"}
    return r


def chain_stats(semis: list[float]) -> dict:
    s = np.array(semis, dtype=float)
    steps = np.abs(np.diff(s)) if s.size > 1 else np.array([0.0])
    return {"mean_step": round(float(steps.mean()), 3), "max_step": round(float(steps.max()), 3),
            "mean_dev": round(float(np.abs(s).mean()), 3),
            "excursion": round(float(s.max() - s.min()), 3), "n": int(s.size)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--target", type=float, default=137.2,
                    help="هرتز — نبرة الأخذات التي أجازها المستمع (g000–g005)")
    ap.add_argument("--start", type=int, default=None, help="أول مجموعة في السلسلة")
    ap.add_argument("--end", type=int, default=None, help="آخر مجموعة (غير مشمولة)")
    ap.add_argument("--w-dev", type=float, default=0.35,
                    help="وزن الابتعاد عن النبرة المعتمدة (0 = الملساء فقط)")
    ap.add_argument("--w-drift", type=float, default=0.15, help="وزن الانزياح داخل الأخذة")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--smooth", action="store_true",
                    help="اختر السلسلة الأقل تنقلًا بين المجموعات المتجاورة لا الأقرب للهدف فقط")
    ap.add_argument("--json", default=None)
    ap.add_argument("--cache", default="work/.pitch-cache.json",
                    help="ذاكرة قياسات النبرة حتى لا يُقاس الملف أكثر من مرة")
    a = ap.parse_args()

    global CACHE_PATH
    CACHE_PATH = Path(a.cache) if a.cache else None
    if CACHE_PATH and CACHE_PATH.exists():
        try:
            CACHE.update(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass

    takes, cand = Path(a.takes), Path(a.candidates)
    have = sorted(int(m.group(1)) for p in takes.glob("g*.mp3")
                  if (m := re.match(r"g(\d+)$", p.stem)))
    if not have:
        raise SystemExit("لا أخذات")
    with_cand = sorted({int(m.group(1)) for p in cand.glob("g*.mp3")
                        if (m := re.match(r"g(\d+)\.alt\d+$", p.stem))})
    start = a.start if a.start is not None else min(with_cand)
    end = a.end if a.end is not None else max(have) + 1
    idx = [g for g in range(start, end) if g in have]
    print(f"الهدف {a.target} هرتز · المدى {idx[0]}→{idx[-1]} ({len(idx)} مجموعة) · "
          f"لها مرشحون: {[g for g in with_cand if g in idx]}\n")

    out = []
    for g in idx:
        base = takes / f"g{g:03d}.mp3"
        options = [measure(base, a.target)]
        options += [measure(p, a.target) for p in sorted(cand.glob(f"g{g:03d}.alt*.mp3"))]
        out.append({"group": g, "target": a.target, "options": options,
                    "chosen": min(options, key=lambda r: r["cost"])})
    by_group = {o["group"]: o for o in out}
    current = [by_group[g]["options"][0]["semis"] for g in idx]   # option 0 = the take in place

    if a.smooth:
        # Anchor: the last approved group before the range (g005 in this run).
        anchor_g = idx[0] - 1
        ap_ = takes / f"g{anchor_g:03d}.mp3"
        anchor = measure(ap_, a.target)["semis"] if ap_.exists() else 0.0
        print(f"  المرساة: g{anchor_g:03d} على {anchor:+.2f} نصف نغمة من الهدف\n")
        semis_of = {g: [o["semis"] for o in by_group[g]["options"]] for g in idx}
        drift_of = {g: [o.get("drift_semis", 0.0) for o in by_group[g]["options"]] for g in idx}

        def cost(g: int, j: int, prev: float | None) -> float:
            s = semis_of[g][j]
            c = a.w_dev * abs(s) + a.w_drift * drift_of[g][j]
            return c + (abs(s - prev) if prev is not None else abs(s - anchor))

        best: dict[tuple[int, int], tuple[float, int | None]] = {}
        for j in range(len(semis_of[idx[0]])):
            best[(idx[0], j)] = (cost(idx[0], j, None), None)
        for i in range(1, len(idx)):
            g, gp = idx[i], idx[i - 1]
            for j in range(len(semis_of[g])):
                opts = [(best[(gp, k)][0] + cost(g, j, semis_of[gp][k]), k)
                        for k in range(len(semis_of[gp]))]
                best[(g, j)] = min(opts)
        j = min(range(len(semis_of[idx[-1]])), key=lambda k: best[(idx[-1], k)][0])
        path = {idx[-1]: j}
        for i in range(len(idx) - 1, 0, -1):
            path[idx[i - 1]] = best[(idx[i], path[idx[i]])][1]
        for g in idx:
            by_group[g]["chosen"] = by_group[g]["options"][path[g]]
        print("  اختيار السلسلة على المدى كله (برمجة ديناميكية):")

    for o in out:
        best_o = o["chosen"]
        print(f"  g{o['group']:03d}: " + " · ".join(
            f"{Path(x['path']).stem} {x['median']:.0f}Hz ({x['semis']:+.2f}، انزياح "
            f"{x.get('drift_semis', 0):.2f}){' ←المختار' if x is best_o else ''}"
            for x in o["options"]))

    chosen = [o["chosen"]["semis"] for o in out]
    if a.smooth:
        changed = [o["group"] for o in out if Path(o["chosen"]["path"]) != takes / f"g{o['group']:03d}.mp3"]
        cs, bs = chain_stats(current), chain_stats(chosen)
        print(f"\n  قبل:  متوسط الخطوة {cs['mean_step']:.2f} · أقواها {cs['max_step']:.2f} · "
              f"متوسط البعد عن الهدف {cs['mean_dev']:.2f} · المدى الكلي {cs['excursion']:.2f}")
        print(f"  بعد:  متوسط الخطوة {bs['mean_step']:.2f} · أقواها {bs['max_step']:.2f} · "
              f"متوسط البعد عن الهدف {bs['mean_dev']:.2f} · المدى الكلي {bs['excursion']:.2f}")
        print(f"  تغيّرت {len(changed)} مجموعة: {changed}")

    if a.apply:
        for o in out:
            best_o = o["chosen"]
            if Path(best_o["path"]) != takes / f"g{o['group']:03d}.mp3":
                base = takes / f"g{o['group']:03d}.mp3"
                keep = cand / f"g{o['group']:03d}.original.mp3"
                if base.exists() and not keep.exists():
                    shutil.copy2(base, keep)      # the old take is never thrown away
                shutil.copy2(best_o["path"], base)
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"range": [idx[0], idx[-1]], "anchor": anchor if a.smooth else None,
             "before": chain_stats(current), "after": chain_stats(chosen) if a.smooth else None,
             "groups": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    if CACHE_PATH is not None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(CACHE, ensure_ascii=False), encoding="utf-8")
    print("\n  " + ("طُبّق الاختيار." if a.apply else "معاينة فقط (--apply للتنفيذ)."))


if __name__ == "__main__":
    main()
