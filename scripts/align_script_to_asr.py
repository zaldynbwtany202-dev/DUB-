#!/usr/bin/env python3
"""Match the script to what the film's ASR actually heard -- allowing mis-spellings.

`full-timed.json` was made by matching the script to the ASR word by word, and it
lost whole sentences: 208 script words carry no time of their own (packed into
0.08 s stubs) and in those places even their neighbours' times are wrong. The ASR
itself is fine -- it has every word of those sentences with a time (checked by
hand on Into the Wild 430-450 s) -- so the matching is what has to be fixed.

Here the two word lists are aligned by dynamic programming, not by exact
equality: an ASR word may be written differently from the script's ("الكوطوب"
against "الكتب", "الموسيكة" against "الموسيقى") and still be the same word.
Longest exact-match runs are used as anchors, and the words between two anchors
are aligned locally with a similarity cost, so nothing can run away: word order
is respected and times stay monotone.

    python scripts/align_script_to_asr.py \
        --script work/into-the-wild/full-timed.json \
        --asr work/into-the-wild/full-asr-vocal.json \
        --out work/into-the-wild/full-timed-v2.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")
AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ئ": "ي",
                        "ؤ": "و", "ة": "ه", "ٱ": "ا"})

GAP = 0.45          # كلفة ترك كلمة بلا مقابل
MATCH_MAX = 0.75    # فوق هذه الكلفة ليست كلمةً واحدة


def norm(word: str) -> str:
    w = word.strip().strip(".,!?؟،؛:\"'()[]{}«»-—…")
    return DIACRITICS.sub("", w).translate(AR_MAP)


def sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def align_block(scr: list[str], asr: list[str]) -> list[tuple[int, int]]:
    """DTW موضعي: يعيد أزواج (فهرس السكربت، فهرس التفريغ) للأزواج المتقابلة."""
    N, M = len(scr), len(asr)
    if not N or not M:
        return []
    INF = 1e9
    D = [[INF] * (M + 1) for _ in range(N + 1)]
    back = [[0] * (M + 1) for _ in range(N + 1)]
    D[0][0] = 0.0
    for i in range(N + 1):
        for j in range(M + 1):
            if i == 0 and j == 0:
                continue
            best, arg = INF, 0
            if i and D[i - 1][j] + GAP < best:            # كلمة سكربت بلا مقابل
                best, arg = D[i - 1][j] + GAP, 1
            if j and D[i][j - 1] + GAP < best:            # كلمة تفريغ بلا مقابل
                best, arg = D[i][j - 1] + GAP, 2
            if i and j:
                cm = 1.0 - sim(scr[i - 1], asr[j - 1])
                if cm > MATCH_MAX:
                    cm = MATCH_MAX + 0.35                 # كلمتان مختلفتان
                if D[i - 1][j - 1] + cm < best:
                    best, arg = D[i - 1][j - 1] + cm, 3
            D[i][j], back[i][j] = best, arg
    pairs: list[tuple[int, int]] = []
    i, j = N, M
    while i or j:
        a = back[i][j]
        if a == 3:
            if 1.0 - sim(scr[i - 1], asr[j - 1]) <= MATCH_MAX:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif a == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--asr", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    doc = json.loads(a.script.read_text(encoding="utf-8"))
    words = [w for w in doc["words"] if w.get("t0") is not None]
    adoc = json.loads(a.asr.read_text(encoding="utf-8"))
    if "words" in adoc:
        asr = adoc["words"]
    else:
        asr = [w for seg in adoc.get("segments", []) for w in seg["words"]]
    S = [norm(w["w"]) for w in words]
    A = [norm(w["w"]) for w in asr]

    sm = difflib.SequenceMatcher(a=A, b=S, autojunk=False)
    anchors = [(j, i, n) for i, j, n in sm.get_matching_blocks()]   # (سكربت، تفريغ، طول)
    blocks = sum(1 for _, _, n in anchors if n)
    matched: dict[int, int] = {}
    for sj, aj, n in anchors:
        for k in range(n):
            matched[sj + k] = aj + k
    # الكلمات بين المراسي: محاذاة موضعية بالتشابه
    for k in range(len(anchors) - 1):
        sj, aj, n = anchors[k]
        sj2, aj2, _ = anchors[k + 1]
        s_block = list(range(sj + n, sj2))
        a_block = list(range(aj + n, aj2))
        if not s_block or not a_block:
            continue
        for p, q in align_block([S[x] for x in s_block], [A[x] for x in a_block]):
            matched[s_block[p]] = a_block[q]

    idx = sorted(matched)
    times: list[tuple[float, float] | None] = [None] * len(words)
    for s_i, a_i in matched.items():
        times[s_i] = (float(asr[a_i]["t0"]), float(asr[a_i]["t1"]))
    filled = 0
    for k in range(len(words)):
        if times[k] is not None:
            continue
        filled += 1
        prev = max([x for x in idx if x < k], default=None)
        nxt = min([x for x in idx if x > k], default=None)
        if prev is not None and nxt is not None and times[nxt][0] > times[prev][1]:
            t0 = times[prev][1] + (times[nxt][0] - times[prev][1]) * (k - prev) / (nxt - prev)
        elif prev is not None:
            t0 = times[prev][1] + 0.3 * (k - prev)
        else:
            t0 = max(0.0, (times[nxt][0] if nxt is not None else 0.0) - 0.3 * (nxt - k if nxt else 0))
        times[k] = (max(0.0, t0), max(0.0, t0) + 0.12)

    out = [{"w": w["w"], "t0": round(t0, 3), "t1": round(t1, 3)} for w, (t0, t1) in zip(words, times)]
    a.out.write_text(json.dumps(
        {"slug": doc.get("slug"), "source": f"script aligned to {a.asr.name} by similarity",
         "script_words": len(words), "asr_words": len(asr),
         "matched": len([k for k in matched if times[k] is not None]), "interpolated": filled,
         "words": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    old_bad = [k for k, w in enumerate(words) if not w.get("measured")]
    now_ok = [k for k in old_bad if k in matched]
    shifts = [abs(times[k][0] - words[k]["t0"]) for k in range(len(words))
              if k in matched and words[k].get("measured")]
    print(f"  مطابقة {len(matched)} · استُوفي {filled} · بلوكات المراسي {blocks}")
    print(f"  الكلمات التي كانت بلا وقت: {len(old_bad)} → طُوبقت الآن: {len(now_ok)}")
    if shifts:
        shifts.sort()
        print(f"  إزاحة الكلمات المطابقة عن توقيتها القديم: وسيط {shifts[len(shifts)//2]:.2f} ث · "
              f"p90 {shifts[int(len(shifts)*0.9)]:.2f} ث")
    print(f"  → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
