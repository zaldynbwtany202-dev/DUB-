#!/usr/bin/env python
"""Align the locked script to word timings read off the isolated vocal stem.

Produces the timing file the dub should be built on. The one derived from the
mix leaves about 15 percent of script words anchored by a real detection; the
vocal stem anchors 93 percent. Against the mix-derived file the new timings
agree overall (median -0.04 s) but disagree locally: a quarter of words move by
more than a second, 18 percent by more than two, the worst by 19. That local
disagreement is the sync fault -- a median can be right while a quarter of the
words are wrong, which is why the offset looks random rather than drifting.

Two bugs cost most of the debugging time and both are recorded here because they
are easy to repeat:

1. The pairing once returned the loop variable instead of best[j][0]. Every
   script word came out matched to the final token of the audio, and the symptom
   was a median difference of +1104 s -- large enough to look like a units error
   if you do not stop and read the loop.

2. One difflib pass over 27k against 30k characters does not work. Wherever the
   texts diverge it emits a single enormous replace block, and pairing its
   characters positionally maps the start of one text onto the end of the other.
   It now anchors on exact runs of MIN_ANCHOR characters or more and aligns the
   gaps between them.

Usage:
    python scripts/align_vocal_timings.py SLUG
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics as st
from pathlib import Path

MIN_ANCHOR = 24


def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s)
    for x, y in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"),
                 ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(x, y)
    return s.strip()


def owner(spans, pos):
    for i, (s, e) in enumerate(spans):
        if s <= pos < e:
            return i
    return None


def char_map(A, B):
    """Map positions of A to positions of B, anchored on long exact runs."""
    anchors = [(a0, b0, a1 - a0)
               for tag, a0, a1, b0, b1
               in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes()
               if tag == "equal" and (a1 - a0) >= MIN_ANCHOR]
    cm = {}
    for a0, b0, L in anchors:
        for k in range(L):
            cm[a0 + k] = b0 + k
    regions, pa, pb = [], 0, 0
    for a0, b0, L in anchors:
        regions.append((pa, a0, pb, b0))
        pa, pb = a0 + L, b0 + L
    regions.append((pa, len(A), pb, len(B)))
    for a0, a1, b0, b1 in regions:
        if a1 <= a0 or b1 <= b0:
            continue
        sa, sb = A[a0:a1], B[b0:b1]
        if len(sa) > 4000 or len(sb) > 4000:
            for k in range(min(len(sa), len(sb))):
                cm[a0 + int(k * len(sa) / max(len(sb), 1))] = b0 + k
            continue
        for tag, x0, x1, y0, y1 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
            if tag in ("equal", "replace"):
                for k in range(min(x1 - x0, y1 - y0)):
                    cm[a0 + x0 + k] = b0 + y0 + k
    return cm


def align(src, dst):
    """src, dst: lists of (word, t0, t1). Returns list of (dst_i, src_i, score)."""
    A = "".join(w for w, _, _ in src)
    B = "".join(w for w, _, _ in dst)
    if not A or not B:
        return []
    sp_a, at = [], 0
    for w, _, _ in src:
        sp_a.append((at, at + len(w)))
        at += len(w)
    sp_b, at = [], 0
    for w, _, _ in dst:
        sp_b.append((at, at + len(w)))
        at += len(w)
    cm = char_map(A, B)
    score = {}
    for ap, bp in cm.items():
        i, j = owner(sp_a, ap), owner(sp_b, bp)
        if i is None or j is None:
            continue
        score[(j, i)] = score.get((j, i), 0) + 1
    best = {}
    for (j, i), n in score.items():
        if j not in best or n > best[j][1]:
            best[j] = (i, n)
    # best[j][0], never the loop variable i
    return sorted((j, best[j][0], best[j][1]) for j in best)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--asr", default=None)
    ap.add_argument("--script", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    work = Path("work") / a.slug
    asr_path = Path(a.asr) if a.asr else (work / "vocal-asr.json")
    src_path = Path(a.script) if a.script else (work / "FvWDRz8WP5s.spoken-fixed.json")

    d = json.loads(asr_path.read_bytes().decode("utf-8", "surrogateescape"))
    asr = [(w["w"], w["t0"], w["t1"]) for s in d["segments"] for w in s["words"]]
    asr.sort(key=lambda x: x[1])
    sw = json.loads(src_path.read_bytes().decode("utf-8", "surrogateescape"))["words"]
    script = [(w["w"], w.get("t0"), w.get("t1")) for w in sw]

    src = [(norm(w), t0, t1) for w, t0, t1 in asr if t0 is not None]
    dst = [(norm(w), 0.0, 0.0) for w, _, _ in script]
    pairs = align(src, dst)
    anch = {j: i for j, i, _ in pairs}

    print("  كلمات السكربت %d · كلمات الصوت المعزول %d" % (len(script), len(asr)))
    print("  مثبَّتة بقياس حقيقي: %d (%.0f%%)" % (len(anch), 100 * len(anch) / max(len(script), 1)))

    out_words, filled = [], 0
    for j, (w, old0, old1) in enumerate(script):
        if j in anch:
            t0, t1 = src[anch[j]][1], src[anch[j]][2]
        else:
            prev = next((out_words[k]["t1"] for k in range(len(out_words) - 1, -1, -1)
                         if out_words[k].get("measured")), None)
            nxt_i = next((anch[m] for m in range(j + 1, len(script)) if m in anch), None)
            t1 = src[nxt_i][1] if nxt_i is not None else (prev if prev is not None else 0.0)
            t0 = prev if prev is not None else max(t1 - 0.25, 0.0)
            if t1 <= t0:
                t1 = t0 + 0.08
            filled += 1
        out_words.append({"w": w, "t0": round(t0, 3), "t1": round(max(t1, t0 + 0.02), 3),
                          "measured": j in anch})

    out_path = Path(a.out) if a.out else (work / "FvWDRz8WP5s.vocal-timed.json")
    out_path.write_bytes(json.dumps(
        {"slug": a.slug, "source": "whisper small on the separated dialogue stem",
         "asr_words": len(asr), "script_words": len(script),
         "anchored": len(anch), "interpolated": filled,
         "words": out_words}, ensure_ascii=False, indent=1).encode("utf-8", "surrogateescape"))
    print("  مُستقرأ بين قياسين: %d (%.0f%%)" % (filled, 100 * filled / max(len(script), 1)))
    print(f"\n  → {out_path}")

    diffs = [out_words[j]["t0"] - o0 for j, (w, o0, o1) in enumerate(script)
             if o0 is not None and j in anch]
    if diffs:
        ad = sorted(abs(x) for x in diffs)
        print("\n  === الفرق عن spoken-fixed ===")
        print("  كلمات مقارَنة %d" % len(diffs))
        print("  وسيط %+.2fث · انحراف %.2fث · p95 %.2fث · أقصى %.2fث" % (
            st.median(diffs), st.pstdev(diffs), ad[int(.95 * len(ad))], max(ad)))
        print("  |فرق| > 1ث: %.0f%%   > 2ث: %.0f%%" % (
            100 * sum(1 for x in ad if x > 1) / len(ad),
            100 * sum(1 for x in ad if x > 2) / len(ad)))


if __name__ == "__main__":
    main()
