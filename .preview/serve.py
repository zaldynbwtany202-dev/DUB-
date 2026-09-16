#!/usr/bin/env python3
"""Range-aware static server so the finished dub can be played in the browser preview.

Python's SimpleHTTPRequestHandler answers Range requests poorly, which makes a 41 MB
MP4 seek badly, so file serving is done by hand here. The player page is generated
from the directory it serves: every *.mp4 becomes a clickable entry and the newest
build is selected by default.

Committed on purpose: an uncommitted tool disappears whenever a turn is interrupted.

Usage: python .preview/serve.py <dir> <port>
"""

from __future__ import annotations

import http.server
import mimetypes
import socketserver
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

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
<title>النسخة المدبلجة — حكاية داس</title>
<style>
 body{{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.7 system-ui,'Segoe UI',Tahoma,sans-serif}}
 .wrap{{max-width:1000px;margin:0 auto;padding:18px}}
 h1{{font-size:20px;margin:0 0 4px}}
 .sub{{color:#8b949e;font-size:13px;margin-bottom:14px}}
 video{{width:100%;background:#000;border-radius:10px}}
 .bar{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}
 select{{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:7px 10px;font-size:14px}}
 a.dl{{background:#238636;color:#fff;text-decoration:none;border-radius:8px;padding:8px 14px;font-size:14px}}
 .chaps{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 18px}}
 .chaps button{{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:6px 10px;cursor:pointer;text-align:right;font-size:13px}}
 .chaps button:hover{{border-color:#58a6ff}}
 .chaps small{{display:block;color:#8b949e;font-size:11px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px 14px;margin-bottom:10px}}
 .card b{{color:#58a6ff}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 td,th{{padding:5px 8px;border-bottom:1px solid #21262d;text-align:right}}
 th{{color:#8b949e;font-weight:600}}
 .ok{{color:#3fb950}} .warn{{color:#d29922}}
</style>
</head>
<body>
<div class="wrap">
 <h1>حكاية داس — النسخة المدبلجة المصحَّحة (أول 9:24)</h1>
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
   <tr><td>أسماء الشخصيات</td><td>موحَّدة: داس · الانا · مول · تور · الفاس</td><td class="ok">أُصلح عيب «دس» و«الانه»</td></tr>
   <tr><td>تغطية كلام الأصل</td><td>99.64%</td><td class="ok">سليم</td></tr>
   <tr><td>تجاوز عن النوافذ</td><td>0.00 ث</td><td class="ok">سليم</td></tr>
   <tr><td>أثر الراوي الأصلي</td><td>corr 0.0023 (الحد 0.08)</td><td class="ok">لا شيء</td></tr>
   <tr><td>سرعة الكلام</td><td>1.17x – 1.48x (وسط 1.30x)</td><td class="warn">أسرع من الطبيعي — ثمن إيقاع المطابقة</td></tr>
   <tr><td>موسيقى/مؤثرات الأصل</td><td>غير مضمّنة</td><td class="warn">قرار لم يُتخذ — يمكن إضافتها</td></tr>
   <tr><td>الصوت</td><td>voice-00 وحده، لا خلط</td><td class="ok">سليم</td></tr>
   <tr><td>المسارات</td><td>1 فيديو + 1 صوت</td><td class="ok">سليم</td></tr>
  </table>
 </div>

 <div class="card" style="color:#8b949e;font-size:13px">
  النسخة تغطي <b>9:24 من أصل 52:22</b>. المتبقي 106 مجموعات (~42 دقيقة)، نصوصها المصحَّحة وتوقيتاتها جاهزة.
  إن ظهرت أي مشكلة: أعطني <b>الثانية</b> والوصف (سابق/لاحق للصورة، مستعجل، كلمة خاطئة).
 </div>
</div>
<script>
 function j(t){{var v=document.getElementById('v'); v.currentTime=t; v.play();}}
 function load(n){{var v=document.getElementById('v'); v.src=n; v.load(); v.play();
   document.getElementById('dl').href=n;}}
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "DubPreview/1.1"

    def log_message(self, fmt, *args):  # keep the process log readable
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

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
    print(f"serving {ROOT} on 0.0.0.0:{PORT}", flush=True)
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()
