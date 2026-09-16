#!/usr/bin/env python3
"""One character, one name — for doctor-lecture (kOrz0WAb2P8).

YouTube's auto-captions wrote the same words in several shapes, and the voice
reads whatever the script says, so the fix belongs in the text (the user reported
exactly this defect on das-full). Timing is untouched: t0/t1/window are copied
verbatim from groups.json, so the repaired alignment cannot drift.

Every rule was checked by counting the variants and reading their contexts:

  بختي 44 / بخت 6 / بختيه 3     the doctor      -> بختي
  نيسه 7 / نيسا 3               his daughter    -> نيسه
  فيدي 5 / فدي 6 / فيديه 2      the student     -> فيدي
  جاينال 2 / جاينا 2            union president -> جاينال

Phrase rules are literal (a bare word rule would be wrong: «صور» is legitimate in
«صور الجنازه», only «صور الجنينه» means the garden fence «سور»).

Ambiguous tokens are REPORTED, never auto-changed.

Outputs: groups-v2.json, text-v2-NNNN.txt, corrections-diff.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from apply_script_corrections import fix as clitic_fix  # noqa: E402  shared matcher

# word-level, clitic-aware («وبختي», «فبخت»); pronoun suffixes handled separately
NAME_FIXES = [
    ("بختيه", "بختي", "الدكتور — 3 مواضع («بيلمعهم بختيه»، «بتلمح بختيه واقف»، «بيهرب من بختيه»)"),
    ("بخت", "بختي", "الدكتور — 6 مواضع كتبها المولّد ناقصة؛ تحققتُ أن كلها اسم Doctor وليس «بخت» بمعنى الحظ"),
    ("نيسا", "نيسه", "بنته — 3 مواضع مقابل 7 بالشكل المعتمد"),
    ("فدي", "فيدي", "الطالب — 6 مواضع مقابل 5؛ الشكل بالياء أوضح نطقاً"),
    ("فيديه", "فيدي", "الطالب — «بيلاقوا فيديه طاير»، «بيبان على فيديه»"),
    ("جاينا", "جاينال", "رئيس اتحاد الطلبة — «بيلمح جاينا للدبدوب»، «بيرد جاينا لانه»"),
]
# بيلمعهم / بيلمعها: the shared matcher allows prefixes only, so add suffixed forms
SUFFIXED_FIXES = [
    (r"بيلمع(هم|ها|ه|كم|نا)?", "بيلمح", "«بيلمع» في كل مواضعه هنا بمعنى يلمح/ينتبه، ولم يرد بمعنى يلمع"),
]
TYPO_FIXES = [
    ("اكث", "اكثر", "حرف ناقص — «مرعبه اكت من المعتاد»، «بتسوق اكث»"),
]
# literal phrases — word-level rules would damage legitimate lookalikes
PHRASE_FIXES = [
    ("بخت اختي", "بختي", "«صوت بخت اختي في دماغها» — الاسم انشطر كلمتين؛ يجب أن تُطبق قبل قاعدة بخت→بختي"),
    ("صور الجنينه", "سور الجنينه", "«الحديد بتاع صور الجنينه» — الحديد هو السور؛ و«صور» تبقى صحيحة في «صور الجنازه»"),
    ("5سه ون:30", "خمسه ونص", "ميعاد المحاضرة وصل مشوهاً من المولّد"),
    ("يتح يتحرك", "يتحرك", "كلمة مكررة مشوهة — «من غير ما يتح يتحرك»"),
    ("بيعرف عرف ينطق", "بيعرف ينطق", "كلمة مكررة مشوهة"),
    ("عايش ش فيه", "عايش فيه", "حرف زائد — «البيت اللي بختي كان عايش ش فيه»"),
    ("نيسه بنت الصغيره", "نيسه بنته الصغيره", "الهاء ناقصة؛ السياق: أبوها الدكتور بختي"),
]
AMBIGUOUS = [
    ("بتروح ملكان", "لعلها «بتروح ميل مكان» أو «بتروح مل كان» — لا أخمن"),
    ("بتحكي مال ان روح", "«مال» زائدة على الأرجح — أول الجزء الثالث"),
    ("شويه طرود للسماق فولا", "مشوهة تماماً — «فبتلمح شويه طرود لسه مقفوله» عند المولّد الصوتي"),
    ("بيعمل عمل كل اللي", "تكرار — «بيرد جاينا لانه بيعمل عمل كل اللي يقدر عليه»"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--groups", type=Path, default=HERE / "groups.json")
    ap.add_argument("--out", type=Path, default=HERE / "groups-v2.json")
    ap.add_argument("--voice-id", default="voice-01",
                    help="the voice the user selected for this project")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    plan = json.loads(a.groups.read_text(encoding="utf-8"))
    groups = plan["groups"]
    diff: list[str] = []
    changed = 0

    for g in groups:
        before = g["text"]
        text = before
        # phrases first: «بخت اختي» must become «بختي» before the word rule sees «بخت»
        for lit, rep, why in PHRASE_FIXES:
            if lit in text:
                n = text.count(lit)
                text = text.replace(lit, rep)
                diff.append(f"مجموعة {g['i']}: «{lit}» → «{rep}» ×{n}  ({why})")
        for pat, rep, why in SUFFIXED_FIXES:
            text, n = re.subn(pat, lambda m, r=rep: r + (m.group(1) or ""), text)
            if n:
                diff.append(f"مجموعة {g['i']}: {pat} → {rep} ×{n}  ({why})")
        text, applied = clitic_fix(text, NAME_FIXES + TYPO_FIXES)
        for note in applied:
            diff.append(f"مجموعة {g['i']}: {note}")
        if text != before:
            changed += 1
            g["text"] = text
            g["chars"] = len(text.replace(" ", ""))

    # recompute the tempo forecast from the corrected char counts
    for g in groups:
        room = g["window"] - 0.08
        g["predicted_tempo"] = round((g["chars"] / plan["chars_per_s"]) / room, 3) if room > 0 else None

    over = [g["i"] for g in groups if (g["predicted_tempo"] or 0) > plan["tempo_ceiling"]]
    hard = [g["i"] for g in groups if g["chars"] > 420]
    plan["groups"] = groups
    plan["voice_id"] = a.voice_id
    plan["words_from"] = ("YouTube watch page transcript kOrz0WAb2P8 (unique.txt), "
                          "normalized by dubs/doctor-lecture/apply_corrections.py")
    plan["corrections"] = {"groups_changed": changed, "rules": len(diff),
                           "over_tempo_ceiling": over, "over_hard_chars": hard}

    flags = []
    joined = " ".join(g["text"] for g in groups)
    for tok, why in AMBIGUOUS:
        if tok in joined:
            flags.append(f"- «{tok}» — {why}")

    if not a.dry_run:
        a.out.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for g in groups:
            (HERE / f"text-v2-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")
        (HERE / "corrections-diff.md").write_text(
            "# تصحيحات نص doctor-lecture (kOrz0WAb2P8)\n\n"
            f"الكلمات من نص يوتيوب؛ الأزمنة من الصوت. تغيّرت {changed} مجموعة من {len(groups)}.\n"
            f"الصوت: **{a.voice_id}**. التوقيت لم يُمسّ (t0/t1/window منقولة حرفياً).\n\n"
            "## التغييرات المطبقة\n\n" + "\n".join(f"- {d}" for d in diff) +
            "\n\n## مرفوعة للمستخدم — لم تُغيَّر تلقائياً\n\n" + ("\n".join(flags) or "- لا شيء") +
            f"\n\n## الجدوى بعد التصحيح\n\n- أعلى سرعة متوقعة: "
            f"{max(g['predicted_tempo'] or 0 for g in groups):.3f} (السقف {plan['tempo_ceiling']})\n"
            f"- مجموعات فوق السقف: {over or 'لا شيء'}\n- مجموعات فوق 420 حرفاً: {hard or 'لا شيء'}\n",
            encoding="utf-8")

    print(json.dumps({"groups": len(groups), "groups_changed": changed, "rules_applied": len(diff),
                      "voice_id": a.voice_id, "over_tempo_ceiling": over, "over_hard_chars": hard,
                      "max_tempo": max(g["predicted_tempo"] or 0 for g in groups),
                      "flagged_for_user": len(flags), "dry_run": a.dry_run},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
