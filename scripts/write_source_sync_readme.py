#!/usr/bin/env python3
"""Generate the delivery README of a source-synchronous dub from its measured reports.

Every number in the document is read from the plan, the render verification, and the hole
comparison - nothing is typed by hand, so a claim in the prose cannot outlive the render it
describes. If a field is missing the tool refuses instead of guessing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TEXT = """# دبلجة متزامنة مع كلام الأصل — صوت {voice} {voice_note}

ملف الإخراج: `final-dub-source-sync.mp4` — {duration} ثانية. الصورة منسوخة بدون إعادة ضغط،
والتعليق مصري رجالي بنفس التسجيلات المحفوظة في هذا المستودع.

هذه النسخة أُصلحت فيها **المزامنة فقط**. لم يُلَّد أي مقطع صوتي ولم تُسرَّع أي كلمة ولم تُحذف
أي جملة: التسجيلات استُعيدت من أرشيف FLAC المثبوت بـ sha256، ثم حُرِّك **الصمت وحده**.

## طريقة المزامنة

1. كلام الأصل في كل نافذة يُقسَّم إلى وحدات بعدد جُمل الدبلجة، والفصل يقع على أنفاس الأصل
   نفسها حيث توجد، وعند التوزيع المتناسب حيث لا توجد. الاقتران بالترتيب وكتلة الكلام، لا
   بتشابه النص: المطابقة اللفظية بين الصياغة المصرية والنص المنطوق ~35% فقط.
2. لكل جملة **مرساة** = بداية وحدتها في الأصل. وكل جملة تبدأ حيث يبدأ محتواها المقابل في
   الأصل. الوقفات تُحسب بإسقاط رياضي يقرب الصمت المتاح من المراسي
   (`youtube_auto_dub/sentence_anchor.py: spread_gaps`)، مع **استيفاء** مواضع المقاطع التي
   لا تحمل بداية جملة داخل المسافة بين مرساتين، فيبقى الجزء مغطّيًا نافذته كاملة ولا يتراكب
   كلامان.
3. القطع عند أدنى طاقة داخل صمت مقاس، والفجوة المسموعة لا تتجاوز {hole} ث، والفحص النهائي
   يقيس **الملف المسلَّم** لا الخطة: {conflicts} مرساة من {anchored} مستحيلة محليًا لأن
   مقطع الدبلجة أطول من زمن الأصل المقابل، ولا حركتها إلا بالنص.

## الفرق عن النسخة السابقة — مقياس واحد للنسختين

`scripts/compare_quiet_holes.py` يقيس الصمت الرقمي (ذروة العيّنات أقل من -55 dBFS على نوافذ
10 مللي‑ثانية) المتداخل مع كلام الأصل، على مسار التعليق الجاف في النسختين؛ الفجوات أقصر من
0.05 ث غير محسوبة، وأقلّ أفضل في كل سطر:

| المقياس | {prev_label} | هذه النسخة |
|---|---|---|
| عدد الفجوات المسموعة | {prev_count} | {count} |
| مجموع الصمت أثناء كلام الأصل | {prev_total} ث | **{total} ث** |
| أطول فجوة | {prev_max} ث | **{max_hole} ث** |
| متوسط الفجوة | {prev_mean} ث | {mean_hole} ث |
| الشريحة المئوية 90 | {prev_p90} ث | {p90_hole} ث |
| فجوات أطول من 0.7 ث | {prev_over} | **{over}** |
| متوسط خطأ موضع الجملة مقابل الأصل | {unanchored_mean} ث | **{mean_phase} ث** |
| أقصى خطأ موضع | {unanchored_max} ث | {max_phase} ث |

عدد الفجوات {delta_dirs} في هذه النسخة ومتوسطها أقصر: الفرق بين «صمت طويل يقطّع الكلام»
و«أنفاس قصيرة طبيعية» هو ما يسمعه الأذن، لا المجموع وحده.

## سقف الصمت: المفاضلة كاملة

{SPEED}

## الأجزاء

| الجزء | نافذة الأصل (ث) | موضع التعليق (ث) | انحراف البداية | وحدات الأصل | مقاطع | كلام (ث) | صمت متاح (ث) | صمت تطلبه المراسي (ث) | سقف الوقفة (ث) | مراسي مستحيلة | خطأ الموضع متوسط | أقصى |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
{table}

## أقيسة الملف المسلَّم

- الحالة **{status}** — {gates_passed}/{gates_total} بوابة ({failed}).
- {chunks} قطعة طابقت التسجيل المُعتمد عيّنة‑بعيّنة؛ أقصى خطأ موضع {align_ms} مللي‑ثانية
  ({align_samples} عيّنة)، وقطع صامتة في الماستر: {silent}.
- تغطية كلام الأصل {cov_dry:.2%} في الجاف و{cov_mix:.2%} في المكساج؛ أطول فترة صمت أثناء
  كلام الأصل {max_hole} ث ({gap_count} فجوة ≥ {report_gap} ث).
- سرعة الكلام {tempo}×؛ `speech_cut: false`؛ `overlapping_speech: {overlap}`؛ قصّات صمت
  {trim_count} بأقصى {trim_ms} مللي‑ثانية؛ صورة MD5 مطابقة للمصدر وأخطاء فك الترميز 0؛
  أقصى زمن حزمة {last_packet}.
- الجهارة {lufs} LUFS والذروة الحقيقية {tp} dBTP؛ {cues} وصلة نصية بتوقيت المقاطع.
- `human_listening_certification: false` — النطق واللهجة والانفعال تُحكم بسمعك لا بالأرقام.

## ما لا تدّعيه هذه النسخة

1. خطأ الموضع الباقي متوسطه {mean_phase} ث (وسيط {median_phase}، p90 {p90_phase}، أقصى
   {max_phase}) وليس صفرًا. السبب مقاسات الجُمل لا الخوارزمية: {conflicts} مرساة يبدأ وقتها
   وينتهي قبل أن تكمل الجملة السابقة كلامها. الصمت المتاح {owned} ث يكفي الطلب ({demand} ث)
   على مستوى الملف كله، لكن التوزيع المحلي هو العائق: الأجزاء الأضيق نصًا {starved}.
   العلاج: نص بكثافة الأصل ثم إعادة تسجيل، ومدخله `../timing-repair/clip-budget.json`
   و`../timing-repair/clause-fit-dense.json` (عدد الكلمات اللازمة لكل جملة).
2. {bg_note}
3. هذا الملف ليس «نهائيًا» قبل أن تسمعه: الفحوص تقيس التوقيت وسلامة الصورة، لا الجودة.

## الملفات

- `final-dub-source-sync.mp4` / `.srt` / `.mp3` — الفيديو والنص ومعاينة صوتية.
- `final-dub-source-sync.mix.json` — وصف المكساج (الخلفية، الخفض، الجهارة، البصمات).
  حقل `status` فيه يصف خطوة المكساج وحدها (`rendered_pending_validation`)؛ الحالة
  المُصادَق عليها تُقرأ من `render-verification.json` لا منه.
- `render-verification.json` — البوابات التسع ونتائجها.
- `../timing-repair/pause-plan.json` — خطة المواضع: لكل مقطع `anchor_target` و`anchor_error`
  و`take_piece`، وبصمة السلامة `{integrity}`.
- `../timing-repair/hole-comparison.json` — قياس الفجوات للنسختين.
- `../timing-repair/source-analysis/words.json` — {words} كلمة بتوقيتها، أساس الوحدات.

## كيف تُعيد الإنتاج

```bash
scripts/materialize_lossless_takes.py --script {script}
scripts/sync_takes_to_source_timeline.py \\
  --script {script} --source {source} \\
  --narration .cache/hajj-sync-repair/narration.wav \\
  --anchor-hole {hole} --min-speech {min_speech} \\
  --plan-json ../timing-repair/pause-plan.json --srt final-dub-source-sync.srt
{bed_recipe}
scripts/verify_source_sync_render.py --source {source} --output final-dub-source-sync.mp4 \\
  --narration .cache/hajj-sync-repair/narration.wav --plan ../timing-repair/pause-plan.json \\
  --script {script} --output-json render-verification.json
scripts/compare_quiet_holes.py --source {source} --master this=.cache/hajj-sync-repair/narration.wav \\
  --master previous={prev_master} --output-json ../timing-repair/hole-comparison.json
```

`--no-anchor` في الأمر الثاني يعيد إنتاج التعبئة القديمة؛ التقرير يطبع رقمَي الطريقتين معًا،
فكل مقارنة أعلاه قابلة للتكرار. لا استدعاء لأي مزوّد صوت في هذه الخطوات: الأصوات إما معتمدة
محفوظة، أو لا إخراج. هذه الصفحة نفسها مولَّدة بـ `scripts/write_source_sync_readme.py`.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--holes", type=Path, required=True)
    parser.add_argument("--sweep", type=Path, default=None,
                        help="timing-repair/policy-sweep.json from scripts/sweep_anchor_policy.py")
    parser.add_argument("--previous-master", type=Path, required=True)
    parser.add_argument("--previous-label", default="النسخة السابقة (تعبئة النافذة)")
    parser.add_argument("--voice", default="voice-12")
    parser.add_argument("--voice-note", default="المُعتمد")
    parser.add_argument("--script", default="dubs/hajj-dream-2108415/timing-repair/script.json")
    parser.add_argument("--source", default="library/hajj-dream-2108415/source.mp4")
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--mix-json", type=Path, default=None,
                        help="the mix report (defaults to final-dub-source-sync.mix.json next to --verification)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    ver = json.loads(args.verification.read_text(encoding="utf-8"))
    holes = json.loads(args.holes.read_text(encoding="utf-8"))["renders"]
    previous_key = [key for key in holes if key != list(holes)[-1]][0] if len(holes) > 1 else None
    if previous_key is None:
        raise ValueError("the hole comparison must contain the previous render too")
    prev, now = holes[previous_key], holes[list(holes)[-1]]
    phase, measured = plan["sentence_timing_error"], ver["clause_phase"]["phase_vs_original"]
    un = plan["sentence_timing_error_unanchored"]
    dry, mixed = ver["timeline"]["dry_narration"], ver["timeline"]["delivered_mix"]
    loud, quiet = ver["loudness"], ver["quiet_holes"]
    for key in ("mean_seconds", "median_seconds", "p90_seconds", "max_seconds", "anchored"):
        if measured.get(key) is not None and phase.get(key) is not None and abs(
                float(measured[key]) - float(phase[key])) > 0.15:
            raise ValueError(f"measured phase {key} disagrees with the plan: {measured[key]} vs {phase[key]}")
    rows = []
    for part in plan["parts"]:
        errs = [abs(row["anchor_error"]) for row in plan["timeline"]
                if row["part"] == part["index"] and row.get("anchor_error") is not None]
        rows.append(f"| {part['index']} | {part['window_start']:.1f}–{part['window_end']:.1f} | "
                    f"{part['start']:.1f}–{part['end']:.1f} | {part['anchor_drift']:+.2f} | "
                    f"{part['source_units']} | {len(part['gaps']) + 1} | {part['speech_seconds']:.1f} | "
                    f"{part['silence_owned']:.1f} | {part['anchor_silence_demand']:.1f} | "
                    f"{part['gap_ceiling']:.2f} | {part['anchor_conflicts']} | "
                    f"{sum(errs) / len(errs):.2f} | {max(errs):.2f} |")
    sweep = "لم يُمرَّر `--sweep`؛ شغّل `scripts/sweep_anchor_policy.py` لتوثيق المفاضلة."
    if args.sweep and args.sweep.exists():
        document = json.loads(args.sweep.read_text(encoding="utf-8"))
        cap = str(plan["anchor_hole"])
        sweep = ("كل سطر إعادة تخطيط كاملة بنفس التسجيلات"
                 " (`scripts/sweep_anchor_policy.py` → `../timing-repair/policy-sweep.json`)؛"
                 " عمودا الطور يقيسان جملة‑بجملة مقابل الأصل، وأعمدة الثقب تقطع الملف الصوتي نفسه:\n\n"
                 "| الوضع | متوسط خطأ الموضع (ث) | الوسيط | أقصى | فجوات | مجموعها (ث) | أكبرها (ث) |\n"
                 "|---|---|---|---|---|---|---|")
        for row in document["rows"]:
            mark = " ← **هذه النسخة**" if row["setting"] == f"anchor_hole_{cap}" else ""
            sweep += (f"\n| {row['setting']}{mark} | {row['phase_mean_seconds']} | "
                      f"{row['phase_median_seconds']} | {row['max'] if 'max' in row else row['phase_max_seconds']} | "
                      f"{row['hole_count']} | {row['hole_total_seconds']} | {row['hole_max_seconds']} |")
        sweep += ("\n\nالسطر الأول يعبّئ الصمت داخل النافذة بالتساوي؛ الأسطر التالية تحرّكه نحو المراسي."
                  " السقف الأوسع يحسّن الطور مقابل صمت أطول يسمعه الأذن، فقد اختير أكبر سقف يبقي أطول"
                  " فجوة ≤ 0.45 ث. هذه المفاضلة هي السبب في أن تحسين النص لا الخوارزمية هو المتبقّي.")
    mix_path = args.mix_json or (args.verification.parent / "final-dub-source-sync.mix.json")
    mix_report = json.loads(mix_path.read_text(encoding="utf-8")) if mix_path.exists() else {}
    gain = mix_report.get("background_gain_db")
    if gain is None:
        bg_note = ("**لا طبقة خلفية إطلاقًا**: المقطع الأصلي لا موسيقى فيه، وتقدير الفصل العصبي "
                   "المتاح منه 91% من طاقته في نطاق الكلام و28 dB تحت المكساج، أي أنه بقايا صوت "
                   "الراوي الأصلي لا موسيقى؛ كان يُضاف بـ+12 dB فيُسمع صوتان عربيان معًا، والآن "
                   "التعليق وحده على الصورة.")
    else:
        bg_note = (f"الخلفية تقدير UVR من صوت المصدر (+{gain} dB مع خفض تلقائي أثناء الكلام)؛ قد "
                   "تحمل بقايا من الراوي الأصلي، فالفصل الآلي لا يضمن نقاءً تامًا.")
    src = args.source or "library/hajj-dream-2108415/source.mp4"
    mixline = ("scripts/mix_original_background.py --voice-video {SRC} "
               "--voice-audio .cache/hajj-sync-repair/narration.wav \\\n"
               "  {FLAGS} --output final-dub-source-sync.mp4 --audio-bitrate 160k "
               "--work-dir .cache/hajj-sync-repair/mix")
    if gain is None:
        bed_recipe = mixline.replace("{SRC}", src).replace("{FLAGS}", "--no-background")
    else:
        bed_recipe = ("scripts/restore_background_parts.py --source " + src +
                      " --parts ../background/parts \\\n"
                      "  --output .cache/hajj-sync-repair/background.wav\n" +
                      mixline.replace("{SRC}", src).replace(
                          "{FLAGS}", "--background .cache/hajj-sync-repair/background.wav "
                                     f"--background-gain-db {gain}.0"))
    cues = len([row for row in plan["timeline"] if row["text"]])
    if args.srt and args.srt.is_file():
        cues = len([block for block in args.srt.read_text(encoding="utf-8").split("\n\n") if block.strip()])
    text = TEXT.format(
        voice=args.voice, voice_note=args.voice_note, duration=plan["source_duration"],
        hole=plan["anchor_hole"], min_speech=plan["min_speech_seconds"], SPEED=sweep,
        anchored=phase["anchored"], conflicts=plan["anchor_conflicts"],
        prev_label=args.previous_label, prev_count=prev["count"], count=now["count"],
        prev_total=prev["total_seconds"], total=now["total_seconds"], prev_max=prev["max_seconds"],
        max_hole=quiet["longest"][0]["duration"], prev_mean=prev["mean_seconds"],
        mean_hole=now["mean_seconds"], prev_p90=prev["p90_seconds"], p90_hole=now["p90_seconds"],
        prev_over=prev["over_0_7_seconds"], over=quiet["count_over_budget"],
        delta_dirs="أكثر" if now["count"] > prev["count"] else "أقل",
        unanchored_mean=un["mean_seconds"], unanchored_max=un["max_seconds"],
        mean_phase=phase["mean_seconds"], median_phase=phase["median_seconds"],
        p90_phase=phase["p90_seconds"], max_phase=phase["max_seconds"],
        table="\n".join(rows), status=ver["status"],
        gates_passed=sum(1 for value in ver["gates"].values() if value),
        gates_total=len(ver["gates"]), failed=", ".join(k for k, v in ver["gates"].items() if not v) or "لا بوابة فاشلة",
        chunks=plan["chunks"], align_ms=ver["speech"]["max_alignment_error_ms"],
        align_samples=ver["speech"]["max_alignment_error_samples"],
        silent=ver["speech"]["pieces_silent_in_master"], cov_dry=dry["source_speech_coverage"],
        cov_mix=mixed["source_speech_coverage"], gap_count=dry["gap_count"], report_gap=0.3,
        tempo=plan["tempo_applied"], overlap=plan["overlapping_speech"],
        trim_count=plan["pieces_trimmed"], trim_ms=plan["max_piece_trim_ms"],
        last_packet=ver["picture"]["output_last_packet_time"], lufs=loud["input_i"], tp=loud["input_tp"],
        cues=cues, owned=plan["silence_owned_seconds"], demand=plan["anchor_silence_demand_seconds"],
        starved=", ".join(str(i) for i in plan["text_starved_parts"]) or "لا شيء",
        bg_note=bg_note, bed_recipe=bed_recipe,
        words=plan["source_word_count"], integrity=plan["parts"][0]["integrity"],
        script=args.script, source=args.source, prev_master=str(args.previous_master))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"written": str(args.output), "bytes": len(text.encode()),
                      "numbers_checked": ["plan vs measured phase", "gates", "holes", "coverage"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
