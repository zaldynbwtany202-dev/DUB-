#!/usr/bin/env python3
"""Browser front door to the dubbing workspace: play the finished dubs, upload a
new source video or an SRT.

Why this exists: the sandbox cannot reach YouTube (TLS blocked), and the user has
a video to hand in. The preview host is the one channel that goes the other way,
so uploads land here and the intake script picks them up.

Two jobs, one server:
  GET  /            player page + upload form (newest build selected by default)
  GET  /<file>      range-aware file serving, so a 40 MB MP4 seeks properly
  POST /upload?name=<filename>   raw body streamed to the inbox directory

Uploads use a raw body rather than multipart because the browser can send a File
directly with fetch/XHR, and streaming keeps a 150 MB source out of memory.
Names are sanitised and extensions whitelisted: this URL is reachable by anyone
who has it.

Usage: python .preview/serve.py <serve-dir> <port> [upload-dir]
"""

from __future__ import annotations

import http.server
import json
import mimetypes
import re
import socketserver
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
UPLOAD_DIR = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else ROOT

MAX_UPLOAD = 600 * 1024 * 1024
ALLOWED_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".srt", ".vtt", ".txt",
               ".json", ".mp3", ".wav", ".m4a", ".aac", ".ogg"}

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
   <button class="go" id="up" onclick="send()">رفع</button>
  </div>
  <progress id="prog" value="0" max="100" style="display:none"></progress>
  <div id="msg" class="mono"></div>
  <div class="sub">يصل الملف إلى <span class="mono">{UPLOAD_DIR}</span> داخل الـsandbox. الحد {MAX_UPLOAD // 1048576} م.ب.
  الامتدادات المسموحة: فيديو، srt/vtt، txt/json، صوت.</div>
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
   document.getElementById('have').innerHTML = d.files.length
     ? '<b>وصل حتى الآن:</b><div class="mono">'+d.files.map(f=>f.name+' — '+(f.bytes/1048576).toFixed(1)+' م.ب').join('<br>')+'</div>'
     : '<span class="sub">لم يصل شيء بعد.</span>';}});}}
 function send(){{
   var inp=document.getElementById('pick'), msg=document.getElementById('msg'),
       btn=document.getElementById('up'), pr=document.getElementById('prog');
   if(!inp.files.length){{msg.innerHTML='<span class="err">اختر ملفاً أولاً</span>';return;}}
   var f=inp.files[0], x=new XMLHttpRequest();
   btn.disabled=true; pr.style.display='block'; pr.value=0;
   msg.textContent='يرفع '+f.name+' ('+(f.size/1048576).toFixed(1)+' م.ب)…';
   x.open('POST','/upload?name='+encodeURIComponent(f.name));
   x.upload.onprogress=function(e){{if(e.lengthComputable) pr.value=Math.round(e.loaded/e.total*100);}};
   x.onload=function(){{
     btn.disabled=false;
     try{{var r=JSON.parse(x.responseText);
       msg.innerHTML = r.ok ? '<span class="ok">✓ وصل: '+r.path+' ('+(r.bytes/1048576).toFixed(1)+' م.ب)</span>'
                             : '<span class="err">✗ '+r.error+'</span>';}}
     catch(e){{msg.innerHTML='<span class="err">✗ '+x.status+'</span>';}}
     list();}};
   x.onerror=function(){{btn.disabled=false; msg.innerHTML='<span class="err">✗ فشل الاتصال</span>';}};
   x.send(f);}}
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
        path = self.path.split("?", 1)[0]
        if path == "/uploads":
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            files = sorted(
                ({"name": p.name, "bytes": p.stat().st_size,
                  "mtime": int(p.stat().st_mtime)}
                 for p in UPLOAD_DIR.iterdir() if p.is_file() and not p.name.startswith(".")),
                key=lambda d: -d["mtime"])
            return self._json({"dir": str(UPLOAD_DIR), "files": files})
        self._serve()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/upload":
            return self._json({"ok": False, "error": "unknown route"}, 404)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = safe_name((query.get("name") or ["upload.bin"])[0])
        if not name:
            return self._json({"ok": False, "error": "اسم أو امتداد غير مسموح"}, 400)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "error": "لا محتوى"}, 400)
        if length > MAX_UPLOAD:
            return self._json({"ok": False, "error": f"أكبر من {MAX_UPLOAD // 1048576} م.ب"}, 413)

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / name
        tmp = dest.with_suffix(dest.suffix + ".part")
        written = 0
        try:
            with tmp.open("wb") as fh:
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    left -= len(chunk)
            if written != length:
                tmp.unlink(missing_ok=True)
                return self._json({"ok": False, "error": f"وصل {written} من {length} بايت"}, 400)
            tmp.replace(dest)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return self._json({"ok": False, "error": str(exc)}, 500)
        return self._json({"ok": True, "path": str(dest), "name": dest.name, "bytes": written})

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
    print(f"serving {ROOT} on 0.0.0.0:{PORT} · uploads → {UPLOAD_DIR}", flush=True)
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()
