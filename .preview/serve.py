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

CHAPTERS = [
    ("0:00", 0.2, "بداية السرد — أهدأ مقطع (1.17x)"),
    ("0:48", 47.9, "أسرع مقطع في النسخة (1.48x)"),
    ("2:23", 143.0, "مقطع سريع (1.36x)"),
    ("3:58", 237.9, "مقطع سريع (1.36x)"),
    ("5:00", 300.0, "منتصف النسخة"),
    ("7:00", 420.0, "الدقيقة السابعة"),
    ("9:00", 540.0, "النهاية — 9:24"),
]


def page() -> str:
    vids = sorted(ROOT.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    main = vids[0].name if vids else ""
    opts = "".join(
        f'<option value="{p.name}"{" selected" if p.name == main else ""}>{p.name}'
        f" — {p.stat().st_size // 1048576} م.ب</option>"
        for p in vids
    )
    chap = "".join(
        f'<button onclick="j({t})">{lab}<small>{note}</small></button>'
        for lab, t, note in CHAPTERS
    )
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

 <h2>٢ — النسخة المدبلجة الجاهزة</h2>
 <div class="sub">24 مجموعة · صوت واحد <b>voice-00</b> · مزامنة على توقيت الفيديو الأصلي · صفر تجاوز · النص المصحَّح <b>groups-v3</b></div>
 <div class="bar">
  <select id="f" onchange="load(this.value)">{opts}</select>
  <a class="dl" id="dl" href="{main}" download>تنزيل الملف</a>
 </div>
 <video id="v" controls preload="metadata" src="{main}"></video>
 <div class="chaps">{chap}</div>

 <div class="card">
  <b>ماذا تفحص</b>
  <table>
   <tr><th>البند</th><th>القيمة</th><th>الحكم</th></tr>
   <tr><td>مدة النسخة</td><td>9:24 (564 ث)</td><td class="ok">سليم</td></tr>
   <tr><td>أسماء الشخصيات</td><td>موحَّدة: داس · الانا · مول · تور · الفاس · التيران</td><td class="ok">أُصلح «دس» و«الانه» و«التران»</td></tr>
   <tr><td>تغطية كلام الأصل</td><td>99.64%</td><td class="ok">سليم</td></tr>
   <tr><td>تجاوز عن النوافذ</td><td>0.00 ث</td><td class="ok">سليم</td></tr>
   <tr><td>أثر الراوي الأصلي</td><td>corr 0.0023 (الحد 0.08)</td><td class="ok">لا شيء</td></tr>
   <tr><td>سرعة الكلام</td><td>1.17x – 1.48x (وسط 1.30x)</td><td class="warn">أسرع من الطبيعي — ثمن إيقاع المطابقة</td></tr>
   <tr><td>موسيقى/مؤثرات الأصل</td><td>غير مضمّنة</td><td class="warn">قرار لم يُتخذ — يمكن إضافتها</td></tr>
   <tr><td>المسارات</td><td>1 فيديو + 1 صوت</td><td class="ok">سليم</td></tr>
  </table>
 </div>
</div>
<script>
 function j(t){{var v=document.getElementById('v'); v.currentTime=t; v.play();}}
 function load(n){{var v=document.getElementById('v'); v.src=n; v.load(); v.play();
   document.getElementById('dl').href=n;}}
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
 list();
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
