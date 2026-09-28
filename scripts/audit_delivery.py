#!/usr/bin/env python3
"""Measure a pronunciation pass and a run's smoothness, then report.

Two questions, one report:

  * Pronunciation: three takes of the same sentence under three treatments
    (as written, with clause punctuation, with Egyptian spellings) -- measured by
    the local Whisper against the written line and by pitch steadiness.
  * Smoothness: for a run of groups recorded with one voice, the pitch step
    between every neighbouring pair; a run that reads smoothly has small steps.

    .venv/bin/python scripts/audit_delivery.py --slug clip-happiness --takes work/into-the-wild/takes-voice25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from build_pro_voice_samples import asr, score  # noqa: E402
from measure_voice_pitch import report as pitch_report  # noqa: E402


def pitch_of(path: Path) -> dict:
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    p = pitch_report(path.stem, x, sr, None, True)
    return {"hz": p["median"], "drift": p["drift_semis"], "seconds": round(len(x) / sr, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pron", nargs="+", default=[], help="variants: label=path")
    ap.add_argument("--expected", default=None, help="the written line all variants should say")
    ap.add_argument("--run", default=None, help="directory of g###.mp3 takes, one per group")
    ap.add_argument("--window-chars", type=int, default=0)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()

    out: dict = {"pron": [], "run": []}
    work = ROOT / "work/build-delivery"
    work.mkdir(parents=True, exist_ok=True)
    if a.pron:
        for item in a.pron:
            label, _, rel = item.partition("=")
            path = ROOT / rel
            p = pitch_of(path)
            text = asr(path, work)
            sc = score(text, a.expected) if a.expected else None
            row = {"label": label, "file": rel, **p, "asr": text, "score": sc}
            out["pron"].append(row)
            head = f"{label:<28} {p['hz']:>6.1f} Hz  {p['seconds']:>5.1f}s  drift {p['drift']:.2f}"
            if sc:
                head += f"  match {sc['char_match']:.0%}  errs {sc['word_errors']}"
            print(head)
            print(f"    {text}")
    if a.run:
        takes = sorted(Path(ROOT / a.run).glob("g*.mp3"))
        rows = []
        for t in takes:
            p = pitch_of(t)
            rows.append({"group": t.stem, **p, "file": str(t.relative_to(ROOT))})
        steps = []
        for i in range(1, len(rows)):
            st = 12 * np.log2(rows[i]["hz"] / rows[i - 1]["hz"])
            steps.append({"between": f"{rows[i-1]['group']}→{rows[i]['group']}",
                          "semitones": round(float(st), 2)})
        print(f"\n  run: {len(rows)} takes")
        for r in rows:
            print(f"    {r['group']}  {r['hz']:>6.1f} Hz  {r['seconds']:>5.1f}s  drift {r['drift']:.2f}")
        print("  steps:")
        for s in steps:
            flag = "  ⚠ كبير" if abs(s["semitones"]) > 1.0 else ""
            print(f"    {s['between']:<14}{s['semitones']:>+6.2f} نصف نغمة{flag}")
        mags = [abs(s["semitones"]) for s in steps] or [0]
        out["run"] = {"takes": rows, "steps": steps,
                      "mean_step": round(float(np.mean(mags)), 2),
                      "max_step": round(float(np.max(mags)), 2)}
        print(f"  متوسط الخطوة {np.mean(mags):.2f} · أكبرها {np.max(mags):.2f} نصف نغمة")
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"  → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
