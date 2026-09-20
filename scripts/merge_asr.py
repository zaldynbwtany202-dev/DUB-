#!/usr/bin/env python
"""Fold beam-search transcriptions back into the stored vocal ASR.

Greedy decoding leaves words on the table, and the cost is not evenly spread.
Segment 01 of the Sindbad stem -- 110 s to 220 s, which is where groups 7 and 8
sit -- came back with 284 words at 2.57 words per second against a script rate
of 3.15, so 55 percent of group 8's words ended up interpolated between measured
edges rather than measured. Those two groups carry the worst offsets in the
preview: -2.48 s and -2.69 s median, against 0.14 s for the well-covered ones.

Re-running the same model with beam search (-bs 5) recovered 352 words from the
same 110 s, 3.19 words per second. Nothing about the audio changed; the decoder
was throwing the words away.

Usage:
    python scripts/merge_asr.py SLUG --beam .cache/voc/beam [PATTERN...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
from scripts.transcribe_vocals import words_from_whisper  # noqa: E402

STRIDE_S = 110.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--beam", required=True, help="dir of seg-NN.json beam outputs")
    ap.add_argument("--asr", default=None)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    work = Path("work") / a.slug
    asr_path = Path(a.asr) if a.asr else (work / "vocal-asr.json")
    d = json.loads(asr_path.read_bytes().decode("utf-8", "surrogateescape"))
    by = {s["segment"]: s for s in d["segments"]}

    changed = []
    for p in sorted(Path(a.beam).glob("seg-*.json")):
        key = p.stem.split("-")[1]
        try:
            ws = words_from_whisper(str(p))
        except Exception as e:
            print(f"  [{key}] تالف: {e}")
            continue
        off = int(key) * STRIDE_S
        for w in ws:
            w["t0"] = round(w["t0"] + off, 3)
            w["t1"] = round(w["t1"] + off, 3)
        old = len(by.get(key, {}).get("words", []))
        if ws and len(ws) > old:
            by[key] = {"segment": key, "offset_s": off, "words": ws}
            changed.append((key, old, len(ws)))
            print(f"  [{key}] {old} ← {len(ws)} كلمة (+{len(ws)-old})")

    d["segments"] = [by[k] for k in sorted(by)]
    asr_path.write_bytes(json.dumps(d, ensure_ascii=False, indent=1)
                         .encode("utf-8", "surrogateescape"))
    total = sum(len(s["words"]) for s in d["segments"])
    print(f"\n  الإجمالي الآن: {total} كلمة من {len(d['segments'])} مقطع")
    if not changed:
        print("  لا تحسّن")

    if a.commit and changed:
        import subprocess
        subprocess.run(["git", "add", "-A", str(asr_path)], check=True)
        r = subprocess.run(["git", "commit", "-q", "-m",
                            "Recover words the greedy decoder dropped\n\n"
                            + " ".join(f"[{k}] {o}->{n}" for k, o, n in changed[:6])],
                           capture_output=True, text=True)
        if r.returncode == 0:
            p = subprocess.run(["git", "push", "origin", "arena/01a09fa1-dub"],
                               capture_output=True, text=True)
            print("  ✓ التزام + دفع" if p.returncode == 0 else "  التزام ✓ · الدفع فشل")


if __name__ == "__main__":
    main()
