#!/usr/bin/env python3
"""Browser front door to the dubbing workspace: play the finished dubs, upload a
new source video or an SRT.

Why this exists: the sandbox cannot reach YouTube (TLS blocked), and the user has
a video to hand in. The preview host is the one channel that goes the other way,
so uploads land here and the intake script picks them up.

Routes:
  GET  /               player page + upload form (newest build selected first)
  GET  /<file>         range-aware file serving, so a 40 MB MP4 seeks properly
  GET  /uploads        the intake projects now sitting in library/
  GET  /upload-status  which chunks of a file already arrived (resume support)
  POST /upload-chunk   one slice, raw body; the browser runs six in parallel
  POST /upload-finish  join the slices, digest them, commit and push
  POST /upload         one-shot upload, kept for curl and small files

Uploads go straight into the repository at library/<slug>/ and are committed and
pushed as soon as they are whole. The user asked for exactly that (2026-09-18)
after a scratch-inbox upload was lost to a sandbox reset: only a commit survives.
A source over 95 MB is committed as parts, because GitHub refuses a 100 MB blob;
the assembled copy stays local and is named *.local.* so .gitignore keeps it out.

Chunking is also the answer to the slow upload: the preview proxy limits one
stream, so the browser opens six and resumes whatever already landed.

Raw bodies rather than multipart, because the browser can send a File slice
directly with fetch, and streaming keeps a 600 MB source out of memory.
Names are sanitised and extensions whitelisted: this URL is reachable by anyone
who has it.

Usage: python .preview/serve.py <serve-dir> <port>
       DUB_UPLOAD_NOGIT=1 exercises the intake without touching git.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import mimetypes
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

MAX_UPLOAD = 600 * 1024 * 1024
ALLOWED_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".srt", ".vtt", ".txt",
               ".json", ".mp3", ".wav", ".m4a", ".aac", ".ogg"}

# Uploads land in the repository, not in a scratch inbox: the user asked for the
# video to be moved straight into its own folder here (2026-09-18), and only a
# commit survives the sandbox dying -- inbox/ had already cost one upload.
LIBRARY = ROOT / "library"
BRANCH = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                        capture_output=True, text=True).stdout.strip() or "HEAD"
GIT_LOCK = ROOT / ".cache" / "upload-git.lock"          # .cache/ is gitignored
NOGIT = os.environ.get("DUB_UPLOAD_NOGIT") == "1"       # tests only

# GitHub refuses a blob of 100 MB, so a bigger source is committed as parts and
# the assembled copy stays local (library/*/*.local.* is gitignored).
PART_LIMIT = 95 * 1024 * 1024
CHUNK_MAX = 64 * 1024 * 1024                            # one POSTed chunk


def slugify(raw: str) -> str | None:
    """A folder name for the project: letters, digits, hyphens, Arabic kept."""
    s = re.sub(r"[\s_]+", "-", (raw or "").strip().lower())
    s = re.sub(r"[^\w\-\u0600-\u06ff]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")[:60].strip("-")
    return s or None


def project_dir(slug: str) -> Path:
    return LIBRARY / slug


def chunks_dir(slug: str, name: str) -> Path:
    return project_dir(slug) / ".parts" / name


def sha256_file(path: Path, block: int = 1 << 21) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(paths: list[Path], message: str) -> dict:
    """add + commit + push, under a lock so a slow upload cannot collide with an
    agent-side commit. Only the named paths are committed: a 200 MB source must
    never be swept in by an unrelated `git add -A` elsewhere. Failure is
    reported, never raised -- bytes already on disk are worth more than a push."""
    if NOGIT:
        return {"state": "skipped", "why": "DUB_UPLOAD_NOGIT=1"}
    GIT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    rel = [str(p.relative_to(ROOT)) for p in paths]
    try:
        with GIT_LOCK.open("w") as lock:
            lock.write(str(os.getpid()))
            for step in (["git", "add", "--", *rel],
                         ["git", "commit", "-q", "-m", message, "--", *rel],
                         ["git", "push", "-q", "origin", BRANCH]):
                r = subprocess.run(step, cwd=ROOT, capture_output=True, text=True,
                                   timeout=1800)
                out = (r.stdout + r.stderr).strip()
                if r.returncode and "nothing to commit" not in out:
                    return {"state": "failed", "step": step[1], "error": out[:400]}
            rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip()
        return {"state": "committed", "commit": rev, "branch": BRANCH}
    except subprocess.TimeoutExpired:
        return {"state": "failed", "step": "timeout", "error": "جاوز 1800 ث"}
    except OSError as exc:
        return {"state": "failed", "step": "lock", "error": str(exc)[:200]}
    finally:
        GIT_LOCK.unlink(missing_ok=True)


def assemble(slug: str, name: str) -> dict:
    """Join the posted chunks into the project folder, then commit them.

    Layout depends on size, because GitHub rejects a 100 MB blob:
      <= 95 MB   library/<slug>/<name>                     committed as one file
      >  95 MB   library/<slug>/parts/<name>.part-NNN      committed as parts
                 library/<slug>/<stem>.local<ext>          assembled, gitignored
    INTAKE.json records which layout, the digest, and what to feed ffmpeg.
    """
    cdir = chunks_dir(slug, name)
    parts = sorted(cdir.glob("chunk-*"), key=lambda p: int(p.name.split("-")[1]))
    if not parts:
        return {"ok": False, "error": "لا مقاطع مرفوعة"}
    gap = [i for i in range(int(parts[-1].name.split("-")[1]) + 1)
           if not (cdir / f"chunk-{i:06d}").is_file()]
    if gap:
        return {"ok": False, "error": f"مقاطع ناقصة: {gap[:8]}{'…' if len(gap) > 8 else ''}",
                "missing": gap}

    dest_dir = project_dir(slug)
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = sum(p.stat().st_size for p in parts)
    dest = dest_dir / name
    t0 = time.time()
    with dest.open("wb") as out:
        for p in parts:
            with p.open("rb") as fh:
                shutil.copyfileobj(fh, out, 1 << 22)
    joined_s = time.time() - t0
    shutil.rmtree(cdir, ignore_errors=True)
    try:
        cdir.parent.rmdir()                 # .parts/ once nothing else is in it
    except OSError:
        pass

    digest = sha256_file(dest)
    big = total > PART_LIMIT
    git_paths: list[Path] = []
    if big:
        pdir = dest_dir / "parts"
        pdir.mkdir(exist_ok=True)
        stem, ext = dest.stem, dest.suffix
        local = dest_dir / f"{stem}.local{ext}"
        n = 0
        with dest.open("rb") as src, local.open("wb") as keep:
            while True:
                block = src.read(PART_LIMIT)
                if not block:
                    break
                part = pdir / f"{name}.part-{n:03d}"
                part.write_bytes(block)
                keep.write(block)
                git_paths.append(part)
                n += 1
        dest.unlink()                       # the single big file cannot be pushed
        fed = local
        layout = f"parts×{n}"
    else:
        git_paths.append(dest)
        fed = dest
        layout = "single"

    intake = {
        "slug": slug, "name": name, "bytes": total, "sha256": digest,
        "chunks": len(parts), "layout": layout, "uploaded_at":
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fed_to_pipeline": str(fed.relative_to(ROOT)),
        "joined_in_s": round(joined_s, 2),
        "git": {"state": "running"},
    }
    manifest = dest_dir / "INTAKE.json"
    manifest.write_text(json.dumps(intake, ensure_ascii=False, indent=2) + "\n", "utf-8")
    git_paths.append(manifest)

    def push() -> None:
        res = git_commit(git_paths, f"Take in {slug}: {name} ({total} bytes, sha256 {digest[:12]})")
        intake["git"] = res
        manifest.write_text(json.dumps(intake, ensure_ascii=False, indent=2) + "\n", "utf-8")

    threading.Thread(target=push, daemon=True, name=f"git-{slug}").start()
    return {"ok": True, "slug": slug, "path": str(fed.relative_to(ROOT)),
            "bytes": total, "sha256": digest, "layout": layout,
            "joined_in_s": round(joined_s, 2), "git": "يعمل في الخلفية"}

# Curated, not discovered: dubs/ holds 44 mp4s and most are historical or
# explicitly rejected (das-full/part1 is rejected forever), so a glob would bury
# the deliverable and invite playing a rejected cut. Newest work first.
FEATURED = [
    {"path": "dubs/doctor-lecture/matched/final-dub-doctor-lecture-full.mp4",
     "label": "doctor-lecture — الفيلم الكامل 19:20 مدبلَّجاً (49 مجموعة)",
     "note": "voice-01 · كل الحدود على سكتات حقيقية · سرعة 1.02-1.42 · أقصي تجاوز 0.0 ث · atempo فقط ثم دمج، بلا أي معالجة أخرى · 19:20.24 مقابل أصل 19:20.30",
     "compare": "library/doctor-lecture/source.mp4"},
    {"path": "dubs/doctor-lecture/matched/final-dub-doctor-lecture-pilot-pause.mp4",
     "label": "doctor-lecture — التجربة المُصلَحة: حدود على السكتات (72 ث)",
     "note": "3 مجموعات · voice-01 · كل حدّ داخل سكتة حقيقية · نَفَس 0.15-0.33 ث بين المقاطع · سرعة 1.21-1.33",
     "compare": "library/doctor-lecture/source.mp4"},
    {"path": "dubs/doctor-lecture/matched/final-dub-doctor-lecture-pilot.mp4",
     "label": "doctor-lecture — التجربة السابقة (حدود في منتصف العبارة)",
     "note": "للمقارنة فقط: الفجوة بين المجموعات كانت 0.010 ث فتُقطع الكلمة («بقى لها» ⟂ «وقت طويل»)",
     "compare": "library/doctor-lecture/source.mp4"},
    {"path": "library/doctor-lecture/source.mp4",
     "label": "doctor-lecture — الأصل 19:20 (للمقارنة)",
     "note": "الأصل كما وصل بالرفع · 854×480 · sha256 684188ac…"},
    {"path": "dubs/das-full/matched/final-dub-das-voice00-0-23-fixed.mp4",
     "label": "das-full — 9:24 بالنص المصحَّح",
     "note": "24 مجموعة · voice-00 · النص المصحَّح groups-v3 · مزامنة على توقيت الأصل",
     "compare": "library/das-full/source.mp4"},
    {"path": "library/das-full/source.mp4",
     "label": "das-full — الأصل 52:22 (للمقارنة)",
     "note": "الأصل الكامل المستعاد بالبصمة"},
]


# Subtitle files worth handing to the user, newest/most useful first.
SUBTITLES = [
    ("dubs/doctor-lecture/matched/final-dub-doctor-lecture-full.srt",
     "doctor-lecture — ترجمة الفيلم الكامل المدبلَج (49 cue) · مطابقة لمواضع الصوت الفعلية"),
    ("library/doctor-lecture/youtube/kOrz0WAb2P8.groups.srt",
     "doctor-lecture — SRT عباري (49 cue) · كلمات يوتيوب × أزمنة مقاسة من الصوت · النص المصحَّح"),
    ("library/doctor-lecture/youtube/kOrz0WAb2P8.spoken-fixed.srt",
     "doctor-lecture — SRT على مستوى الكلمة (2880 cue) · أزمنة مُصلَحة"),
    ("library/doctor-lecture/asr-full.srt",
     "doctor-lecture — تفريغ الصوت الخام (236 مقطعاً) · للتوقيت فقط، ليس مصدر كلمات"),
    ("dubs/doctor-lecture/matched/final-dub-doctor-lecture-pilot-pause.srt",
     "doctor-lecture — ترجمة التجربة المُصلَحة (72 ث)"),
    ("library/das-full/youtube/NB2YcTh_L6k.spoken-fixed.srt",
     "das-full — SRT على مستوى الكلمة · النص المعتمد"),
]


def page() -> str:
    live = []
    for f in FEATURED:
        if not (ROOT / f["path"]).is_file():
            continue
        f = dict(f)
        # das-full's original is restored on demand and is not on disk now, so its
        # A/B pair is absent: blank it rather than show a button that 404s.
        if f.get("compare") and not (ROOT / f["compare"]).is_file():
            f["compare"] = ""
        live.append(f)
    main = live[0]["path"] if live else ""
    main_cmp = live[0].get("compare", "") if live else ""
    note = live[0]["note"] if live else ""
    opts = "".join(
        f'<option value="{f["path"]}" data-cmp="{f.get("compare", "")}"'
        f' data-note="{f["note"]}"{" selected" if f["path"] == main else ""}>'
        f'{f["label"]} — {(ROOT / f["path"]).stat().st_size // 1048576} م.ب</option>'
        for f in live
    )
    subs = "".join(
        f'<a class="dl" style="background:#1f6feb;display:block;margin:6px 0" href="{path}" download>'
        f'تنزيل</a><div class="sub" style="margin:-2px 0 10px">{label}'
        f'<div class="mono">{path} · {(ROOT / path).stat().st_size // 1024} ك.ب</div></div>'
        for path, label in SUBTITLES if (ROOT / path).is_file()
    ) or '<div class="sub">لا توجد ملفات ترجمة بعد.</div>'
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>الدبلجة — تشغيل ورفع</title>
<style>
 body{{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.7 system-ui,'Segoe UI',Tahoma,sans-serif}}
 .wrap{{max-width:1000px;margin:0 auto;padding:18px}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:17px;margin:18px 0 8px}}
 .sub{{color:#8b949e;font-size:13px;margin-bottom:14px}}
 video{{width:100%;background:#000;border-radius:10px}}
 .bar{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}
 select,input[type=file]{{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:7px 10px;font-size:14px}}
 a.dl,button.go{{background:#238636;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer}}
 button.go:disabled{{background:#30363d;cursor:default}}
 .chaps{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 18px}}
 .chaps button{{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:6px 10px;cursor:pointer;text-align:right;font-size:13px}}
 .chaps button:hover{{border-color:#58a6ff}}
 .chaps small{{display:block;color:#8b949e;font-size:11px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px 14px;margin-bottom:10px}}
 .card b{{color:#58a6ff}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 td,th{{padding:5px 8px;border-bottom:1px solid #21262d;text-align:right}}
 th{{color:#8b949e;font-weight:600}}
 .ok{{color:#3fb950}} .warn{{color:#d29922}} .err{{color:#f85149}}
 progress{{width:100%;height:14px}}
 .mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#8b949e;word-break:break-all}}
</style>
</head>
<body>
<div class="wrap">

 <h2>١ — ارفع الفيديو الجديد أو ملف الترجمة</h2>
 <div class="card">
  <div class="bar">
   <input type="file" id="pick" accept=".mp4,.webm,.mov,.mkv,.m4v,.srt,.vtt,.txt,.json,.mp3,.wav,.m4a">
   <input type="text" id="slug" placeholder="اسم المجلد (اختياري)" style="width:190px;background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:7px 10px">
   <button class="go" id="up" onclick="send()">رفع</button>
  </div>
  <progress id="prog" value="0" max="100" style="display:none"></progress>
  <div id="msg" class="mono"></div>
  <div class="sub">يصل الملف <b>مباشرة إلى المستودع</b> في مجلده الخاص <span class="mono">library/&lt;الاسم&gt;/</span>
  ويُدفَع فور اكتماله — لا صندوق مؤقت يضيع مع انقطاع الـsandbox.
  يُرفع مقاطع ٨ م.ب على <b>٦ اتصالات متوازية</b>، ويستأنف ما وصل إن انقطع. الحد {MAX_UPLOAD // 1048576} م.ب.
  ما فوق ٩٥ م.ب يُحفَظ أجزاءً (حدّ GitHub للملف الواحد) مع بصمة sha256 في <span class="mono">INTAKE.json</span>.</div>
  <div id="have"></div>
 </div>

 <h2>٢ — النسخة المدبلجة</h2>
 <div class="sub" id="note">{note}</div>
 <div class="bar">
  <select id="f" onchange="load(this.value)">{opts}</select>
  <button class="go" id="ab" onclick="ab()" style="display:{'inline-block' if main_cmp else 'none'}">الأصل ⇄ النسخة عند نفس اللحظة</button>
  <a class="dl" id="dl" href="{main}" download>تنزيل الملف</a>
 </div>
 <video id="v" controls preload="metadata" src="{main}"></video>
 <div class="chaps" id="chaps"></div>

 <div class="card" id="facts"></div>

 <h2>٣ — ملفات الترجمة (SRT)</h2>
 <div class="sub">ملف يوتيوب المُوقَّت نفسه غير قابل للجلب (يحتاج توقيع المشغّل، ويوتيوب يحجب
 الطرفيات العامة) — لذلك هذه الملفات مبنية بالطريقة المعتمدة: <b>الكلمات من نص يوتيوب</b>
 و<b>الأزمنة مقاسة من الصوت الذي رفعتَه</b>.</div>
 {subs}

 <h2>٤ — الصوت المقفل لدبلجة الأفلام</h2>
 <div class="sub">قرارك 2026-09-17: هذا الصوت لكل دبلجات الأفلام. المرجع الدائم هو <b>هذا الملف
 الصوتي</b> لا اسم المعرّف، لأن <span class="mono">voice_id</span> مرتبط بالجلسة التي اختير فيها
 ولا يعمل في جلسة لاحقة. <b>الأذن هي الحكم</b>: لا بوابة آلية موثوقة — قِسنا سبع ميزات طيفية على
 1176 زوجًا من الصوت نفسه و1568 زوجًا من صوت مختلف، فأفضل هامش 0.55× وخطأ متساوٍ 5.1٪.</div>
 <audio controls preload="none" src="library/voices/film-dub-narrator/voice-film-bank.mp3"
        style="width:100%"></audio>
 <div class="sub">البنك المرجعي 1:58 — المجموعات 0 و12 و24 و47 من الفيلم ·
  <a class="dl" href="library/voices/film-dub-narrator/voice-film-bank.mp3" download>تنزيل</a> ·
  الإيقاع المقاس <b>9.21 حرف/ث</b> (8.19–10.89) · القفل
  <span class="mono">library/voices/film-dub-narrator/LOCKED_VOICE.json</span></div>
 <div class="sub">لمقارنة عيّنة جديدة به: <span class="mono">python scripts/verify_voice.py --ab
  &lt;الملف&gt;</span> فيُنشأ ملف فيه البنك ثم العيّنة، ويُسمع من <span class="mono">/ab/</span>.</div>
</div>
<script>
 var CMP='', DUB='';
 function fmt(s){{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+('0'+(s%60)).slice(-2);}}
 function setSrc(p,t){{var v=document.getElementById('v');v.src=p;v.load();
   v.addEventListener('loadedmetadata',function once(){{if(t!=null)v.currentTime=t;v.play();
     v.removeEventListener('loadedmetadata',once);}});
   document.getElementById('dl').href=p;facts(p);}}
 function load(n){{var sel=document.getElementById('f'),o=sel.options[sel.selectedIndex];
   DUB=n;CMP=o.getAttribute('data-cmp')||'';
   document.getElementById('note').textContent=o.getAttribute('data-note')||'';
   document.getElementById('ab').style.display=CMP?'inline-block':'none';
   setSrc(n,null);}}
 function ab(){{var v=document.getElementById('v');if(!CMP)return;
   setSrc(v.currentSrc.indexOf(DUB)>=0?CMP:DUB,v.currentTime);}}
 function chapters(){{var v=document.getElementById('v'),d=v.duration,box=document.getElementById('chaps');
   if(!isFinite(d)||d<=1)return;box.innerHTML='';
   [[0,'البداية'],[0.25,'٢٥٪'],[0.5,'المنتصف'],[0.75,'٧٥٪'],[0.97,'النهاية']].forEach(function(p){{
     var b=document.createElement('button');b.innerHTML=fmt(d*p[0])+'<small>'+p[1]+'</small>';
     b.onclick=function(){{v.currentTime=d*p[0];v.play();}};box.appendChild(b);}});}}
 function row(k,val,cls){{cls=cls||'ok';return '<tr><td>'+k+'</td><td>'+val+'</td><td class="'+cls+'">'+
   (cls==='warn'?'انتبه':'سليم')+'</td></tr>';}}
 function facts(p){{var box=document.getElementById('facts'),rp=p.replace(/\.mp4$/,'.report.json');
   box.innerHTML='<b>تقرير البناء</b><div class="mono">'+rp+'</div>';
   fetch(rp).then(function(r){{return r.ok?r.json():null;}}).then(function(d){{
     if(!d){{box.innerHTML+='<div class="sub">لا يوجد تقرير بناء لهذا الملف — فهو أصل، أو نسخة سابقة لنظام التقارير.</div>';return;}}
     var miss=(d.groups_missing_takes||[]);
     var t='<table><tr><th>البند</th><th>القيمة</th><th>الحكم</th></tr>';
     t+=row('مدة النسخة',fmt(d.video_seconds)+' ('+Math.round(d.video_seconds||0)+' ث)');
     t+=row('الصوت',d.voice_id||'—');
     t+=row('الإيقاع',d.pacing||'—');
     t+=row('المجموعات',(d.groups_placed||0)+' موضوعة من '+(d.groups_selected||0)+' مختارة'+
       (miss.length?' · بلا تسجيل: '+miss.join('،'):''),miss.length?'warn':'ok');
     t+=row('تغطية كلام الأصل',((d.coverage_ratio||0)*100).toFixed(2)+'%',(d.coverage_ratio||0)>=0.99?'ok':'warn');
     t+=row('سرعة الكلام',(d.tempo_min||0)+'x – '+(d.tempo_max||0)+'x (السقف 1.60)',(d.tempo_max||0)<=1.35?'ok':'warn');
     t+=row('تجاوز عن النوافذ',(d.max_overrun_s||0).toFixed(2)+' ث',(d.max_overrun_s||0)<=0.05?'ok':'warn');
     t+=row('أثر الراوي الأصلي','corr '+(d.corr_original_vocals||0)+' (الحد 0.08)',(d.corr_original_vocals||0)<=0.08?'ok':'warn');
     t+=row('مسار الصوت',(d.audio_streams||1)+' مسار',(d.audio_streams||1)===1?'ok':'warn');
     box.innerHTML+='<br>'+t;
   }}).catch(function(){{box.innerHTML+='<div class="err">تعذّر قراءة التقرير</div>';}});}}
 function boot(){{var v=document.getElementById('v'),sel=document.getElementById('f');
   v.addEventListener('loadedmetadata',chapters);
   if(sel.value){{DUB=sel.value;CMP=sel.options[sel.selectedIndex].getAttribute('data-cmp')||'';facts(DUB);}}}}
 function list(){{fetch('/uploads').then(r=>r.json()).then(d=>{{
   var ps=d.projects||[];
   document.getElementById('have').innerHTML = ps.length
     ? '<b>في المستودع:</b><div class="mono">'+ps.map(function(p){{
         var g=(p.intake&&p.intake.git)||{{}}, sz=(p.intake&&p.intake.bytes)||0;
         var st=g.state==='committed'?'<span class="ok">مدفوع '+(g.commit||'')+'</span>'
               :(g.state==='failed'?'<span class="err">فشل الدفع: '+(g.error||'')+'</span>'
               :(g.state==='running'?'<span class="warn">يُدفَع الآن…</span>':'<span class="sub">غير مدفوع</span>'));
         return p.slug+'/ — '+(sz/1048576).toFixed(1)+' م.ب · '+st;}}).join('<br>')+'</div>'
     : '<span class="sub">لم يصل شيء بعد.</span>';}});}}
 var CHUNK=8*1048576, PAR=6;
 function slugOf(n){{return n.replace(/\\.[^.]+$/,'').toLowerCase().replace(/[\\s_]+/g,'-')
   .replace(/[^\\w\\-\\u0600-\\u06ff]/g,'').replace(/-{{2,}}/g,'-').slice(0,60);}}
 function postJSON(url,body){{return fetch(url,{{method:'POST',body:body}}).then(function(r){{return r.json();}});}}
 function send(){{
   var inp=document.getElementById('pick'), msg=document.getElementById('msg'),
       btn=document.getElementById('up'), pr=document.getElementById('prog'),
       sl=document.getElementById('slug');
   if(!inp.files.length){{msg.innerHTML='<span class="err">اختر ملفاً أولاً</span>';return;}}
   var f=inp.files[0];
   var slug=(sl.value||'').trim()||slugOf(f.name);
   if(!slug){{msg.innerHTML='<span class="err">اسم المجلد غير صالح</span>';return;}}
   var qs='slug='+encodeURIComponent(slug)+'&name='+encodeURIComponent(f.name);
   var n=Math.ceil(f.size/CHUNK), done=0, sent=0, failed=0, i=0, active=0, t0=Date.now();
   btn.disabled=true; pr.style.display='block'; pr.value=0;
   msg.textContent='يرفع '+f.name+' → library/'+slug+'/ ('+(f.size/1048576).toFixed(1)+' م.ب، '+n+' مقطعًا)…';
   fetch('/upload-status?'+qs).then(function(r){{return r.json();}}).then(function(st){{
     var have=(st&&st.chunks_have)||{{}}, skip=0;
     for(var k in have){{ if(have[k]>0){{ done++; sent+=have[k]; skip++; }} }}
     if(skip) msg.textContent='استئناف: '+skip+' مقطعًا ('+(sent/1048576).toFixed(1)+' م.ب) وصلت سابقًا…';
     pump();
   }}).catch(pump);
   function pump(){{ while(active<PAR && i<n) start(i++); if(done>=n && active===0) finish(); }}
   function start(idx){{
     active++;
     postJSON('/upload-chunk?'+qs+'&index='+idx, f.slice(idx*CHUNK, Math.min(f.size,(idx+1)*CHUNK)))
       .then(function(r){{ active--; if(r&&r.ok){{done++;sent+=r.bytes;}} else failed++; tick(); pump(); }})
       .catch(function(){{ active--; failed++; tick(); pump(); }});
   }}
   function tick(){{
     pr.value=Math.round(sent/f.size*100);
     var s=(Date.now()-t0)/1000, rate=s>1?(sent/1048576/s):0;
     msg.textContent='library/'+slug+'/ ← '+f.name+' · '+(sent/1048576).toFixed(1)+' من '+(f.size/1048576).toFixed(1)
       +' م.ب · '+rate.toFixed(1)+' م.ب/ث · '+done+'/'+n+' مقطعًا'+(failed?' · '+failed+' فشل':'');
   }}
   function finish(){{
     if(done<n){{ btn.disabled=false;
       msg.innerHTML='<span class="err">✗ وصل '+done+' من '+n+' مقطعًا — اضغط «رفع» ثانيةً ليستأنف</span>'; return; }}
     pr.value=100; msg.textContent='اكتمل النقل · يُجمَّع وتُحسب البصمة ويُدفَع إلى المستودع…';
     postJSON('/upload-finish?'+qs,'').then(function(r){{
       btn.disabled=false;
       msg.innerHTML = (r&&r.ok)
         ? '<span class="ok">✓ في المستودع: '+r.path+' · '+(r.bytes/1048576).toFixed(1)+' م.ب · '+r.layout
           +' · sha256 '+r.sha256.slice(0,16)+'… · جُمع في '+r.joined_in_s+'ث · الدفع يعمل في الخلفية</span>'
         : '<span class="err">✗ '+((r&&r.error)||'فشل التجميع')+'</span>';
       list();
     }}).catch(function(){{ btn.disabled=false; msg.innerHTML='<span class="err">✗ فشل الاتصال عند التجميع</span>'; }});
   }}
 }}
 setInterval(list, 5000);
 list();boot();
</script>
</body>
</html>"""


def safe_name(raw: str) -> str | None:
    name = Path(urllib.parse.unquote(raw or "")).name.strip()
    name = re.sub(r"[^\w.\-()\u0600-\u06ff ]+", "_", name)[:120]
    if not name or name.startswith("."):
        return None
    return name if Path(name).suffix.lower() in ALLOWED_EXT else None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "DubPreview/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        query = urllib.parse.parse_qs(qs)
        if path == "/uploads":
            projects = []
            if LIBRARY.is_dir():
                for d in sorted(LIBRARY.iterdir(), key=lambda p: -p.stat().st_mtime):
                    if not d.is_dir() or d.name.startswith("."):
                        continue
                    man = d / "INTAKE.json"
                    if not man.is_file():
                        continue          # a library folder, not an intake target
                    try:
                        intake = json.loads(man.read_text("utf-8"))
                    except (OSError, ValueError):
                        intake = None
                    projects.append({"slug": d.name, "intake": intake,
                                     "files": sorted((p.name for p in d.rglob("*")
                                                      if p.is_file()), key=len)[:6]})
            return self._json({"dir": str(LIBRARY), "projects": projects,
                               "branch": BRANCH})
        if path == "/upload-status":
            slug = slugify((query.get("slug") or [""])[0])
            name = safe_name((query.get("name") or [""])[0])
            if not slug or not name:
                return self._json({"ok": False, "error": "slug أو اسم غير صالح"}, 400)
            cdir = chunks_dir(slug, name)
            have = ({int(p.name.split("-")[1]): p.stat().st_size
                     for p in cdir.glob("chunk-*")} if cdir.is_dir() else {})
            return self._json({"ok": True, "slug": slug, "name": name,
                               "chunks_have": have, "bytes_have": sum(have.values())})
        self._serve()

    def do_POST(self):
        path, _, qs = self.path.partition("?")
        query = urllib.parse.parse_qs(qs)
        if path not in ("/upload", "/upload-chunk", "/upload-finish"):
            return self._json({"ok": False, "error": "unknown route"}, 404)
        name = safe_name((query.get("name") or [""])[0])
        if not name:
            return self._json({"ok": False, "error": "اسم أو امتداد غير مسموح"}, 400)
        slug = slugify((query.get("slug") or [""])[0]) or slugify(Path(name).stem) or "upload"

        if path == "/upload-finish":
            self._drain()
            res = assemble(slug, name)
            return self._json(res, 200 if res.get("ok") else 400)

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "error": "لا محتوى"}, 400)
        limit = CHUNK_MAX if path == "/upload-chunk" else MAX_UPLOAD
        if length > limit:
            return self._json({"ok": False, "error": f"أكبر من {limit // 1048576} م.ب"}, 413)

        index = 0
        if path == "/upload-chunk":
            try:
                index = int((query.get("index") or ["-1"])[0])
            except ValueError:
                index = -1
            if index < 0:
                return self._json({"ok": False, "error": "index مطلوب (0..n-1)"}, 400)

        cdir = chunks_dir(slug, name)
        cdir.mkdir(parents=True, exist_ok=True)
        final = cdir / f"chunk-{index:06d}"
        tmp = final.with_suffix(".receiving")
        written = 0
        try:
            with tmp.open("wb") as fh:
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(1 << 21, left))
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    left -= len(chunk)
            if written != length:
                tmp.unlink(missing_ok=True)
                return self._json({"ok": False, "error": f"وصل {written} من {length} بايت"}, 400)
            tmp.replace(final)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return self._json({"ok": False, "error": str(exc)[:200]}, 500)
        if path == "/upload-chunk":
            return self._json({"ok": True, "index": index, "bytes": written})
        res = assemble(slug, name)          # single-shot /upload, kept for curl
        return self._json(res, 200 if res.get("ok") else 400)

    def _drain(self) -> None:
        """A body we are not going to read must still be consumed, or the next
        request on this keep-alive connection reads our reply as its own."""
        try:
            left = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            left = 0
        while left > 0:
            block = self.rfile.read(min(1 << 20, left))
            if not block:
                return
            left -= len(block)

    def _serve(self, send_body: bool = True) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/", "/index.html"):
            body = page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        target = (ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT)) or not target.is_file():
            self.send_error(404, "not found")
            return

        size = target.stat().st_size
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and "=" in rng:
            unit, spec = rng.split("=", 1)
            if unit.strip().lower() == "bytes":
                try:
                    a, b = spec.split("-", 1)
                    if a.strip():
                        start = int(a)
                        end = int(b) if b.strip() else size - 1
                    elif b.strip():
                        start, end = size - int(b), size - 1
                    status = 206
                except ValueError:
                    start, end, status = 0, size - 1, 200
        start = max(0, min(start, size - 1 if size else 0))
        end = max(start, min(end, size - 1))
        length = end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body:
            return

        with target.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(131072, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"serving {ROOT} on 0.0.0.0:{PORT} · uploads → {LIBRARY}/<slug>/ "
          f"· git push origin {BRANCH}"
          f"{' (معطَّل للاختبار: DUB_UPLOAD_NOGIT=1)' if NOGIT else ''}", flush=True)
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()
