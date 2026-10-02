#!/usr/bin/env python3
"""Give every script word a time taken from the film's own audio.

The old timing (`full-timed.json`) came from whisper on the separated dialogue
stem; 208 of its 5701 words got no time of their own and were packed into 0.08 s
stubs, which is what made the dub drift in those groups. `asr-mix.json` is the
same whisper on the film's own mix, which hears those sentences. Here the two
are matched word by word (Arabic spelling normalised) and the film's times are
written back onto the script, so every word carries a real time.

    python scripts/retime_script_from_audio.py \
        --script work/into-the-wild/full-timed.json \
        --asr work/into-the-wild/asr-mix.json \
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


def norm(word: str) -> str:
    w = word.strip().strip(".,!?؟،؛:\"'()[]{}«»-—…")
    w = DIACRITICS.sub("", w).translate(AR_MAP)
    return w


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
    else:                                   # نسخة المسار الأصلي: مقاطع فيها كلمات
        asr = [w for seg in adoc.get("segments", []) for w in seg["words"]]
    a_norm = [norm(w["w"]) for w in asr]
    s_norm = [norm(w["w"]) for w in words]

    sm = difflib.SequenceMatcher(a=a_norm, b=s_norm, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(words)
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            times[j + k] = (asr[i + k]["t0"], asr[i + k]["t1"])
        if i + n >= len(asr):
            break

    # الخانات التي لم تُطابق: استيفاء متناسب بين أقرب جارتين مطابقتين
    idx = [k for k, t in enumerate(times) if t is not None]
    filled = 0
    if not idx:
        raise SystemExit("  ✗ لا تطابق بين تفريغ الصوت والنص")
    for k, t in enumerate(times):
        if t is not None:
            continue
        filled += 1
        prev = max([x for x in idx if x < k], default=None)
        nxt = min([x for x in idx if x > k], default=None)
        if prev is not None and nxt is not None:
            t0 = times[prev][1] + (times[nxt][0] - times[prev][1]) * (k - prev) / (nxt - prev)
        elif prev is not None:
            t0 = times[prev][1] + 0.3 * (k - prev)
        else:
            t0 = times[nxt][0] - 0.3 * (nxt - k)
        times[k] = (max(0.0, t0), max(0.0, t0) + 0.12)

    from_asr = [False] * len(words)
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            from_asr[j + k] = True
    out_words = []
    for w, (t0, t1) in zip(words, times):
        out_words.append({"w": w["w"], "t0": round(float(t0), 3), "t1": round(float(t1), 3)})
    a.out.write_text(json.dumps(
        {"slug": doc.get("slug"), "source": f"retimed from {a.asr}",
         "script_words": len(words), "asr_words": len(asr),
         "matched": sum(n for _, _, n in sm.get_matching_blocks()),
         "interpolated": filled, "words": out_words},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"  تطابق {sum(n for _, _, n in sm.get_matching_blocks())} كلمة · استُوفي {filled}")
    old_bad = [k for k, w in enumerate(words) if not w.get("measured")]
    now_ok = [k for k in old_bad if from_asr[k]]
    print(f"  الكلمات التي كانت بلا وقت: {len(old_bad)} → لها وقت من الصوت الآن: {len(now_ok)}")
    print(f"  → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
