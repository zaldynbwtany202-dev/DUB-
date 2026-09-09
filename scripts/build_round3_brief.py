#!/usr/bin/env python3
"""Turn the measurements into a per-segment rewrite brief for the next recording pass.

Nothing here records audio or edits the delivered file. It joins what already exists -
the affect map (how fast, how loud, how wide and how cut the picture is in each of the
original's own speech units) and the phrase budget those units imply - with the wording
that is currently published, and writes instructions a writer can act on: this much time,
this many words, this register, this delivery cue, and the size of the gap in words.

Two things are deliberate:

* the word budget scales with `--voice-rate`, the words/second the chosen narrator was
  actually measured at (`scripts/compare_voice_candidates.py` prints it), because the
  alternative to fitting the words is stretching the voice - which this project forbids;
* `current_text` is quoted from the approved script so nobody re-invents the meaning, and
  the source text is the ASR of the original, which is known to be imperfect and is a
  meaning reference only, never a transcript to translate.

    python scripts/build_round3_brief.py \
        --affect-map dubs/hajj-dream-2108415/timing-repair/affect-map.json \
        --script dubs/hajj-dream-2108415/timing-repair/script.json \
        --voice-rate 2.545 --voice-label voice-07 \
        --markdown dubs/hajj-dream-2108415/timing-repair/round3-brief.md \
        --output dubs/hajj-dream-2108415/timing-repair/round3-brief.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE_RATE = 2.507  # density the original narrator sustains, from phrase-budget.json
ROOT = Path(__file__).resolve().parents[1]


def writing_rules() -> dict[str, dict]:
    """The register rules from the shipped affect module, if the environment can import it."""
    import sys

    sys.path.insert(0, str(ROOT))
    try:
        from youtube_auto_dub.affect import WRITING_RULES  # local import: needs numpy
        return dict(WRITING_RULES)
    except Exception:  # numpy missing in a fresh sandbox must not hide the numbers
        return {}


def build(affect_map: Path, script: Path, voice_rate: float) -> tuple[dict, list[dict]]:
    doc = json.loads(affect_map.read_text(encoding="utf-8"))
    published = {int(t["index"]): t for t in json.loads(script.read_text(encoding="utf-8"))["takes"]}
    rules = writing_rules()
    scale = voice_rate / SOURCE_RATE
    rows: list[dict] = []
    parts: list[dict] = []
    for part in doc["parts"]:
        index = int(part["part"])
        take = published.get(index, {})
        text = (take.get("text") or "").strip()
        allowed_part = 0
        for row in part["rows"]:
            needed = int(row.get("needed_dub_words") or 0)
            allowed = max(3, int(round(needed * scale)))
            allowed_part += allowed
            register = row.get("register") or "neutral"
            rule = rules.get(register, {})
            rows.append({
                "part": index,
                "segment": int(row["segment"]),
                "start": round(float(row["start"]), 2),
                "end": round(float(row["end"]), 2),
                "seconds": round(float(row["seconds"]), 2),
                "register": register,
                "why_register": row.get("why"),
                "words_allowed": allowed,
                "source_words": row.get("words"),
                "source_rate": row.get("rate"),
                "source_spread_st": row.get("f0_spread"),
                "source_level_db": row.get("level_db"),
                "motion": row.get("motion"),
                "cuts_per_min": row.get("cut_rate_per_min"),
                "pace_target": rule.get("rate", 1.0),
                "punch": rule.get("punch", 0),
                "sentence_shape": rule.get("sentences", "medium"),
                "cue": rule.get("note", ""),
                "source_text": (row.get("source_text") or "").strip(),
            })
        current = len(text.split())
        parts.append({"part": index, "window": [round(float(part["window_start"]), 2),
                                                round(float(part["window_end"]), 2)],
                      "segments": len(part["rows"]), "words_allowed": allowed_part,
                      "published_words": current, "gap_words": allowed_part - current,
                      "published_opening": text[:110]})
    summary = {"segments": len(rows),
               "seconds_covered": round(sum(row["seconds"] for row in rows), 2),
               "words_allowed_total": sum(p["words_allowed"] for p in parts),
               "published_words_total": sum(p["published_words"] for p in parts),
               "registers": doc.get("registers_total", {}),
               "scale_vs_source_density": round(scale, 4)}
    return summary, {"rows": rows, "parts": parts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--affect-map", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True,
                        help="the approved take script (published wording, per window)")
    parser.add_argument("--voice-rate", type=float, default=SOURCE_RATE)
    parser.add_argument("--voice-label", default="(لم يُختر الصوت بعد)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    summary, body = build(args.affect_map, args.script, args.voice_rate)
    rows, parts = body["rows"], body["parts"]
    doc = {"rule": ("a segment is rewritten, never stretched. gap_words > 0 in a window means the "
                    "dub is thinner than the original there and silence is being spent; gap_words < 0 "
                    "means the wording must lose words, not the voice lose speed"),
           "voice_label": args.voice_label, "voice_words_per_sec": args.voice_rate,
           "summary": summary, "parts": parts, "rows": rows}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        lines = [f"# موجز إعادة الكتابة — {args.voice_label} ({args.voice_rate} كلمة/ث)",
                 "",
                 f"{summary['segments']} مقطعًا فوق {summary['seconds_covered']} ثانية من كلام "
                 f"الأصل. المطلوب {summary['words_allowed_total']} كلمة؛ المنشور حاليًا "
                 f"{summary['published_words_total']} كلمة — يعني "
                 f"{summary['words_allowed_total'] - summary['published_words_total']} كلمة ناقصة "
                 "لا تُمَدَّد في الصوت، بل تُكتب.",
                 "",
                 "| نافذة | ثوانٍ | مسموح | منشور | فرق |", "|---|---|---|---|---|"]
        for p in parts:
            span = round(p["window"][1] - p["window"][0], 1)
            gap = p["gap_words"]
            lines.append(f"| p{p['part']} ({p['window'][0]}–{p['window'][1]} ث) | {span} | "
                         f"{p['words_allowed']} | {p['published_words']} | "
                         f"{'+' if gap > 0 else ''}{gap} |")
        lines += ["", "## تعليمات الطابع — المقاطع ذات طابع غير محايد", "",
                  "العادي «عادي» والحماس «حماس» لا من الاثنتين معًا؛ العمود `pace_target` هو "
                  "سرعة الإلقاء المستهدفة نسبةً لأساس الصوت، و`punch` عدد علامات التعجب/الأمر "
                  "المسموح، و`sentence_shape` طول الجُمل.", "",
                  "| مقطع | ثوانٍ | طابع | مسموح | سرعة | جُمل | لماذا |", "|---|---|---|---|---|---|---|"]
        notable = sorted((row for row in rows if row["register"] != "neutral"),
                         key=lambda r: (-(r["source_spread_st"] or 0)))
        for row in notable:
            lines.append(f"| p{row['part']}s{row['segment']} ({row['start']}–{row['end']} ث) | "
                         f"{row['seconds']} | **{row['register']}** | {row['words_allowed']} | "
                         f"×{row['pace_target']} | {row['sentence_shape']} | "
                         f"{(row['why_register'] or '')[:80]} |")
        lines += ["", f"العدد الكامل ({len(rows)} مقطعًا) في `round3-brief.json` بجانب هذا الملف، "
                  "مع `source_text` لكل مقطع كمرجع معنى (تفريغ آلي، ليس نصًا يُترجم).", ""]
        args.markdown.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"summary": summary,
                      "parts": [{k: p[k] for k in ("part", "words_allowed", "published_words",
                                                   "gap_words")} for p in parts]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
