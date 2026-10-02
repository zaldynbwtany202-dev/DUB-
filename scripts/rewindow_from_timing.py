#!/usr/bin/env python3
"""Cut each group's window where its own words really are, not where a broken timing said.

A group's window is the slot of film its take is laid into, so a wrong timing
puts the window in the wrong place: the group then speaks over words that belong
to its neighbour while its own words sit half outside. That is what turned a
timing defect into 3-6 s of drift in five groups of Into the Wild.

Windows are rebuilt from the corrected word times (`full-timed-*.json`): each
group keeps the text it already has (so no take is re-recorded) and gets the slot
between the first and the last word of that text. Neighbours stay glued to each
other, so the dub still runs without holes.

    python scripts/rewindow_from_timing.py work/into-the-wild \
        --timing work/into-the-wild/full-timed-v2.json \
        --out work/into-the-wild/groups.json --backup work/into-the-wild/groups.before-rewindow.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")
AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ئ": "ي",
                        "ؤ": "و", "ة": "ه", "ٱ": "ا"})


def norm(word: str) -> str:
    w = word.strip().strip(".,!?؟،؛:\"'()[]{}«»-—…")
    return DIACRITICS.sub("", w).translate(AR_MAP)


def words_of(text: str) -> list[str]:
    return [w for w in text.replace('،', ' ').replace('؛', ' ').split() if w]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--timing", type=Path, required=True)
    ap.add_argument("--groups", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--backup", type=Path, default=None)
    ap.add_argument("--lookahead", type=int, default=120)
    ap.add_argument("--takes", type=Path, default=None, help="مجلد الأخذات: لقياس طول كل أخذة")
    ap.add_argument("--min-tempo", type=float, default=0.90)
    ap.add_argument("--max-tempo", type=float, default=1.78)
    ap.add_argument("--max-shift", type=float, default=1.0,
                    help="أقصى إزاحة لحدّ نافذة عن موضع كلماته (ثانية)")
    a = ap.parse_args()

    gp = a.groups or (a.work / "groups.json")
    doc = json.loads(gp.read_text(encoding="utf-8"))
    groups = doc["groups"]
    src = [w for w in json.loads(a.timing.read_text(encoding="utf-8"))["words"]
           if w.get("t0") is not None]
    norm_src = [norm(w["w"]) for w in src]

    if a.backup:
        a.backup.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    ptr = 0
    spans: list[tuple[int, int]] = []
    for g in groups:
        tw = [norm(x) for x in words_of(g["text"])]
        window = norm_src[ptr:ptr + len(tw) + a.lookahead]
        sm = difflib.SequenceMatcher(a=window, b=tw, autojunk=False)
        blocks = [(i, n) for i, j, n in sm.get_matching_blocks() if n]
        if not blocks:
            spans.append((ptr, ptr + 1))
            continue
        first = min(i for i, _ in blocks)
        last = max(i + n for i, n in blocks)
        start = max(ptr, ptr + first)
        end = max(start + 1, ptr + last)
        spans.append((start, end))
        ptr = end

    # بداية كل مجموعة = أول كلمة من نصّها؛ ونهايتها = بداية التالية (اتصال بلا فراغات)
    for k, g in enumerate(groups):
        s_i, e_i = spans[k]
        s_i = min(s_i, len(src) - 1)
        g["t0_prev"] = float(g["t0"])
        g["_span"] = [s_i, e_i]
        g["t0"] = round(float(src[s_i]["t0"]), 3)
    for k, g in enumerate(groups):
        if k + 1 < len(groups):
            g["t1"] = groups[k + 1]["t0"]
        else:
            e_i = min(spans[k][1] - 1, len(src) - 1)
            g["t1"] = round(float(src[e_i]["t1"]), 3)
        g["window"] = round(g["t1"] - g["t0"], 3)

    # ضبط الحدود: كل نص يجب أن يتوزّع داخل نافذته (بين سقف السرعة وأدنى تبطيء).
    # الحدود سلسلة واحدة متصلة، فالحل إسقاط متناوب (تقدّم ثم تراجع) عليها.
    import soundfile as sf
    takes = a.takes or (a.work / "takes-voice25")
    nat: dict[int, float] = {}
    for g in groups:
        i = int(g["i"])
        for cand in (takes / f"g{i:03d}.mp3", takes / f"g{i:03d}.wav"):
            if cand.exists():
                info = sf.info(str(cand))
                nat[i] = float(info.frames) / float(info.samplerate)
                break
    w = [float(g["t0"]) for g in groups] + [float(groups[-1]["t1"])]
    lo = [(nat.get(int(g["i"]), 0.0) / a.max_tempo) if int(g["i"]) in nat else 0.0 for g in groups]
    hi = [(nat.get(int(g["i"]), 0.0) / a.min_tempo) if int(g["i"]) in nat else 1e9 for g in groups]
    b = list(w)
    for _ in range(300):
        for k in range(len(groups)):                      # تقدّم: امنع نافذة أضيق من اللازم
            if hi[k] < 1e8:
                b[k + 1] = min(max(b[k + 1], b[k] + lo[k]), b[k] + hi[k])
                b[k + 1] = min(max(b[k + 1], w[k + 1] - a.max_shift), w[k + 1] + a.max_shift)
        for k in range(len(groups) - 1, -1, -1):          # تراجع: امنع نافذة أوسع من اللازم
            if hi[k] < 1e8:
                b[k] = max(min(b[k], b[k + 1] - lo[k]), b[k + 1] - hi[k])
                b[k] = min(max(b[k], w[k] - a.max_shift), w[k] + a.max_shift)
    moved = []
    for k, g in enumerate(groups):
        d0 = float(g["t0"]); d1 = float(g["t1"])
        g["t0"] = round(b[k], 3)
        g["t1"] = round(b[k + 1], 3)
        g["window"] = round(g["t1"] - g["t0"], 3)
        if abs(g["t0"] - d0) > 0.05 or abs(g["t1"] - d1) > 0.05:
            moved.append((int(g["i"]), round(float(g["t0"]) - d0, 2), round(float(g["t1"]) - d1, 2)))

    doc["rewindowed_from"] = str(a.timing)
    doc["rewindow_note"] = ("كل مجموعة تأخذ الشريحة بين أول كلمة وآخر كلمة من نصّها بالتوقيت "
                            "المصحَّح؛ النصوص والأخذات لم تُمسّ؛ النوافذ متصلة (بلا فراغ).")
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"  النافذة قبل → بعد (ثانية) لكل مجموعة تغيّرت أكثر من 0.25 ث:")
    moved = 0
    for g in groups:
        d0 = float(g["t0"]) - float(g.get("t0_prev", g["t0"]))
        if abs(d0) > 0.25:
            moved += 1
            print(f"    g{int(g['i']):03d}  {float(g['t0_prev']):8.2f} → {float(g['t0']):8.2f}  "
                  f"({d0:+.2f} ث)  مدى {g['window']:.2f} ث")
    print(f"  تحرّكت {moved} مجموعة · كُتب → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
