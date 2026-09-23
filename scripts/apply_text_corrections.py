#!/usr/bin/env python3
"""Apply reviewed whole-word corrections to a script, and write the diff.

The words of a dub come from the source text, so a caption error becomes a
spoken error: the voice reads exactly what the script says. When the source is
YouTube's auto-caption track the errors are predictable -- a dropped letter, a
name spelled two ways, a word that is not a word at all -- and the fix belongs
in the text, not in the audio.

Two rules carried over from the jobs that did this before:

Whole words only. «انا» (the pronoun) must never be touched inside «الانسان»,
and a substring replace would do exactly that. Every replacement here is bounded
by non-letters on both sides.

Every change is documented with its context, so a reviewer can disagree with a
specific line instead of the whole file. The diff is written next to the output
rather than printed and lost.

    python scripts/apply_text_corrections.py work/into-the-wild/text/script-full.txt \
        work/into-the-wild/script-corrections.json \
        work/into-the-wild/text/script-corrected.txt \
        --diff work/into-the-wild/script-corrections-diff.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

AR = r"\u0621-\u064A\u0660-\u0669\u066E-\u06D3"


def _boundary(word: str) -> str:
    return rf"(?<![{AR}]){re.escape(word)}(?![{AR}])"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", type=Path)
    ap.add_argument("corrections", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--diff", type=Path, default=None)
    a = ap.parse_args()

    text = a.text.read_text(encoding="utf-8")
    spec = json.loads(a.corrections.read_text(encoding="utf-8"))
    rules = spec["corrections"]

    rows, applied, unused = [], 0, []
    for rule in rules:
        src, dst = rule["from"], rule["to"]
        pat = re.compile(_boundary(src))
        hits = list(pat.finditer(text))
        if not hits:
            unused.append(src)
            continue
        for m in hits:
            s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
            rows.append((src, dst, text[s:e].replace("\n", " ")))
        text = pat.sub(dst, text)
        applied += len(hits)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text, encoding="utf-8")
    words = len(re.split(r"\s+", text.strip()))
    print(f"  طُبِّق {applied} تصحيحًا من {len(rules)} قاعدة → {a.out}")
    print(f"  النص الآن: {words} كلمة · {len(text)} حرف")
    if unused:
        print(f"  قواعد لم تُطابق شيئًا ({len(unused)}): {'، '.join(unused)}")
        print("  هذه إما كلمة صحيحة أصلًا أو صيغتها في النص مختلفة — تُراجَع")

    if a.diff:
        lines = ["# تصحيحات نص Into the Wild", "",
                 f"طُبِّق {applied} تصحيحًا. كل سطر يقرأ سياقه ويُقبل أو يُرفض منفردًا.", ""]
        for src, dst, ctx in rows:
            lines += [f"- **{src}** ← **{dst}**", f"  > …{ctx}…", ""]
        a.diff.write_text("\n".join(lines), encoding="utf-8")
        print(f"  → {a.diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
