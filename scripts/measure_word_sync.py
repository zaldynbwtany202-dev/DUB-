#!/usr/bin/env python3
"""How far every spoken word sits from the original's word -- one metric, both builds.

The dub is judged by ear, so the numbers behind it must mean the same thing
before and after a change. This reads a placement (either assembler writes one),
rebuilds where each of our words actually lands -- the take's own silences part
it, the placement says where each part starts and how fast it runs -- and
compares that with the original's word times, word by word.

    python scripts/measure_word_sync.py work/into-the-wild \
        --placement work/into-the-wild/build/into-the-wild-placement-0-20.json \
        --timed work/into-the-wild/full-timed.json \
        --takes work/into-the-wild/takes-voice25 \
        --json work/into-the-wild/qa/word-sync-before.json
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

from align_by_words import group_targets, silence_splits, words_of  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--placement", type=Path, required=True)
    ap.add_argument("--timed", type=Path, required=True)
    ap.add_argument("--takes", type=Path, required=True)
    ap.add_argument("--groups", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    groups_path = a.groups or (a.work / "groups.json")
    doc = json.loads(groups_path.read_text(encoding="utf-8"))
    by_i = {int(g["i"]): g for g in doc["groups"]}
    src = [w for w in json.loads(a.timed.read_text(encoding="utf-8"))["words"]
           if w.get("t0") is not None and w.get("t1") is not None]
    pl = json.loads(a.placement.read_text(encoding="utf-8"))

    rows, errs, per_group = [], [], []
    for p in pl["groups"]:
        i = int(p["group"])
        grp = by_i.get(i)
        if grp is None:
            continue
        t0, t1 = float(grp["t0"]), float(grp["t1"])
        sw = [w for w in src if t0 - 0.02 <= w["t0"] < t1]
        tw = words_of(grp["text"])
        if not sw or not tw:
            continue
        tgt = group_targets(tw, sw)
        take = a.takes / f"g{i:03d}.mp3"
        if not take.exists():
            continue
        x, sr = sf.read(str(take), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        n = len(x) / sr
        b = [0.0] + silence_splits(x, sr) + [n]
        real = [(b[k], b[k + 1]) for k in range(len(b) - 1) if b[k + 1] - b[k] > 0.08]
        if len(real) < 2:
            real = [(0.0, n)]
        # مواضع كلماتنا كما وُضعت فعلًا: نسبة كل مقطع في الأخذة معروفة من السجل
        def seg_index(t: float) -> int:
            for k, (s, e) in enumerate(real):
                if s <= t < e:
                    return k
            return len(real) - 1
        # المقطع في السجل يعرف مدته وبدايته؛ نسبة كلماتنا داخله بحسب ترتيبها
        N = len(tw)
        d_real = np.array([e - s for s, e in real])
        # توزيع الكلمات على المقاطع كما يفعل المُجمِّع: بحسب مدة المقطع
        q = np.cumsum(d_real) / d_real.sum()
        ci = np.minimum(np.searchsorted(q, (np.arange(N) + 0.5) / N), len(real) - 1)
        pos = np.zeros(N)
        for k in range(len(real)):
            ks = np.where(ci == k)[0]
            if len(ks):
                pos[ks] = (np.arange(len(ks)) + 0.5) / len(ks)
        # مقاطع السجل مرتّبة؛ نطابقها بمقاطع الأخذة بالترتيب إن تساوى العدد
        segs = p.get("segments", [])
        if len(segs) != len(real):
            # سجل من مُجمِّع آخر يقصّ الصمت: نطابق كل مقطع سجل بأقرب مقطع أخذة
            mapping = []
            for s in segs:
                frm = float(s.get("from", 0.0))
                mapping.append(seg_index(min(frm, n - 1e-3)))
            ci = np.array([mapping[min(k, len(mapping) - 1)] for k in ci])
        pred = np.array([float(segs[c]["start"]) + float(segs[c]["dur"]) *
                         (1.0 / float(segs[c]["tempo"])) * po for c, po in zip(ci, pos)])
        e = np.abs(pred - tgt)
        errs += list(e)
        per_group.append({"group": i, "words": N, "mean": round(float(e.mean()), 3),
                          "median": round(float(np.median(e)), 3),
                          "p90": round(float(np.percentile(e, 90)), 3),
                          "max": round(float(e.max()), 3)})
    e = np.array(errs) if errs else np.array([0.0])
    out = {"label": a.label or a.placement.name, "placement": str(a.placement),
           "timed": str(a.timed), "words": int(len(errs)),
           "mean_s": round(float(e.mean()), 3), "median_s": round(float(np.median(e)), 3),
           "p90_s": round(float(np.percentile(e, 90)), 3), "max_s": round(float(e.max()), 3),
           "within_0.35_pct": round(float(100 * (e <= 0.35).mean()), 1),
           "groups": per_group}
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  {out['label']}: {len(errs)} كلمة · متوسط {out['mean_s']:.3f} ث · "
          f"وسيط {out['median_s']:.3f} · p90 {out['p90_s']:.2f} · أقصى {out['max_s']:.2f} · "
          f"داخل 0.35 ث {out['within_0.35_pct']:.0f}%")
    worst = sorted(per_group, key=lambda r: -r["mean"])[:5]
    for r in worst:
        print(f"    g{r['group']:03d}: متوسط {r['mean']:.2f} · أقصى {r['max']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
