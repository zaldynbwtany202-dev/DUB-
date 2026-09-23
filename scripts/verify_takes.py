#!/usr/bin/env python3
"""Check that every recorded take actually says the whole group.

The TTS truncates long texts. It is not a theory: group 12 of Into the Wild was
generated from 406 characters and came back with 63 words against the script's
80, ending mid-clause at «بتقول بتقول». The audio is clean, the voice is right,
and a duration check barely notices -- the take is 30.1 seconds where 34.8 were
expected, and this voice's own reading rate varies by more than that between
takes, so a ratio cannot separate a fast read from a lost half.

So ask directly. Transcribe the take, align it to the group's own text, and
report how much of that text was found and whether the tail is present. A take
that stops early is a take that has to be redone, and finding that out after
seventy-eight of them are recorded is the expensive way.

Usage
    python scripts/verify_takes.py SLUG [--groups work/SLUG/groups.json]
                                      [--limit 20] [--threads 4]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from align_vocal_timings import align, norm  # noqa: E402
from asr_to_words import words_with_dtw  # noqa: E402

WHISPER = ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ".cache/models/whisper-ggml/ggml-small.bin"
TMP = Path(".cache/takecheck")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--groups", default=None)
    ap.add_argument("--limit", type=int, default=10 ** 9)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--min-coverage", type=float, default=0.95)
    a = ap.parse_args()

    work = Path("work") / a.slug
    groups_path = Path(a.groups) if a.groups else (work / "groups.json")
    groups = json.loads(groups_path.read_text(encoding="utf-8"))["groups"]
    TMP.mkdir(parents=True, exist_ok=True)

    rows, bad = [], []
    for g in groups[: a.limit]:
        take = work / "takes" / f"g{g['i']:03d}.mp3"
        if not take.is_file():
            continue
        out = TMP / f"check-{g['i']:03d}.json"
        if not out.is_file():
            subprocess.run([WHISPER, "-m", MODEL, "-f", str(take), "-l", "ar",
                            "-t", str(a.threads), "-ojf", "-dtw", "small",
                            "-of", str(out.with_suffix(""))],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        heard = words_with_dtw(out)
        text_words = [w for w in g["text"].split() if w]
        src = [(norm(w["w"]), w["t0"], w["t1"]) for w in heard]
        dst = [(norm(w), 0.0, 0.0) for w in text_words]
        pairs = align(src, dst)
        matched = {j for j, _, _ in pairs}
        cov = len(matched) / max(len(text_words), 1)
        # Where does the take stop relative to the text? The last fifth of the
        # script is what a truncation eats first.
        tail_from = max(len(text_words) * 4 // 5, 0)
        tail_n = len(text_words) - tail_from
        tail_hit = sum(1 for j in range(tail_from, len(text_words)) if j in matched)
        tail_cov = tail_hit / max(tail_n, 1)
        rows.append({"group": g["i"], "chars": g["chars"], "text_words": len(text_words),
                     "heard_words": len(heard), "matched": len(matched),
                     "coverage": round(cov, 3), "tail_coverage": round(tail_cov, 3)})
        flag = ""
        if cov < a.min_coverage or tail_cov < 0.8:
            flag = "  ← ناقص، يُعاد"
            bad.append(g["i"])
        print(f"  {g['i']:3d}  {g['chars']:4d} حرف · نص {len(text_words):3d} · سُمع "
              f"{len(heard):3d} · مطابق {len(matched):3d}  تغطية {cov*100:5.1f}% · "
              f"الذيل {tail_cov*100:5.1f}%{flag}", flush=True)

    (work / "take-check.json").write_bytes(json.dumps(
        {"slug": a.slug, "limit": a.limit, "rows": rows,
         "incomplete": bad}, ensure_ascii=False, indent=1)
        .encode("utf-8", "surrogateescape"))
    print()
    if bad:
        print(f"  {len(bad)} تسجيلًا ناقصًا: {bad}")
        print("  هذه تُعاد بنص أقصر — أو تُقسَّم إلى قطع.")
    else:
        print(f"  كل التسجيلات ({len(rows)}) كاملة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
