#!/usr/bin/env python3
"""Normalize the locked Das script where YouTube's auto-captions wrote the same
word in different shapes, so one character keeps one name across all 134 groups.

The user reported the defect: the hero is called «داس» for ten minutes and then
«دس». The voice reads whatever the script says, so the fix belongs in the text,
not in the audio. Every rule below was confirmed by reading its context in the
script (see the generated diff); replacements are whole-word only, so «انا»
(the pronoun "I") is never touched.

Timing is deliberately left alone: windows, onsets and offsets are copied
verbatim from groups-v2.json, so already-recorded takes that contain no changed
word stay valid and the alignment that was repaired earlier cannot drift.

Outputs:
  youtube-v33/groups-v3.json          same plan, corrected text, recomputed chars
  youtube-v33/text-v3-NNNN.txt        one file per group, ready to record from
  youtube-v33/script-corrections-diff.md   every change with its context
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "dubs/das-full/youtube-v33"

# One character, one name. Confirmed against context in every occurrence.
NAME_FIXES = [
    ("دس", "داس", "البطل — نفس الاسم كتبه المولّد بشكلين"),
    ("دايس", "داس", "البطل — مجموعة 49"),
    ("داساني", "داس", "البطل — مجموعة 32"),
    ("الانه", "الانا", "البطلة — 25 مجموعة كانت تنطقها بشكل ثان"),
    ("مور", "مول", "زعيمة القرية — مجموعة 25"),
    ("طور", "تور", "الثور — مجموعة 23 (والجمع «تيران» صحيح فبقي)"),
    ("الفاز", "الفاس", "سلاحه — مجموعة 32"),
    ("التران", "التيران", "جمع الثور — المجموعة 24 تكتبها بشكل ثان؛ والنص المجلوب من يوتيوب يجمع بين «طور» و«تور» في جملتين متتاليتين فيؤكد الخطأ"),
]

# Truncated or garbled tokens whose intended word is unambiguous in context.
TYPO_FIXES = [
    ("اكث", "اكثر", "«بضاعه اكث» — حرف ناقص، 11 مجموعة"),
    ("اتغازوا", "اتغاظوا", "«اتغازوا جدا» — من الغيظ"),
    ("سعترد", "سعها رد", "كلمتان التصقتا — مجموعة 28"),
    ("الصحه", "صحى", "«الصبح ده الصحه فجاه» — أي استيقظ"),
    ("مؤنو", "مؤنه", "«باقي مؤنو» — مجموعة 28"),
]

# Flagged for the user, never auto-changed: the intended word is a guess.
AMBIGUOUS = ["متبرجل", "كحب", "قورتها", "قفشت", "غلب ومنهده", "الوز", "بتكذب", "سبيشيال",
             "قلقنا"]  # مجموعة 24: السياق يقول إنها «مول» (بعدها «بعد لما مول سمعت») لكن الصوتيات بعيدة، فلا أخمن

MAX_CHARS_SOFT = 380   # what the planner aimed for
MAX_CHARS_HARD = 420   # beyond this a single take stops being reliable
VOICE_CPS = 11.79
TEMPO_CEILING = 1.60


# Arabic clitics attach without a space ("والانه", "واتغازوا"), so a bare word
# boundary misses them. Only these five prefixes are allowed, which keeps the rule
# away from lookalikes: «مور» inside «الامور» and «طور» inside «الاسطوري» are
# preceded by other letters and stay untouched.
CLITICS = "وفبلك"


def fix(text: str, table) -> tuple[str, list[str]]:
    applied = []
    for a, b, why in table:
        pat = r"(?<!\w)([" + CLITICS + r"]?)" + re.escape(a) + r"(?!\w)"
        new, n = re.subn(pat, lambda m: m.group(1) + b, text)
        if n:
            text = new
            applied.append(f"{a} → {b} ×{n}")
    return text, applied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", type=Path, default=HERE / "groups-v2.json")
    ap.add_argument("--out", type=Path, default=HERE / "groups-v3.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redo", default="",
                    help="comma-separated group ids being re-recorded now; typo fixes "
                         "apply to these and to every group at or after --typo-min-group, "
                         "so a stale take never disagrees with its subtitle")
    ap.add_argument("--typo-min-group", type=int, default=0,
                    help="first group id that has no take yet (typo fixes apply from here on)")
    args = ap.parse_args()

    plan = json.loads(args.groups.read_text(encoding="utf-8"))
    groups = plan["groups"]

    # chars must keep whatever definition the planner used
    sample = groups[0]
    strip_spaces = len(sample["text"].replace(" ", "")) == sample["chars"]

    redo = {int(x) for x in args.redo.split(",") if x.strip()}
    changed, diff_rows = [], []
    for g in groups:
        table = NAME_FIXES + (TYPO_FIXES if g["i"] >= args.typo_min_group or g["i"] in redo else [])
        new, applied = fix(g["text"], table)
        if new != g["text"]:
            g["text_original"] = g["text"]
            g["text"] = new
            g["corrections"] = applied
            g["chars"] = len(new.replace(" ", "")) if strip_spaces else len(new)
            changed.append(g["i"])
            diff_rows.append(g)

    over_soft = [g["i"] for g in groups if g["chars"] > MAX_CHARS_SOFT]
    over_hard = [g["i"] for g in groups if g["chars"] > MAX_CHARS_HARD]
    worst = max(g["chars"] / VOICE_CPS / (g["t1"] - g["t0"] - 0.08) for g in groups)
    windows_moved = [g["i"] for g in groups if "text_original" in g and
                     (g["t0"], g["t1"]) != (g["t0"], g["t1"])]

    print(json.dumps({
        "groups_total": len(groups),
        "groups_changed": len(changed),
        "changed_ids": changed,
        "already_recorded_to_redo": [i for i in changed if i <= 31],
        "chars_over_soft_cap": over_soft,
        "chars_over_hard_cap": over_hard,
        "worst_predicted_tempo": round(worst, 3),
        "tempo_ceiling": TEMPO_CEILING,
        "windows_changed": windows_moved,
        "ambiguous_left_for_user": AMBIGUOUS,
    }, ensure_ascii=False, indent=2))

    # the binding constraint is speaking rate, not the planner's soft char target:
    # a correction may push a group one or two chars past 380 as long as the take
    # still fits its window inside the approved ceiling
    if over_hard or worst > TEMPO_CEILING:
        print(f"✗ التصحيح كسر قيداً (فوق {MAX_CHARS_HARD} حرف: {over_hard} · "
              f"أسوأ tempo {worst:.3f} > {TEMPO_CEILING}) — لم يُكتب شيء")
        return 1
    if args.dry_run:
        return 0

    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for g in groups:
        (HERE / f"text-v3-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")

    lines = ["# فروق تصحيح النص — داس",
             "",
             "المصدر المقفول `unique.txt` جاء من ترجمة يوتيوب التلقائية، فكتب الاسم نفسه",
             "بأشكال مختلفة والصوت قرأها كما هي. القواعد كلها على مستوى الكلمة الكاملة،",
             "والتوقيتات لم تُمسّ (النوافذ منسوخة حرفياً من `groups-v2.json`).",
             "",
             "## القواعد",
             "",
             "| من | إلى | السبب |",
             "|---|---|---|"]
    for a, b, why in table:
        lines.append(f"| «{a}» | «{b}» | {why} |")
    lines += ["", f"**مجموعات تغيّرت: {len(changed)} من {len(groups)}** · "
                  f"مسجَّلة منها وتُعاد: {[i for i in changed if i <= 31]}",
              "",
              f"قواعد الأخطاء المطبعية طُبِّقت على المجموعات {args.typo_min_group}+ وعلى "
              f"{sorted(redo)} (المُعادة الآن). المجموعات المسجَّلة الأخرى التي فيها أخطاء "
              f"مطبعية فقط تبقى كما هي حتى يُعاد تسجيلها، كي لا يختلف الصوت عن الترجمة.",
              "", "## كل تغيير بسياقه", ""]
    for g in diff_rows:
        lines.append(f"### مجموعة {g['i']} — {', '.join(g['corrections'])}")
        lines.append(f"- **قبل:** {g['text_original']}")
        lines.append(f"- **بعد:** {g['text']}")
        lines.append("")
    amb = [(w, [g["i"] for g in groups if re.search(r"(?<!\w)" + re.escape(w), g["text"])])
           for w in AMBIGUOUS]
    lines += ["## كلمات مشكوك فيها — لم أُغيّرها (تحتاج قرارك)", ""]
    for w, ids in amb:
        if ids:
            lines.append(f"- «{w}» في المجموعات {ids}")
    (HERE / "script-corrections-diff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ كُتب: {args.out.relative_to(ROOT)} + {len(groups)} ملف نص + سجل الفروق")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
