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
import subprocess
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
    # A manifest that hashes files git never stored is a lie waiting for the next sandbox reset:
    # that is exactly how 19 approved clips vanished while their JSON survived the commit.
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "docs/voice-library"],
                             capture_output=True, text=True).stdout.split()
    on_disk = sorted(str(r.relative_to(ROOT)) for r in (ROOT / "docs" / "voice-library").glob("*.mp3"))
    untracked = sorted(set(on_disk) - set(tracked))
    if untracked:
        print(f"WARNING: {len(untracked)} generated file(s) are NOT tracked by git "
              f"(.gitignore is swallowing them): {untracked[:4]}", file=sys.stderr)

    out = {"built_by": "scripts/voice_library.py", "project": "hajj-dream-2108415",
           "total_slots": len(rows), "generated": generated, "pending": pending,
           "distinct_voices": len({r["voice_id"] for r in rows}),
           "audio_files_on_disk": len(on_disk), "audio_files_tracked": len([t for t in tracked if t.endswith(".mp3")]),
           "untracked_audio": untracked,
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
<meta name="viewport" content="width=device-width,initial-scale=1"><title>مكتبة مئة عينة صوت — تختار من هنا</title>
<style>
body{font-family:system-ui,"Segoe UI",Tahoma,sans-serif;background:#12141a;color:#e8ecf3;margin:0;padding:20px}
h1{font-size:19px;margin:0 0 6px}
.sub{color:#9fb0c8;font-size:13px;line-height:1.8;margin-bottom:12px}
.card{background:#171c26;border:1px solid #232a3a;border-radius:12px;padding:10px 12px;margin-bottom:12px;font-size:13px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
input,select,button{background:#1c2130;color:#e8ecf3;border:1px solid #2c3547;border-radius:8px;padding:7px 10px;font:inherit;font-size:13px}
button{cursor:pointer}button.pick{background:#2563eb;border-color:#2563eb;color:#fff}
button.pick.sel{background:#0f9d58;border-color:#0f9d58}
button.ghost{background:transparent}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 6px;border-bottom:1px solid #232a3a;text-align:right;vertical-align:top}
th{color:#9fb0c8;font-weight:600;position:sticky;top:0;background:#12141a}
tr.gen{background:#141b24}tr.pen{background:#171521;opacity:.55}tr.bad{background:#241416}
tr.sel{outline:1px solid #0f9d58}
code{background:#1c2130;padding:1px 5px;border-radius:5px;font-size:12px}
.ok{color:#7ddc9b}.no{color:#e6757a}.mut{color:#8698b3;font-size:12px}
audio{width:200px;height:30px;vertical-align:middle}
a{color:#7cc0ff}
#say{min-height:20px;font-size:13px;line-height:1.7}
#say.err{color:#e6757a}#say.warn{color:#e8c46b}#say.ok{color:#7ddc9b}
</style></head><body>
<h1>مكتبة مئة عينة صوت — @@GEN@@/@@TOTAL@@ مولَّدة</h1>
<div class="sub">@@RULES@@<br>
أصوات الجلسة: <code>@@VOICES@@</code> · نصوص مختلفة: <code>@@TEXTS@@</code> ·
ميزانية الجولة التالتة <code>1633</code> مسموح / <code>1457</code> منشور / عجز <code>176</code> ·
هدف السرعة <code>2.507</code> كلمة/ث. كل «مقيس» في الجدول محسوب من العيّنة نفسها،
مش صفة مكتوبة قبل السماع. <b>اضغط «اختر هذه العينة» على اللي تعجبك</b> — اختيارك بيتكتب في
<code>docs/voice-choice.json</code> داخل المستودع نفسه، فالتوليد الجاي ياخد الصوت ده بالظبط.</div>

<div class="card" id="tok">
  <span class="mut">علشان اختيارك يوصلني: الصق رمز GitHub (صلاحية <code>public_repo</code> بس) —
  الرمز بيتكتب في المتصفح مرة واحدة ومش بيترفع لمكان تاني غير api.github.com.</span>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
    <input id="token" type="password" placeholder="ghp_..." style="flex:1;min-width:220px">
    <button id="saveTok">احفظ الرمز</button>
    <button id="testTok" class="ghost">اختبره</button>
    <button id="dropTok" class="ghost">امسحه</button>
    <a id="tokUrl" href="@@TOKEN_URL@@" target="_blank" rel="noopener">اعمل رمز ↗</a>
  </div>
</div>

<div class="card" id="choice"><span class="mut">لسه ما اخترتشي عينة.</span></div>

<div class="bar">
  <input id="q" placeholder="دوّر في الكلام...">
  <select id="v"><option value="">كل الأصوات</option></select>
  <select id="s"><option value="">كل الحالات</option><option value="generated">مولَّدة</option>
    <option value="pending">في الانتظار</option><option value="failed_silent">صامتة</option></select>
  <span class="mut" id="c">@@COUNT@@</span>
</div>

<table><thead><tr><th>#</th><th>الصوت</th><th>العيّنة</th><th>النص</th><th>كلمة/ث</th><th>مقيس</th><th>اختيار</th></tr></thead>
<tbody id="b"></tbody></table>
<p id="say"></p>

<script type="module">
import { commitTextFile, loadToken, saveToken, forgetToken, checkToken, TOKEN_URL,
         OWNER, REPO, BRANCH } from './github-upload.js';
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const PROJECT = params.get('project') || 'hajj-dream-2108415';
const CHOICE_PATH = 'docs/voice-choice.json';
const LOCAL_KEY = 'prostudio.voice.library.choice';
$('tokUrl').href = TOKEN_URL;
let EMBEDDED = @@ROWS@@;
let ROWS = EMBEDDED;
let current = null, localChoice = null;
try { localChoice = JSON.parse(localStorage.getItem(LOCAL_KEY) || 'null'); } catch {}

const say = (msg, cls) => { const el = $('say'); el.innerHTML = msg || ''; el.className = cls || ''; };

/* The committed manifest wins whenever it is reachable; the embedded copy is the offline fallback. */
async function fresh(){
  const cands = [`voice-library.json?ts=${Date.now()}`,
                 `../docs/voice-library.json?ts=${Date.now()}`,
                 `https://raw.githubusercontent.com/${OWNER}/${REPO}/${encodeURIComponent(BRANCH)}/docs/voice-library.json?ts=${Date.now()}`];
  for (const u of cands) { try { const r = await fetch(u, {cache:'no-store'});
    if (r.ok) { const d = await r.json(); if (d && d.samples) { ROWS = d.samples;
      say(`اتقريت من <code>voice-library.json</code>: ${d.generated}/${d.total_slots} مولَّدة.`, 'ok'); return; } } } catch {} }
  say('المانيفيست الجديد ما وصلش — بوصل نسخة المتصفح المضمّنة.');
}

function esc(t){return String(t==null?'':t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmt(n){return (typeof n === 'number' && isFinite(n)) ? n.toFixed(3) : '—'}
function commandFor(r){return `اختار العينة ${r.id} (صوت ${r.voice_id} · ${fmt(r.words_per_sec)} كلمة/ث · خام بلا فلاتر) project=${PROJECT}`}

const vb = $('v');
[...new Set(ROWS.map(r=>r.voice_id))].forEach(v=>{const o=document.createElement('option');o.textContent=v;vb.appendChild(o)});

function paintChoice(c, where){
  if (!c) { $('choice').innerHTML = '<span class="mut">لسه ما اخترتشي عينة.</span>'; return; }
  $('choice').innerHTML = `<b>العينة المختارة:</b> <code>${esc(c.sample_id)}</code> ·
    صوت <code>${esc(c.voice_id)}</code> · ${esc(c.text||'')} ·
    <span class="mut">${where}${c.branch?' · فرع <code>'+esc(c.branch)+'</code>':''}</span>
    ${c.commit_url?` · <a href="${c.commit_url}" target="_blank" rel="noopener">شوف الـcommit ↗</a>`:''}`;
  current = c.sample_id;
}

async function loadSavedChoice(){
  const cands = [`https://raw.githubusercontent.com/${OWNER}/${REPO}/${encodeURIComponent(BRANCH)}/${CHOICE_PATH}?ts=${Date.now()}`,
                 `${CHOICE_PATH.split('/').pop()}?ts=${Date.now()}`, `../${CHOICE_PATH}?ts=${Date.now()}`];
  for (const u of cands) { try { const r = await fetch(u, {cache:'no-store'});
    if (r.ok) { paintChoice(JSON.parse(await r.text()), 'في المستودع'); return; } } catch {} }
  paintChoice(localChoice, localChoice ? 'في المتصفح — لسه ما بعتّوهش للمستودع' : null);
}

async function pick(row){
  if (row.status !== 'generated') { say('العينة دي لسه ما تولّدتش — اختار من المولَّدة.', 'warn'); return; }
  const payload = { schema: 1, sample_id: row.id, source_catalog: 'docs/voice-library.json',
    voice_id: row.voice_id, text: row.text, words: row.words,
    words_per_sec: row.words_per_sec || null, delivery_measured: row.delivery_measured || null,
    audio: row.audio, sha256: row.sha256 || null, chain: 'anull',
    note: 'خرج أداة الصوت زي ما هو: لا فلتر ولا pitch ولا موسيقى ولا حشو سكون.',
    project: PROJECT, chosen_at: new Date().toISOString(),
    chosen_by: 'docs/voice-library.html', branch: BRANCH,
    how_to_apply: 'scripts/set_voice_choice.py --catalog docs/voice-library.json' };
  const text = JSON.stringify(payload, null, 2) + '\n';
  localStorage.setItem(LOCAL_KEY, JSON.stringify({...payload, commit_url: null}));
  paintChoice(payload, 'محفوظ في المتصفح'); render();
  const token = loadToken();
  if (!token) {
    $('token').focus();
    say('اختارك اتسجّل عندك في المتصفح. يوصلني في المستودع بطريقتين: اكتب رمز GitHub فوق، ' +
        `أو انسخ السطر ده والصقه في المحادثة: <code>${esc(commandFor(row))}</code>
         <br><button class="ghost" id="cp">انسخ</button>`, 'warn');
    const b = $('cp'); if (b) b.onclick = () => navigator.clipboard.writeText(commandFor(row))
      .then(()=>say('اتنسخ.','ok'));
    return;
  }
  say('بكتب الاختيار في المستودع…');
  try {
    const res = await commitTextFile(CHOICE_PATH, text, `voice: ${row.id} (${row.voice_id}) من مكتبة المئة عينة`, token);
    let per = null;
    try { per = await commitTextFile(`library/${PROJECT}/voice-choice.json`, text, `voice: ${row.id} for ${PROJECT}`, token); } catch {}
    payload.commit_url = res.url; localStorage.setItem(LOCAL_KEY, JSON.stringify(payload));
    paintChoice(payload, 'في المستودع'); render();
    say(`<b>وصل الاختيار للمستودع</b> — <code>${row.id}</code>${per?` · اتسجّل في <code>library/${PROJECT}/</code>`:''}
         · <a href="${res.url}" target="_blank" rel="noopener">شوف الـcommit ↗</a><br>
         قولّي «كمّل الدبلجة بالعينة دي» وأنا أبدأ الجولة التالتة بنفس الصوت.`, 'ok');
  } catch (err) { say('ما قدرتش أكتب في المستودع: ' + err.message + ' — الاختيار فاضل في المتصفح.', 'err'); }
}

function render(){
  const q = $('q').value.trim(), v = vb.value, st = $('s').value;
  const b = $('b'); b.innerHTML = ''; let n = 0;
  ROWS.forEach(r => {
    if (v && r.voice_id !== v) return;
    if (st && r.status !== st) return;
    if (q && esc(r.text).indexOf(q) < 0) return;
    n++;
    const tr = document.createElement('tr');
    tr.className = (r.status === 'generated' ? 'gen' : (r.status === 'failed_silent' ? 'bad' : 'pen'))
                 + (r.id === current ? ' sel' : '');
    const a = r.status === 'generated'
      ? `<audio controls preload="none" src="${r.audio}"></audio>` : '<span class="mut">لسه ما تولّدش</span>';
    tr.innerHTML = `<td>${r.id}<br><span class="mut">${esc(r.paragraph)}</span></td>
      <td><code>${esc(r.voice_id)}</code><br><span class="mut">${esc(r.battle_batch||'')}</span></td>
      <td>${a}</td>
      <td>${esc(r.text)}<br><span class="mut">${r.words} كلمة · ${r.sha256?r.sha256.slice(0,12):''}</span></td>
      <td>${fmt(r.words_per_sec)}</td>
      <td>${esc(r.delivery_measured||'—')}</td>
      <td><button class="pick ${r.id===current?'sel':''}" ${r.status==='generated'?'':'disabled'}>${r.id===current?'✓ المختارة':'اختر هذه العينة'}</button>
          <br><button class="ghost">${'انسخ الأمر'}</button></td>`;
    const [pickBtn, copyBtn] = tr.querySelectorAll('button');
    pickBtn.onclick = () => pick(r);
    copyBtn.onclick = () => navigator.clipboard.writeText(commandFor(r)).then(()=>say('اتنسخ.','ok'));
    b.appendChild(tr);
  });
  $('c').textContent = `معرض ${n} · مُولَّدة ${ROWS.filter(r=>r.status==='generated').length}/${ROWS.length}`;
}
['q','s'].forEach(i=>$(i).addEventListener('input',render));
vb.addEventListener('input',render);
$('saveTok').onclick = () => { saveToken($('token').value.trim()); say('الرمز اتحفظ في المتصفح.','ok'); };
$('dropTok').onclick = () => { forgetToken(); $('token').value=''; say('اتمسح.','warn'); };
$('testTok').onclick = async () => { const t = $('token').value.trim() || loadToken();
  if (!t) return say('اكتب الرمز الأول.','warn');
  try { const u = await checkToken(t); say(`الرمز شغال: <code>${esc(u&&u.login||'?')}</code> — يقدر يكتب في <code>${esc(BRANCH)}</code>.`,'ok'); }
  catch (e) { say('الرمز مرفوض: '+e.message,'err'); } };
(async () => { const t = loadToken(); if (t) $('token').value = t;
  await fresh(); await loadSavedChoice(); render();
  const pend = ROWS.filter(r=>r.status==='pending').length;
  if (pend) say(`فيه <b>${pend}</b> خانة لسه بتتولّد (سقف المنصة 10 مقاطع في اللفة) — `
    + 'المولَّد شغال حالًا، والباقي بييجي ورا بعضه.','warn');
})();
</script></body></html>
"""


def write_page(doc: dict) -> None:
    rows = [{k: r.get(k) for k in ("id", "voice_id", "text", "words", "words_per_sec",
                                  "delivery_measured", "voice_verdict", "battle_batch",
                                  "status", "audio", "sha256", "bytes", "paragraph")}
            for r in doc["samples"]]
    html = (PAGE_TEMPLATE
            .replace("@@ROWS@@", json.dumps(rows, ensure_ascii=False))
            .replace("@@TOKEN_URL@@", "https://github.com/settings/tokens/new?scopes=public_repo&amp;description=ProStudio%20voice%20choice")
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
