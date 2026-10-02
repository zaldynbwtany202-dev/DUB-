#!/usr/bin/env python
"""Quality audit of a dub run: the gate the pipeline runs before it builds anything.

It answers, with measurements and against config/dubbing-qa.json:

  * تغطية: which groups in the range have a take at all
  * نبرة: every take's own pitch, how far it sits from the approved 137.2 Hz, how
    much it drifts inside itself, and the step between each pair of neighbours
  * ملاءمة: what the assembler will actually do with each take -- required tempo,
    whether it will be clamped at the locked ceiling, the lateness that clamping
    pushes onto the following groups, and the silence left when a take is short
  * مستوى: spread across takes
  * خطة: the candidate requests that would fix it, ordered by how bad each group is

    python3 scripts/audit_dub.py --slug into-the-wild --takes work/into-the-wild/takes-voice13 \
        --start 6 --end 26 --json work/into-the-wild/qa/audit-latest.json

Exit codes
    0  clean            -- no hard violation, no candidate needed
    1  candidate plan   -- deliverable allowed, but the run is outside the target band
    2  hard violation   -- the build is blocked (see gate.block_build_on)
    --gate prints one PASS/FAIL line per hard check, for the build path to read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from measure_voice_pitch import report  # noqa: E402

POLICY = Path("config/dubbing-qa.json")


def measure(path: Path, target: float) -> dict:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    r = report(path.stem, x, sr, target, True)
    r["seconds"] = round(len(x) / sr, 3)
    r["rms_db"] = round(20 * np.log10(float(np.sqrt((x ** 2).mean())) + 1e-9), 2)
    return r


def fit_run(rows: list[dict], groups: list[dict], ceiling: float, pack: bool,
            elastic: bool = False, min_tempo: float = 0.9) -> None:
    """Replicate the assembler's placement maths, in place.

    Two assemblers exist. The plain one stretches a take by natural/window, never
    below 1.0, and never above the locked ceiling; in packed mode a clamped group
    starts late and the next group inherits the delay. The word-aligned one
    (`align_by_words.py`, used when a run is built with --word-align) lays every
    take inside its own window from the window's start to its end: it slows a take
    down to min_tempo when the text is shorter than the window (the window plan is
    what has to carry that, not the pace) and it never leaves a gap. Simulating the
    right one here is what turns "this take is long" into "the voice will be 4.8 s
    behind the picture at group 25".
    """
    cursor = None
    for r in rows:
        g = groups[r["group"]]
        t0, window = float(g["t0"]), float(g["window"])
        start = max(t0, cursor) if (pack and cursor is not None) else t0
        room = max(0.05, t0 + window - start) if pack else window
        need = r["seconds"] / room
        tempo = need
        if elastic:
            tempo = min(max(need, min_tempo), ceiling)
            clamped = need > ceiling + 1e-9
        else:
            # المزج الحقيقي (assemble_dub.build_group) يبطئ إلى min_tempo حين تكون
            # الأخذة أقصر من نافذتها، فيملأ النافذة ولا يترك صمتًا. المحاكاة كانت
            # تمنع أي إبطاء (tempo ≥ 1.0) فتُنذر بفراغات لا وجود لها في الملف
            # الناتج: الفيلم المسلَّم قِيس فيه أطول صمت 0.00 ث بينما تنبّأت
            # المحاكاة بفجوات 0.41–0.44 ث. صُحّحت لتطابق المزج.
            if tempo < min_tempo:
                tempo = min_tempo
            clamped = tempo > ceiling + 1e-9
            if clamped:
                tempo = ceiling
        end = start + r["seconds"] / tempo
        if cursor is None or end > cursor:
            cursor = end
        r.update({"window": round(window, 2), "room": round(room, 2),
                  "required_tempo": round(need, 3), "tempo": round(tempo, 3),
                  "start": round(start, 3), "end": round(end, 3),
                  "late": round(start - t0, 3), "clamped": bool(clamped)})
    for a, b in zip(rows, rows[1:]):
        b["gap_before"] = round(max(0.0, b["start"] - a["end"]), 2)
    if rows:
        rows[0].setdefault("gap_before", 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--takes", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--gate", action="store_true",
                    help="اطبع نتيجة كل شرط صارم في سطر واحد ثم اخرج بـ0/2")
    ap.add_argument("--elastic", action="store_true",
                    help="البناء بملاءمة الكلمات (align_by_words): كل أخذة داخل نافذتها "
                         "من بدايتها إلى نهايتها، بتباطؤ لا يقل عن أدنى سرعة")
    ap.add_argument("--plan-limit", type=int, default=10,
                    help="أقصى عدد مرشحين يُعرض في جولة واحدة (سقف حصة التوليد)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    pol = json.loads(POLICY.read_text(encoding="utf-8"))
    pit, tgt = pol["pitch"], pol["pitch"]["target"]
    hard, lvl, tim = pol["pitch"]["hard"], pol["level"], pol["timing"]
    att = pol["pitch"].get("attention", {"max_deviation_semitone": 2.5,
                                         "max_step_semitone": 2.6})
    target = a.target or float(pit["target_hz"])
    work = Path("work") / a.slug
    takes = Path(a.takes)
    groups = json.loads((work / "groups.json").read_text(encoding="utf-8"))["groups"]
    idx = [g for g in range(a.start, min(a.end, len(groups)))]

    # The plan carries both t1-t0 and a `window` field the assembler reads; if they
    # ever disagree the audit measures a run the build will not produce.
    inconsistent = [g for g in idx
                    if abs(float(groups[g]["window"]) - (float(groups[g]["t1"])
                                                         - float(groups[g]["t0"]))) > 0.01]

    rows, missing = [], []
    for g in idx:
        p = takes / f"g{g:03d}.mp3"
        if not p.exists():
            missing.append(g)
            continue
        r = measure(p, target)
        r["group"] = g
        rows.append(r)
    if not rows:
        print("  ✗ لا أخذات في المدى")
        return 2 if a.gate else 1
    fit_run(rows, groups, float(tim["atempo_ceiling"]), pack=True,
            elastic=a.elastic, min_tempo=float(tim.get("min_tempo", 0.9)))

    semis = np.array([r["semis"] for r in rows])
    steps = np.abs(np.diff(semis)) if len(semis) > 1 else np.array([0.0])
    step_between = [f"{rows[i]['group']}→{rows[i+1]['group']}" for i in range(len(steps))]
    lvlv = np.array([r["rms_db"] for r in rows])
    level_spread = float(lvlv.max() - lvlv.min())
    max_late = max([r["late"] for r in rows], default=0.0)
    max_gap = max([r.get("gap_before", 0.0) for r in rows], default=0.0)
    n_clamped = sum(1 for r in rows if r["clamped"])

    checks = [
        ("plan_consistent", not inconsistent,
         f"{len(inconsistent)} مجموعة بياناتها متناقضة (window ≠ t1−t0): {inconsistent[:6]}" if inconsistent
         else "نافذة كل مجموعة = t1−t0 كما يقرأها المُجمِّع"),
        ("take_missing", not missing, f"{len(missing)} مجموعة بلا أخذة" if missing else "كل المجموعات لها أخذات"),
        ("tempo_clamped", n_clamped == 0,
         f"{n_clamped} مجموعة تحتاج أكثر من {tim['atempo_ceiling']}× فتُقصّ" if n_clamped
         else f"أقصى سرعة مطلوبة {max(r['required_tempo'] for r in rows):.2f}×"),
        ("lateness", max_late <= float(tim["max_lateness_seconds"]),
         f"أقصى تأخير {max_late:.2f} ث (السقف {tim['max_lateness_seconds']})"),
        ("silence_gap", max_gap <= float(tim["max_silence_gap_seconds"]),
         f"أطول صمت بين مجموعتين {max_gap:.2f} ث (السقف {tim['max_silence_gap_seconds']})"),
        ("pitch_hard_band", float(np.abs(semis).max()) <= float(hard["max_deviation_semitone"]),
         f"أقصى بُعد {np.abs(semis).max():.2f} نصف نغمة (الحدّ المطلق {hard['max_deviation_semitone']})"),
        ("step_hard_band", float(steps.max()) <= float(hard["max_step_semitone"]),
         f"أقصى قفزة {steps.max():.2f} نصف نغمة (الحدّ المطلق {hard['max_step_semitone']})"),
        ("pitch_attention_band",
         float(np.abs(semis).max()) <= float(att["max_deviation_semitone"]),
         f"أقصى بُعد {np.abs(semis).max():.2f} نصف نغمة (سقف ما أجازه المستمع "
         f"{att['max_deviation_semitone']})"),
        ("step_attention_band", float(steps.max()) <= float(att["max_step_semitone"]),
         f"أقصى قفزة {steps.max():.2f} نصف نغمة (سقف ما أجازه المستمع {att['max_step_semitone']})"),
        ("level_spread", level_spread <= float(lvl["max_spread_db"]),
         f"تفاوت المستوى {level_spread:.2f} د.ب (السقف {lvl['max_spread_db']})"),
    ]
    hard_names = set(pol["gate"]["block_build_on"])
    failed = [c for c, ok, _ in checks if not ok and c in hard_names]
    attention = [c for c, ok, _ in checks
                 if not ok and c not in hard_names and c.endswith("attention_band")]

    print(f"تدقيق الجودة — {a.slug} · المجموعات {idx[0]}–{idx[-1]} · الهدف {target} هرتز · "
          f"سياسة v{pol.get('version', 1)}\n")
    if a.gate:
        for name, ok, msg in checks:
            mark = "✓" if ok else ("✗" if name in hard_names else "!")
            print(f"  {mark} {name:<22} {msg}")
    else:
        print(f"{'مجموعة':>8}{'نبرة':>9}{'بعده':>9}{'انزياح':>9}{'ثوان':>8}{'سرعة':>8}"
              f"{'تأخير':>9}{'صمت':>7}{'مستوى':>9}   الحكم")
        for r in rows:
            flags = []
            if abs(r["semis"]) > hard["max_deviation_semitone"]:
                flags.append("نبرة!")
            elif abs(r["semis"]) > tgt["max_deviation_semitone"]:
                flags.append("بعيد")
            if r["clamped"]:
                flags.append("قصّ!")
            if r["late"] > float(tim["max_lateness_seconds"]):
                flags.append("تأخير!")
            elif r["required_tempo"] > float(tim["tempo_warn"]):
                flags.append("سريع")
            if r.get("gap_before", 0) > float(tim["max_silence_gap_seconds"]):
                flags.append("صمت!")
            print(f"{r['group']:>8}{r['median']:>9.1f}{r['semis']:>+9.2f}"
                  f"{r.get('drift_semis', 0):>+9.2f}{r['seconds']:>8.1f}{r['tempo']:>8.2f}"
                  f"{r['late']:>9.2f}{r.get('gap_before', 0):>7.2f}{r['rms_db']:>9.2f}   "
                  + " ".join(flags))
        print()
        for name, ok, msg in checks:
            mark = "✓" if ok else ("✗" if name in hard_names else "!")
            print(f"  {mark} {name:<22} {msg}")

    worst_step = int(np.argmax(steps)) if len(steps) else 0
    print(f"\n  النبرة: مدى {semis.min():+.2f}…{semis.max():+.2f} نصف نغمة · "
          f"متوسط القفزة {steps.mean():.2f} · أقصى قفزة {steps.max():.2f} "
          f"(بين {step_between[worst_step] if step_between else '—'})")

    # What the next generation round should ask for, worst group first: distance
    # from the target and the size of the jump it takes part in are the two things
    # a fresh take can change, and a group that fails both goes to the front.
    plan = []
    for i, r in enumerate(rows):
        step = max(steps[i - 1] if i > 0 else 0.0, steps[i] if i < len(steps) else 0.0)
        bad_dev = abs(r["semis"]) > float(tgt["max_deviation_semitone"])
        bad_step = step > float(tgt["max_step_semitone"])
        if not (bad_dev or bad_step):
            continue
        per = int(pit["candidates_per_group"])
        plan.append({"group": r["group"], "candidates": per if (bad_dev and bad_step) else max(1, per - 1),
                     "needs": "+".join([*(["انزياح"] if bad_dev else []), *(["قفزة"] if bad_step else [])]),
                     "semis": r["semis"], "step": round(float(step), 2),
                     "urgency": round(abs(r["semis"]) / float(tgt["max_deviation_semitone"])
                                      + step / float(tgt["max_step_semitone"]), 2)})
    plan.sort(key=lambda p: -p["urgency"])
    if plan:
        print(f"  خطة التوليد: {sum(p['candidates'] for p in plan)} مرشحًا على "
              f"{len(plan)} مجموعة (أسوأها أولًا):")
        for p in plan[:a.plan_limit]:
            print(f"    g{p['group']:03d} × {p['candidates']}   ({p['needs']} · "
                  f"بُعد {p['semis']:+.2f} · قفزة {p['step']:.2f})")
        if len(plan) > a.plan_limit:
            print(f"    … و{len(plan) - a.plan_limit} مجموعة بعدها في الجولة التالية")
    else:
        print("  ✓ النبرة داخل المدى المطلوب — لا مرشحين مطلوبين.")

    verdict = ("مرفوض: شرط صارم" if failed else "يحتاج مرشحين" if plan else "جاهز للبناء")
    if attention and not failed:
        verdict = "يُبنى بتحفّظ: مدى غير نهائي"
    print(f"\n  الحكم: {verdict}" + (f" ({'، '.join(failed)})" if failed else ""))
    if attention:
        print("  ! تنبيه: نبرة خارج سقف ما أجازه المستمع — المرشحات إلزامية للجولة القادمة، "
              "ولا يُسمّى هذا العمل مُجازًا.")

    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"slug": a.slug, "range": [idx[0], idx[-1]], "target": target,
             "policy_version": pol.get("version", 1), "rows": rows, "missing": missing,
             "plan": plan, "verdict": verdict, "failed": failed,
             "attention": attention,
             "checks": [{"name": n, "ok": ok, "detail": m} for n, ok, m in checks],
             "steps_mean": round(float(steps.mean()), 3),
             "steps_max": round(float(steps.max()), 3),
             "level_spread": round(level_spread, 2),
             "tempo_max": round(max(r["required_tempo"] for r in rows), 3),
             "max_late": round(max_late, 3), "max_gap": round(max_gap, 3)},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {out}")
    if failed:
        return 2
    return 1 if (missing or plan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
