#!/usr/bin/env python
"""Serve the repository over HTTP and accept large video uploads.

The sandbox has no file picker, so the only way to get a new video in is over
the wire. Plain http.server cannot do it -- it has no POST handler at all.

Uploads are streamed straight to disk in one-megabyte chunks, never buffered in
memory, because the files in question are full-length films. Each upload lands
in incoming/ as NAME.part and is renamed to NAME only once the byte count
matches, so a half-finished transfer is never mistaken for a whole one. If the
connection drops the client asks /status how many bytes survived and resumes
from there, which matters more than it sounds on a file this size: a film that
dies at 80 percent does not have to start again.

    python scripts/upload_server.py 8000
"""
from __future__ import annotations

import html
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/user/DUB-").resolve()
INCOMING = ROOT / "incoming"
PREVIEWS = ROOT / "previews"
CHUNK = 1 << 20

def _human(n):
    for unit in ("بايت", "ك.بايت", "م.بايت", "ج.بايت"):
        if n < 1024 or unit == "ج.بايت":
            return f"{n:.0f} {unit}" if unit == "بايت" else f"{n:.1f} {unit}"
        n /= 1024.0


def render_previews():
    """Players for the finished cuts, so the preview frame shows the work and
    not only the door. Reads the directory on every request: a new cut appears
    on refresh instead of needing a restart."""
    try:
        cuts = sorted(PREVIEWS.glob("*.mp4"), key=lambda p: p.name)
    except OSError:
        cuts = []
    if not cuts:
        return ('<div class="prev"><h2>العيّنات الجاهزة</h2>'
                '<p class="m">لا عيّنات بعد.</p></div>')
    cards = []
    for c in cuts:
        url = "/previews/" + urllib.parse.quote(c.name)
        cards.append(
            '<div class="card">'
            f'<p class="t">{html.escape(c.stem)}</p>'
            f'<video controls preload="metadata" src="{url}"></video>'
            f'<p class="m">{_human(c.stat().st_size)}</p>'
            f'<a href="{url}" download>تنزيل</a>'
            f'<a href="{url}" target="_blank">فتح في نافذة جديدة</a>'
            '</div>')
    return ('<div class="prev"><h2>العيّنات الجاهزة</h2>' + "".join(cards) + '</div>')


def page():
    return PAGE.replace("<!--PREVIEWS-->", render_previews())


PAGE = """<!doctype html><html dir="rtl" lang="ar">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>رفع فيديو للدبلجة</title>
<style>
 body{font-family:system-ui,Segoe UI,Tahoma,sans-serif;background:#14161a;color:#e8eaed;
      margin:0;padding:28px;line-height:1.7}
 .wrap{max-width:760px;margin:0 auto}
 h1{font-size:21px;margin:0 0 6px}
 p.sub{color:#9aa0a6;margin:0 0 22px;font-size:14px}
 #drop{border:2px dashed #4a4f57;border-radius:14px;padding:44px 20px;text-align:center;
       cursor:pointer;transition:.15s;background:#1b1e24}
 #drop.hot{border-color:#5b8cff;background:#1f2634}
 #drop b{display:block;font-size:17px;margin-bottom:8px}
 #drop span{color:#9aa0a6;font-size:13px}
 #file{display:none}
 .row{margin-top:18px;display:none}
 .name{font-size:14px;margin-bottom:8px;word-break:break-all}
 .bar{height:12px;background:#2a2e36;border-radius:7px;overflow:hidden}
 .fill{height:100%;width:0;background:linear-gradient(90deg,#3b6cff,#5b8cff);transition:width .2s}
 .meta{display:flex;justify-content:space-between;font-size:12px;color:#9aa0a6;margin-top:7px}
 .ok{color:#4ade80;font-size:14px;margin-top:16px;display:none;line-height:1.8}
 .err{color:#f87171;font-size:13px;margin-top:12px}
 .done{color:#9aa0a6;font-size:13px;margin-top:26px;border-top:1px solid #2a2e36;padding-top:16px}
 .done li{margin:4px 0}
 .prev{margin-top:30px;border-top:1px solid #2a2e36;padding-top:20px}
 .prev h2{font-size:16px;margin:0 0 14px;color:#e8eaed}
 .card{background:#1b1e24;border:1px solid #2a2e36;border-radius:12px;padding:12px;margin-bottom:16px}
 .card video{width:100%;border-radius:8px;display:block;background:#000}
 .card .t{font-size:14px;font-weight:600;margin:0 0 4px}
 .card .m{font-size:12px;color:#9aa0a6;margin:6px 0 0}
 .card a{color:#5b8cff;font-size:12px;text-decoration:none;margin-inline-end:14px}
</style>
<div class="wrap">
 <h1>رفع فيديو للدبلجة</h1>
 <p class="sub">اسحب الملف هنا أو اضغط للاختيار. يُرفع على دفعات ويستأنف تلقائيًا إن انقطع.</p>
 <div id="drop"><b>اسحب الفيديو هنا</b><span>أو اضغط لاختيار ملف — mp4 / mkv / mov / webm / mp3 / wav</span></div>
 <input type="file" id="file">
 <div class="row" id="row">
   <div class="name" id="nm"></div>
   <div class="bar"><div class="fill" id="fill"></div></div>
   <div class="meta"><span id="pct">0%</span><span id="spd"></span></div>
 </div>
 <div class="ok" id="ok"></div>
 <div class="err" id="err"></div>
 <div class="done" id="done"></div>
 <!--PREVIEWS-->
</div>
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file');
const row=document.getElementById('row'),fill=document.getElementById('fill');
const pct=document.getElementById('pct'),spd=document.getElementById('spd');
const nm=document.getElementById('nm'),ok=document.getElementById('ok'),err=document.getElementById('err');
let busy=false;

drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot')};
drop.ondragleave=()=>drop.classList.remove('hot');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');if(e.dataTransfer.files[0])send(e.dataTransfer.files[0])};
file.onchange=()=>{if(file.files[0])send(file.files[0])};

function human(b){const u=['B','KB','MB','GB'];let i=0;while(b>=1024&&i<3){b/=1024;i++}return b.toFixed(1)+' '+u[i]}

function status(name){return fetch('/status?name='+encodeURIComponent(name)).then(r=>r.json())}

function send(f){
  if(busy)return; busy=true;
  ok.style.display='none'; err.textContent=''; row.style.display='block'; nm.textContent=f.name;
  let off=0, t0=Date.now();
  status(f.name).then(s=>{
    off = s.done ? 0 : s.bytes;
    if(off>0){ const r=confirm('يوجد '+human(off)+' مرفوع مسبقًا من هذا الملف. استئناف من حيث توقف؟'); if(!r) off=0; }
    pump();
  }).catch(()=>pump());

  function pump(){
    const xhr=new XMLHttpRequest();
    xhr.open('POST','/upload?name='+encodeURIComponent(f.name)+'&offset='+off);
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    let base=off;
    xhr.upload.onprogress=e=>{
      const now=base+e.loaded, p=Math.min(100,now/f.size*100);
      fill.style.width=p+'%'; pct.textContent=p.toFixed(1)+'%';
      const sec=(Date.now()-t0)/1000;
      if(sec>0.5){const rate=now/sec;spd.textContent=human(rate)+'/ث · بقي '+Math.max(0,Math.round((f.size-now)/rate))+'ث'}
    };
    xhr.onload=()=>{
      if(xhr.status===200){
        fill.style.width='100%'; pct.textContent='100%';
        ok.style.display='block';
        ok.innerHTML='✓ اكتمل الرفع: <b>'+f.name+'</b> ('+human(f.size)+')<br>'+
          'قل لي «ابدأ الدبلجة» وسأعالجه.';
        busy=false; list();
      } else { fail(xhr.responseText||('خطأ '+xhr.status)) }
    };
    xhr.onerror=()=>{
      status(f.name).then(s=>{
        if(s.bytes>off){ off=s.bytes; base=off; setTimeout(pump,700); }
        else fail('انقطع الاتصال. أعد المحاولة.');
      }).catch(()=>fail('انقطع الاتصال.'));
    };
    xhr.send(f.slice(off));
  }
  function fail(m){ err.textContent='✗ '+m; busy=false }
}

function list(){
  fetch('/files').then(r=>r.json()).then(d=>{
    if(!d.files.length){done.innerHTML='';return}
    done.innerHTML='<b>الملفات المرفوعة:</b><ul>'+
      d.files.map(x=>'<li>'+x.name+' — '+human(x.size)+'</li>').join('')+'</ul>';
  }).catch(()=>{})
}
list();
</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _safe(self, name):
        """Keep the name inside incoming/ and survive odd characters."""
        name = os.path.basename(name.replace("\\", "/").strip())
        name = name.lstrip(".") or "upload"
        INCOMING.mkdir(parents=True, exist_ok=True)
        return INCOMING / name

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/upload", "/", "/index.html"):
            # The preview frame opens at '/', so the door has to be there too.
            return self._send(200, page(), "text/html; charset=utf-8")
        if u.path == "/status":
            p = self._safe(q.get("name", [""])[0])
            part = p.with_suffix(p.suffix + ".part") if p.suffix else Path(str(p) + ".part")
            return self._send(200, json.dumps(
                {"bytes": part.stat().st_size if part.exists() else 0,
                 "size": p.stat().st_size if p.exists() else 0,
                 "done": p.exists()}), "application/json")
        if u.path == "/files":
            INCOMING.mkdir(parents=True, exist_ok=True)
            fs = sorted([{"name": f.name, "size": f.stat().st_size}
                         for f in INCOMING.iterdir()
                         if f.is_file() and not f.name.endswith(".part")],
                        key=lambda x: -x["size"])
            return self._send(200, json.dumps({"files": fs}), "application/json")
        # static
        rel = urllib.parse.unquote(u.path).lstrip("/")
        tgt = (ROOT / rel).resolve() if rel else ROOT / "index.html"
        if not str(tgt).startswith(str(ROOT)) or not tgt.is_file():
            return self._send(404, "غير موجود")
        ctype = "video/mp4" if tgt.suffix in (".mp4", ".m4v") else (
            "audio/mpeg" if tgt.suffix == ".mp3" else (
                "audio/wav" if tgt.suffix == ".wav" else "application/octet-stream"))
        size = tgt.stat().st_size
        rng = self.headers.get("Range")
        code, start, end = 200, 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(tgt, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                buf = fh.read(min(CHUNK, left))
                if not buf:
                    break
                self.wfile.write(buf)
                left -= len(buf)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path != "/upload":
            return self._send(404, "غير موجود")
        name = q.get("name", [""])[0]
        if not name:
            return self._send(400, "لا اسم")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, "طول غير صالح")
        try:
            offset = max(0, int(q.get("offset", ["0"])[0]))
        except ValueError:
            offset = 0
        # Optional total. Without it a request is assumed to carry the whole
        # remainder of the file, so a client that sends fixed-size chunks would
        # have each chunk renamed as if it were the finished upload. With it,
        # completion is a byte count rather than a guess.
        try:
            total = max(0, int(q["total"][0])) if "total" in q else None
        except ValueError:
            total = None
        final = self._safe(name)
        part = final.with_suffix(final.suffix + ".part") if final.suffix else Path(str(final) + ".part")
        INCOMING.mkdir(parents=True, exist_ok=True)
        # Honouring an offset is only possible when the bytes are actually
        # there. Opening wb and seeking would silently fill the gap with
        # zeroes -- the file would reach the right size and be renamed as
        # complete while its head was garbage. Refuse instead: 409 carries the
        # real byte count so the client can restart from it.
        have = part.stat().st_size if part.exists() else 0
        if offset and have < offset:
            return self._send(409, json.dumps(
                {"error": "لا بيانات جزئية عند هذه الإزاحة",
                 "bytes": have}), "application/json")
        mode = "r+b" if offset else "wb"
        if mode == "r+b":
            with open(part, "r+b") as fh:
                fh.truncate(offset)
        written = 0
        try:
            with open(part, mode) as fh:
                # Reopening starts at zero. Without this the resumed bytes
                # overwrite the head of the partial file instead of following
                # it, and the upload finishes at the length of the last chunk
                # rather than of the whole file.
                if offset:
                    fh.seek(offset)
                while written < length:
                    buf = self.rfile.read(min(CHUNK, length - written))
                    if not buf:
                        break
                    fh.write(buf)
                    written += len(buf)
        except (BrokenPipeError, ConnectionResetError):
            sys.stderr.write(f"[upload] انقطع {name} عند {offset + written}\n")
            return
        if written < length:
            sys.stderr.write(f"[upload] ناقص {name}: {written}/{length}\n")
            return self._send(500, "رفع ناقص")
        done_at = offset + written
        if total is not None and done_at < total:
            sys.stderr.write(f"[upload] تقدّم {name}: {done_at}/{total}\n")
            return self._send(200, json.dumps({"bytes": done_at, "complete": False}),
                              "application/json")
        if total is not None and done_at > total:
            with open(part, "r+b") as fh:
                fh.truncate(total)
        os.replace(part, final)
        sys.stderr.write(f"[upload] ✓ {final.name} ({final.stat().st_size} بايت)\n")
        self._send(200, "ok")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    INCOMING.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"  باب الرفع مفتوح على المنفذ {port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
