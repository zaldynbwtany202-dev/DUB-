#!/usr/bin/env python3
"""Fix the auto-caption errors in the Sindbad word source before planning.

The transcript came from YouTube auto-captions, which mangle the proper nouns
worst: out of 161 mentions of the hero, 62 were some wrong shape -- سندبات,
سنديباد, سندباات, سندديباد, سندبادي. Read aloud those are different words, and
the narrator says the name constantly, so leaving them in is the exact class of
defect the user rejected before: a dub that is in sync but says the wrong word.

Rules here are only the ones confirmed against the clean caption track that the
watch page served for the first two fifths of the video, or forced by context
(الحوت is a whale, الجليد is ice, الحوريات are mermaids). Anything uncertain is
left alone -- a wrong guess is worse than an auto-caption spelling the ear may
not even notice.

Run:
  .venv/bin/python scripts/correct_sindbad_script.py [--in FILE] [--out FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN = ROOT / "work/كرتون-مغامرات-السندباد-البحري-القصة-الكاملة/FvWDRz8WP5s.unique.txt"

AR = "؀-ۿ"

# Longest first so the regex cannot bite off a short prefix of a long variant.
NAME_VARIANTS = [
    "سندباات",
    "سندديباد",
    "سنديبات",
    "سنديباد",
    "سندبات",
    "سندبادي",
    "سندباد",
    "سندبا",
]
NAME_RE = re.compile(
    r"(?<![" + AR + r"])"
    r"(ال)?(و|ف|ل|ب)?(" + "|".join(NAME_VARIANTS) + r")"
    r"(?![" + AR + r"])"
)

# Whole-word replacements, confirmed against the clean track or forced by sense.
WORD_FIXES: list[tuple[str, str]] = [
    # the bird: the narrator names her ياسمينة
    ("ياسمينه", "ياسمينة"),
    ("وياسمينه", "وياسمينة"),
    # a ship's captain, not كبطان
    ("كبطان", "قبطان"),
    ("الكبطان", "القبطان"),
    # mermaids
    ("حريه", "حورية"),
    ("الحريه", "الحورية"),
    ("حريات", "حوريات"),
    ("الحريات", "الحوريات"),
    ("الحريه البحر", "حورية البحر"),
    # vultures, not eagles
    ("النصور", "النسور"),
    ("النصر", "النسر"),
    # sorcerer
    ("مشعوز", "مشعوذ"),
    ("مشعو", "مشعوذ"),
    ("المشعوز", "المشعوذ"),
    # a whale, not a wall
    ("الحوط", "الحوت"),
    ("حوط", "حوت"),
    # the vase from China
    ("الفاظه", "الفازة"),
    ("فازه", "فازة"),
    # beheading sentence
    ("رقصه", "رؤوسهم"),
    ("رقبه", "رقبته"),
    # fight with the snake
    ("سراع", "صراع"),
    ("حيه", "حية"),
    # the roc can carry an elephant or a rhino horn
    ("قار", "قرن"),
    ("يشيل فيه", "يشيل فيل"),
    ("تفيال", "أفيال"),
    # pirates
    ("الكراسنه", "القراصنة"),
    ("كراسنه", "قراصنة"),
    # the riddle word, Egyptian
    ("فزدوره", "فزورة"),
    ("فزوره", "فزورة"),
    ("الفزوره", "الفزورة"),
    # the governor of Baghdad
    ("للولا", "للوالي"),
    ("الول ", "الوالي "),
    ("يتجو جوز", "يتجوز"),
    # idiom
    ("سوق ظنه", "سوء ظنه"),
    # horsemanship
    ("الفروصيه", "الفروسية"),
    # ice, not palm fronds
    ("الجريد", "الجليد"),
    ("كباديره", "كبادرة"),
    ("سرانديب", "سرنديب"),
    ("اخطبوه", "أخطبوط"),
    ("اخطبوط", "أخطبوط"),
    ("استحاله", "استحالة"),
    ("عليه بابا", "علي بابا"),
    # opening line, from the clean track
    ("جهائنا اتربوا", "جيل أبائنا تربوا"),
    ("اتراع", "اترعب"),
    ("فصيحه", "فصحى"),
    ("عمو علي", "عمه علي"),
    ("صلاه", "صلاة"),
    ("تلاوه", "تلاوة"),
    ("للقاع", "للقاعة"),
    ("الحاله", "الحلقة"),
    ("الحاله دي", "الحلقة دي"),
    ("سلطنه", "سلطة"),
    ("الصحبيه", "الصحبة"),
    ("التفاصه", "الطماعة"),
    ("بعجروا", "اتخنوا"),
    ("يجنسره", "يجيب له"),
    ("صده كبيره", "صيدة كبيرة"),
    ("هيفشخوك", "هيقتلوك"),
]


def apply(text: str) -> tuple[str, dict[str, int]]:
    """Return corrected text plus a tally of what changed."""
    tally: dict[str, int] = {}

    def name_sub(m: re.Match[str]) -> str:
        art, conj, core = m.group(1) or "", m.group(2) or "", m.group(3)
        if core != "سندباد":
            tally[f"{core}→سندباد"] = tally.get(f"{core}→سندباد", 0) + 1
        return f"{art}{conj}سندباد"

    text = NAME_RE.sub(name_sub, text)

    for wrong, right in WORD_FIXES:
        if wrong == right:
            continue
        pat = re.compile(r"(?<![" + AR + r"])" + re.escape(wrong) + r"(?![" + AR + r"])")
        text, n = pat.subn(right, text)
        if n:
            tally[f"{wrong}→{right}"] = tally.get(f"{wrong}→{right}", 0) + n
    return text, tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.src.is_file():
        print(f"لا يوجد: {a.src}", file=sys.stderr)
        return 2

    src = a.src.read_text(encoding="utf-8")
    fixed, tally = apply(src)

    total = sum(tally.values())
    print(f"المدخل : {a.src}")
    print(f"  أحرف قبل : {len(src):,} · كلمات قبل : {len(src.split()):,}")
    print(f"  أحرف بعد : {len(fixed):,} · كلمات بعد : {len(fixed.split()):,}")
    print(f"  تصحيحات  : {total}")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"    {k:28} ×{v}")

    if a.out and not a.dry_run:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(fixed, encoding="utf-8")
        print(f"\n  كُتب: {a.out}")
    elif a.dry_run:
        print("\n  (تجربة بلا كتابة)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
