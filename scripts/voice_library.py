#!/usr/bin/env python3
"""مكتبة مئة عينة: كل سطر فيها مقطع مولّد فعليًا من أداة الصوت، لا فلتر ولا تكرار.

Three rules decide the shape of this file, and all three come from demands the user already made:

1. **Nothing measured is claimed.** A row never says "this is the grief take" because someone
   typed an emotion into a table. The engine got plain text and nothing else, so the only
   delivery labels in here are measured off the samples themselves (rate, level, f0 spread,
   low-band share). Adjectives are derived from numbers or they are not written.
2. **Nothing processed is sold as a dialect.** The platform honoured only `ar` and `ar-EG` and
   refused `ar-SA/ar-MA/ar-DZ/ar-AE/ar-KW/ar-LB/ar-IQ/ar-TN/ar-JO`, so every row is the raw
   engine output (`post_processing: anull`). No Egyptian clip is pitch-bent and labelled Saudi.
3. **No filler.** 100 rows need 100 different texts, so they are pulled from the dub script
   itself - the same sentences the finished narration has to carry - and a row stays `pending`
   until its file actually exists on disk.

    python scripts/voice_library.py plan --total 100 --voices voice-08,voice-09,...
    python scripts/voice_library.py next --batch 10
    python scripts/voice_library.py ingest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCRIPT_TXT = "dubs/hajj-dream-2108415/script.ar-EG.txt"
KIT_TXT = "dubs/hajj-dream-2108415/voice-tests/dialect-stress.txt"
PLAN = "docs/voice-library/plan.json"
DOC = "docs/voice-library.json"
PAGE = "docs/voice-library.html"
AUDIO = "docs/voice-library"
SILENT_FLOOR = -55.0

VERDICT = {  # what the user actually said about each voice in this session - nothing more
    "default": "لم يُعلَّق عليه بعد",
    "refused": "رُفض راويًا للدبلجة في الجلسة دي (وقائمة اللهجة 0 من 6 لم تُكمَل له)",
}
BATCH_OF = {0: "index 0–2 (مرفوض: «كلها سئ»)", 1: "index 3–7 (مرفوض: «كلها سئ»)",
            2: "index 8–12 (اخترته المنصة من battle، ولم يُعلَّق عليه راويًا بعد)"}


def battle_meta(num: int) -> tuple[int | None, int]:
    """Which audition batch a session voice id came from. Labels restart every session."""
    if 0 <= num <= 2:
        return 0, num
    if 3 <= num <= 7:
        return 1, num
    if 8 <= num <= 12:
        return 2, num
    return None, num


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_marks(text: str) -> str:
    return re.sub(r"[«»\"'\u0640*]", "", text).strip()


def script_sentences(limit_hi: int = 22, limit_lo: int = 6) -> list[tuple[int, str]]:
    """Real dub lines, kept with the paragraph they came from (that is the p0..p6 of the budget)."""
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pi, para in enumerate([p for p in Path(ROOT / SCRIPT_TXT).read_text(encoding="utf-8").split("\n") if p.strip()]):
        for sent in re.split(r"(?<=[.!?؛])\s+", para):
            sent = strip_marks(sent)
            n = len(sent.split())
            if limit_lo <= n <= limit_hi and sent not in seen:
                seen.add(sent)
                out.append((pi, sent))
    return out


def kit_lines() -> list[tuple[int, str]]:
    """The six dialect-trap lines. Same material the narrator gate runs on."""
    rows: list[tuple[int, str]] = []
    for line in Path(ROOT / KIT_TXT).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([1-6])\.\s*(.+)", line)
        if m:
            rows.append((int(m.group(1)) - 1, strip_marks(m.group(2))))
    return rows


def para_words(text: str) -> int:
    return len(strip_marks(text).split())


def cmd_plan(args) -> int:
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    if not voices:
        raise SystemExit("--voices is empty")
    # The six gate lines come first: they are the only material every candidate narrator is
    # already required to survive, so the library is comparable with the narrator test.
    lines = [(f"kit{pi + 1}", t) for pi, t in kit_lines()] + \
            [(f"p{pi}", t) for pi, t in script_sentences()]
    if len(lines) < args.total:
        raise SystemExit(f"only {len(lines)} distinct lines available for {args.total} samples")
    items = []
    for i in range(args.total):
        pi, text = lines[i]
        vi = i % len(voices)
        raw = voices[vi]
        num = int(re.sub(r"\D", "", raw) or 0)
        batch, battle = battle_meta(num)
        items.append({
            "id": f"lib-{i + 1:03d}",
            "voice_id": raw,
            "battle_index": battle,
            "battle_batch": BATCH_OF.get(batch, "خارج الدفعات المسجلة"),
            "voice_verdict": VERDICT["refused"] if num < 8 else VERDICT["default"],
            "text": text,
            "words": para_words(text),
            "paragraph": pi,
            "source_line": "dialect-stress.txt" if pi.startswith("kit") else "script.ar-EG.txt",
            "audio": f"voice-library/{Path(raw).stem}-{i + 1:03d}.mp3",
            "status": "pending",
        })
    out = ROOT / PLAN
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"built_by": "scripts/voice_library.py", "total": args.total,
                               "voices": voices, "rule": "لا فلتر ولا لهجة مُدّعاة ولا نص متكرر؛ "
                               "والسطر يفضل pending لحد ما ملفه يبقى على الديسك",
                               "items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"planned": len(items), "distinct_texts": len({it["text"] for it in items}),
                      "distinct_voices": len({it["voice_id"] for it in items}), "plan": str(out)},
                     ensure_ascii=False))
    return 0


def load_plan() -> dict:
    path = ROOT / PLAN
    if not path.exists():
        raise SystemExit(f"no plan at {path}; run `plan` first")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_next(args) -> int:
    doc = load_plan()
    pend = [it for it in doc["items"] if not (ROOT / "docs" / it["audio"]).exists()]
    print(json.dumps({"pending": len(pend), "done": len(doc["items"]) - len(pend),
                      "next": [{"id": it["id"], "voice_id": it["voice_id"], "audio": it["audio"],
                                "text": it["text"], "words": it["words"]} for it in pend[:args.batch]]},
                     ensure_ascii=False, indent=1))
    return 0


def measure(path: Path) -> dict:
    audio, rate = sf.read(str(path), always_2d=True)
    x = np.asarray(audio, dtype=np.float32).mean(axis=1)
    seconds = round(len(x) / rate, 2)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt((x ** 2).mean())) if len(x) else 0.0
    level = round(20 * math.log10(max(rms, 1e-9)), 2)
    frame = max(1, int(0.01 * rate))
    peaks = np.abs(x[: len(x) - len(x) % frame].reshape(-1, frame)).max(axis=1)
    voiced = peaks > np.percentile(peaks, 60) if len(peaks) else np.array([False])
    low = 0.0
    if len(x) > rate // 4:
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), 1 / rate)
        total = float(spec.sum()) or 1.0
        low = round(float(spec[freqs < 200.0].sum()) / total, 3)
    from youtube_auto_dub import affect

    stats = affect.pitch_stats(x, rate)
    # Rate has to be measured on the speech span, not the file length: the engine pads the ends,
    # and counting that padding as "slow narrator" is exactly the silence-padding the user banned.
    live = np.where(peaks > max(np.percentile(peaks, 40) + 1e-6, 10 ** (SILENT_FLOOR / 20)))[0] if len(peaks) else []
    first = int(live[0]) if len(live) else 0
    last = int(live[-1]) if len(live) else 0
    span = round(max((last - first) * frame / rate, 1e-3), 2) if len(live) else seconds
    facts = {"seconds": seconds, "sample_rate": int(rate), "peak_dbfs": round(20 * math.log10(max(peak, 1e-9)), 2),
             "level_dbfs": level, "voiced_share": round(float(voiced.mean()), 3) if len(voiced) else 0.0,
             "low_band_share_under_200hz": low, "clipped": bool(peak >= 0.999),
             "silent": bool(level < SILENT_FLOOR),
             "speech_span_seconds": span,
             "engine_padding_seconds": round(max(seconds - span, 0.0), 2),
             "leading_silence_seconds": round(first * frame / rate, 2),
             "trailing_silence_seconds": round(max((len(peaks) - last - 1) * frame / rate, 0.0), 2)}
    for key in ("f0_mean", "f0_spread", "f0_range"):
        value = stats.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            facts[key] = round(float(value), 2)
    return facts


def delivery_label(facts: dict, rate_words: float | None) -> str:
    """Measured, not claimed: what the numbers say the read actually did."""
    bits = []
    if facts.get("level_dbfs", -99) > -18:
        bits.append("أعلى من متوسط الكلام")
    elif facts.get("level_dbfs", 0) < -24:
        bits.append("أخفض من متوسط الكلام")
    spread = facts.get("f0_spread")
    if isinstance(spread, (int, float)):
        bits.append("حركة نبرة واسعة" if spread >= 4.0 else "حركة نبرة ضيّقة")
    if isinstance(rate_words, float):
        fit = "يبلع ميزانية الجولة التالتة" if rate_words >= 2.507 else "أبطأ من 2.507 ك/ث"
        bits.append(f"سرعته {rate_words} ك/ث — {fit}")
    return "، ".join(bits) if bits else "مقيس بلا فوارق تُذكر"


def cmd_ingest(args) -> int:
    doc = load_plan()
    rows, generated, pending = [], 0, 0
    for it in doc["items"]:
        row = {k: it[k] for k in ("id", "voice_id", "battle_index", "battle_batch", "voice_verdict",
                                  "text", "words", "paragraph", "source_line")}
        row["audio"] = it["audio"]
        path = ROOT / "docs" / it["audio"]
        if not path.exists():
            row["status"] = "pending"
            pending += 1
            rows.append(row)
            continue
        facts = measure(path)
        rate = round(it["words"] / facts["speech_span_seconds"], 3) if facts["speech_span_seconds"] else None
        row.update({"status": "generated" if not facts["silent"] else "failed_silent",
                    "sha256": sha256(path), "bytes": path.stat().st_size,
                    "post_processing": "anull", "words_per_sec": rate,
                    "delivery_measured": delivery_label(facts, rate), "facts": facts})
        if row["status"] == "generated":
            generated += 1
        rows.append(row)
    by_voice: dict[str, dict] = {}
    for row in rows:
        if row.get("words_per_sec"):
            by_voice.setdefault(row["voice_id"], []).append(row["words_per_sec"])
    voices = {vid: {"clips": len(v), "words_per_sec_mean": round(sum(v) / len(v), 3),
                    "min": round(min(v), 3), "max": round(max(v), 3)}
              for vid, v in sorted(by_voice.items())}
    out = {"built_by": "scripts/voice_library.py", "project": "hajj-dream-2108415",
           "total_slots": len(rows), "generated": generated, "pending": pending,
           "distinct_voices": len({r["voice_id"] for r in rows}),
           "distinct_texts": len({r["text"] for r in rows}),
           "audio_rules": ("كل ملف هو خرج أداة الصوت مباشرة: no filtering, no pitch, no music, "
                           "no silence padding. أي لهجة غير ar/ar-EG مرفوضة من المنصة فتُترك "
                           "كملاحظة مش كفلتر."),
           "pace_budget": {"target_words_per_sec": 2.507, "round3_allowed": 1633,
                           "round3_published": 1457, "deficit_words": 176},
           "voices": voices, "samples": rows}
    (ROOT / DOC).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_page(out)
    print(json.dumps({"total_slots": len(rows), "generated": generated, "pending": pending,
                      "failed": sum(1 for r in rows if r["status"] == "failed_silent"),
                      "distinct_voices": out["distinct_voices"], "doc": DOC, "page": PAGE},
                     ensure_ascii=False))
    return 0


PAGE_TEMPLATE = r"""<!doctype html><html dir="rtl" lang="ar"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>مكتبة مئة عينة صوت</title>
<style>
body{font-family:system-ui,"Segoe UI",Tahoma,sans-serif;background:#12141a;color:#e8ecf3;margin:0;padding:22px}
h1{font-size:20px;margin:0 0 6px}.sub{color:#9fb0c8;font-size:13px;margin-bottom:14px;line-height:1.8}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
input,select{background:#1c2130;color:#e8ecf3;border:1px solid #2c3547;border-radius:8px;padding:7px 10px;font:inherit;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid #232a3a;text-align:right;vertical-align:top}
th{color:#9fb0c8;font-weight:600;position:sticky;top:0;background:#12141a}
tr.gen{background:#141b24}tr.pen{background:#171521;opacity:.6}tr.bad{background:#241416}
code{background:#1c2130;padding:1px 5px;border-radius:5px;font-size:12px}
.ok{color:#7ddc9b}.no{color:#e6757a}.mut{color:#8698b3;font-size:12px}
audio{width:210px;height:30px;vertical-align:middle}
</style></head><body>
<h1>مكتبة مئة عينة صوت — @@GEN@@/@@TOTAL@@ مولَّد فعليًا</h1>
<div class="sub">@@RULES@@<br>أصوات الجلسة: <code>@@VOICES@@</code> · نصوص مختلفة: <code>@@TEXTS@@</code> ·
ميزانية الجولة التالتة: <code>1633</code> مسموح / <code>1457</code> منشور / عجز <code>176</code> كلمة ·
هدف السرعة <code>2.507</code> كلمة/ث.<br>
كل تعليق في عمود «مقيس» محسوب من العيّنات نفسها (سرعة، جهارة، اتساع نبرة، نطاق تحت 200 هرتز)،
مش صفة مكتوبة قبل السماع. <span class="no">لا يُعتمد أي صوت راويًا بالأرقام وحدها</span> —
القرار بسمعك على سطور اختبار اللهجة الستة.</div>
<div class="bar"><input id="q" placeholder="دوّر في الكلام...">
<select id="v"><option value="">كل الأصوات</option></select>
<select id="s"><option value="">كل الحالات</option><option>generated</option><option>pending</option>
<option>failed_silent</option></select>
<span class="mut" id="c">@@COUNT@@</span></div>
<table><thead><tr><th>#</th><th>الصوت</th><th>العينة</th><th>النص</th><th>كلمة/ث</th><th>مقيس</th><th>الحالة</th></tr></thead>
<tbody id="b"></tbody></table>
<script>
const D=@@ROWS@@;
const vb=document.getElementById("v");
[...new Set(D.map(r=>r.voice_id))].forEach(v=>{const o=document.createElement("option");o.textContent=v;vb.appendChild(o)});
function esc(t){return String(t==null?"":t).replace(/[&<>]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;"}[c]})}
function cls(r){return r.status==="generated"?"gen":(r.status==="failed_silent"?"bad":"pen")}
function draw(){
  const q=document.getElementById("q").value.trim(), v=vb.value, s=document.getElementById("s").value;
  const b=document.getElementById("b"); b.innerHTML=""; let n=0;
  D.forEach(function(r){
    if(v && r.voice_id!==v) return;
    if(s && r.status!==s) return;
    if(q && esc(r.text).indexOf(q)<0) return;
    n++;
    const tr=document.createElement("tr"); tr.className=cls(r);
    const rate=r.words_per_sec?r.words_per_sec.toFixed(3):"—";
    const a=r.status==="generated"
      ?'<audio controls preload="none" src="'+r.audio+'"></audio>'
      :'<span class="mut">لسه ما تولّدش</span>';
    const hash=r.sha256?r.sha256.slice(0,12):"";
    tr.innerHTML="<td>"+r.id+"<br><span class='mut'>"+r.paragraph+"</span></td>"
      +"<td><code>"+r.voice_id+"</code><br><span class='mut'>"+esc(r.battle_batch)+"</span></td>"
      +"<td>"+a+"</td>"
      +"<td>"+esc(r.text)+"<br><span class='mut'>"+r.words+" كلمة · "+hash+"</span></td>"
      +"<td>"+rate+"</td>"
      +"<td>"+esc(r.delivery_measured||"—")+"</td>"
      +"<td><span class='"+(r.status==="generated"?"ok":"mut")+"'>"+r.status+"</span>"
      +"<br><span class='mut'>"+esc(r.voice_verdict)+"</span></td>";
    b.appendChild(tr);
  });
  document.getElementById("c").textContent="معرض "+n+" سطر";
}
["q","v","s"].forEach(function(i){document.getElementById(i).addEventListener("input",draw)});
draw();
</script></body></html>
"""


def write_page(doc: dict) -> None:
    rows = [{k: r.get(k) for k in ("id", "voice_id", "text", "words", "words_per_sec",
                                  "delivery_measured", "voice_verdict", "battle_batch",
                                  "status", "audio", "sha256", "bytes", "paragraph")}
            for r in doc["samples"]]
    html = (PAGE_TEMPLATE
            .replace("@@ROWS@@", json.dumps(rows, ensure_ascii=False))
            .replace("@@GEN@@", str(doc["generated"]))
            .replace("@@TOTAL@@", str(doc["total_slots"]))
            .replace("@@VOICES@@", str(doc["distinct_voices"]))
            .replace("@@TEXTS@@", str(doc["distinct_texts"]))
            .replace("@@RULES@@", doc["audio_rules"])
            .replace("@@COUNT@@", f"مُولَّد {doc['generated']} · في الانتظار {doc['pending']}"))
    (ROOT / PAGE).write_text(html, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--total", type=int, default=100)
    p.add_argument("--voices", default="")
    p.set_defaults(func=cmd_plan)
    n = sub.add_parser("next")
    n.add_argument("--batch", type=int, default=10)
    n.set_defaults(func=cmd_next)
    g = sub.add_parser("ingest")
    g.set_defaults(func=cmd_ingest)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
