"""The listener decision must survive refreshes; preview must not expose the repo."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from scripts import upload_server as preview


@contextmanager
def server(tmp_path, monkeypatch):
    root = tmp_path
    (root / "previews").mkdir()
    (root / "config").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret")
    (root / "previews" / "patrick-jane-voice27-g004.mp3").write_bytes(b"0123456789")
    (root / "previews" / "patrick-jane-voice29-g004.mp3").write_bytes(b"archived")
    (root / "config" / "patrick-jane-voice-status.json").write_text(json.dumps({
        "project": "patrick-jane-return", "status": "paused",
        "approved_for_production": False, "preview_stems": [],
    }))
    monkeypatch.setattr(preview, "ROOT", root)
    monkeypatch.setattr(preview, "PREVIEWS", root / "previews")
    monkeypatch.setattr(preview, "INCOMING", root / "incoming")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), preview.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def get(base, path, *, method="GET", headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as response:
        return response.code, response.headers, response.read()


def test_pause_hides_candidates_without_deleting_them(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        status, _, body = get(base, "/")
        assert status == 200
        assert "اختيار صوت باتريك جين متوقف" in body.decode()
        assert "patrick-jane-voice27-g004.mp3" not in body.decode()
        assert "patrick-jane-voice29-g004.mp3" not in body.decode()
        assert (root / "previews" / "patrick-jane-voice29-g004.mp3").is_file()
        # A deliberate, later decision updates the page without a server restart.
        state = root / "config" / "patrick-jane-voice-status.json"
        state.write_text(json.dumps({"project": "patrick-jane-return",
                                     "status": "auditioning", "approved_for_production": False,
                                     "preview_stems": ["patrick-jane-voice27-g004"]}))
        body = get(base, "/")[2].decode()
        assert "patrick-jane-voice27-g004.mp3" in body
        assert "patrick-jane-voice29-g004.mp3" not in body
        # Malformed or absent state fails closed rather than bringing back old voices.
        state.write_text("not json")
        body = get(base, "/")[2].decode()
        assert "قرار الصوت غير مؤكد" in body
        assert "patrick-jane-voice27-g004.mp3" not in body


@pytest.mark.parametrize("path", ["/.git/config", "/config/patrick-jane-voice-status.json",
                                   "/previews/../.git/config", "/previews/%2e%2e%2f.git%2fconfig",
                                   "/previews/%2ehidden.mp3", "/previews/missing.mp3"])
def test_static_server_does_not_expose_repository(tmp_path, monkeypatch, path):
    with server(tmp_path, monkeypatch) as (base, _):
        assert get(base, path)[0] == 404


@pytest.mark.parametrize("header,expected,content_range,body", [
    ("bytes=0-2", 206, "bytes 0-2/10", b"012"),
    ("bytes=8-200", 206, "bytes 8-9/10", b"89"),
    ("bytes=4-", 206, "bytes 4-9/10", b"456789"),
    ("bytes=-3", 206, "bytes 7-9/10", b"789"),
    ("bytes=12-20", 416, "bytes */10", b""),
    ("bytes=8-3", 416, "bytes */10", b""),
    ("bytes=-0", 416, "bytes */10", b""),
    ("bytes=garbage", 416, "bytes */10", b""),
    ("bytes=0-2,4-5", 416, "bytes */10", b""),
])
def test_media_byte_ranges(tmp_path, monkeypatch, header, expected, content_range, body):
    with server(tmp_path, monkeypatch) as (base, _):
        status, headers, received = get(base, "/previews/patrick-jane-voice27-g004.mp3",
                                        headers={"Range": header})
        assert status == expected
        assert headers["Content-Range"] == content_range
        assert int(headers["Content-Length"]) == len(body)
        assert received == body


def test_head_does_not_send_media_body(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, _):
        status, headers, body = get(base, "/previews/patrick-jane-voice27-g004.mp3",
                                    method="HEAD", headers={"Range": "bytes=0-3"})
        assert status == 206
        assert headers["Content-Length"] == "4"
        assert body == b""


def test_health_is_read_only_and_delete_is_disabled(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        status, _, body = get(base, "/health")
        assert status == 200
        assert json.loads(body) == {"ready": True, "build": "92",
                                    "voice_status": "paused", "production_approved": False}
        assert not (root / "incoming").exists()
        (root / "incoming").mkdir()
        (root / "incoming" / "film.mp4").write_bytes(b"important")
        req = urllib.request.Request(base + "/delete?name=film.mp4", data=b"", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=3)
        assert exc.value.code == 405
        assert (root / "incoming" / "film.mp4").read_bytes() == b"important"


def test_upload_cannot_overwrite_existing_film(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        (root / "incoming").mkdir()
        (root / "incoming" / "film.mp4").write_bytes(b"important")
        req = urllib.request.Request(base + "/upload?name=film.mp4&offset=0&total=3",
                                     data=b"new", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=3)
        assert exc.value.code == 409
        assert (root / "incoming" / "film.mp4").read_bytes() == b"important"


def test_preview_symlink_cannot_escape(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        (root / "previews" / "secret.mp3").symlink_to(root / ".git" / "config")
        assert get(base, "/previews/secret.mp3")[0] == 404


def test_production_gate_requires_voice_and_text_approval(tmp_path):
    from scripts.voice_decision_gate import VoiceDecisionError, require_production_ready

    cfg = tmp_path / "config"
    cfg.mkdir()
    state_path = cfg / "patrick-jane-voice-status.json"
    with pytest.raises(VoiceDecisionError):
        require_production_ready("patrick-jane-return", tmp_path)
    state = {"project": "patrick-jane-return", "status": "paused",
             "approved_for_production": True, "transcript_approved": True,
             "approved_voice_id": "voice-27"}
    state_path.write_text(json.dumps(state))
    with pytest.raises(VoiceDecisionError):
        require_production_ready("patrick-jane-return", tmp_path)
    state["status"] = "approved"
    state["transcript_approved"] = False
    state_path.write_text(json.dumps(state))
    with pytest.raises(VoiceDecisionError):
        require_production_ready("patrick-jane-return", tmp_path)
    state["transcript_approved"] = True
    state_path.write_text(json.dumps(state))
    require_production_ready("patrick-jane-return", tmp_path)
    require_production_ready("into-the-wild", tmp_path)


def test_assembler_refuses_paused_project_before_creating_output(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run([sys.executable, str(Path(preview.__file__).with_name("assemble_dub.py")),
                             "work/patrick-jane-return", "--out", str(tmp_path / "dub.wav")],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "production paused" in result.stderr
    assert not (tmp_path / "dub.wav").exists()


@pytest.mark.parametrize("query", [
    "name=clip.mp4&offset=-1&total=4", "name=clip.mp4&offset=nope&total=4",
    "name=clip.mp4&offset=0&total=1", "name=clip.mp4&offset=0&total=wat",
])
def test_invalid_upload_metadata_preserves_partial_take(tmp_path, monkeypatch, query):
    with server(tmp_path, monkeypatch) as (base, root):
        (root / "incoming").mkdir()
        (root / "incoming" / "clip.mp4.part").write_bytes(b"unfinished")
        req = urllib.request.Request(base + "/upload?" + query, data=b"new", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=3)
        assert exc.value.code == 400
        assert (root / "incoming" / "clip.mp4.part").read_bytes() == b"unfinished"


def test_resumable_upload_still_assembles_exact_bytes(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        payload = b"first_second"
        first = urllib.request.Request(base + "/upload?name=film.mp4&offset=0&total=12",
                                       data=payload[:6], method="POST")
        with urllib.request.urlopen(first, timeout=3) as r:
            assert json.loads(r.read()) == {"bytes": 6, "complete": False}
        assert json.loads(get(base, "/status?name=film.mp4")[2])["bytes"] == 6
        second = urllib.request.Request(base + "/upload?name=film.mp4&offset=6&total=12",
                                        data=payload[6:], method="POST")
        with urllib.request.urlopen(second, timeout=3) as r:
            assert r.status == 200
        assert (root / "incoming" / "film.mp4").read_bytes() == payload
        assert json.loads(get(base, "/status?name=film.mp4")[2])["done"] is True


def post_chunk(base, name, offset, total, data, fingerprint=None, checksum=None):
    import hashlib
    from urllib.parse import quote

    headers = {}
    if fingerprint:
        headers["X-Upload-Fingerprint"] = fingerprint
    if checksum is not None:
        headers["X-Chunk-SHA256"] = (hashlib.sha256(data).hexdigest()
                                     if checksum is True else checksum)
    req = urllib.request.Request(
        base + f"/upload?name={quote(name)}&offset={offset}&total={total}",
        headers=headers, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as r:
        return r.status, r.read()


def test_same_file_retry_is_idempotent_and_never_truncates(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        fp = "a" * 64
        assert post_chunk(base, "film.mp4", 0, 12, b"abcdef", fp, True)[0] == 200
        assert post_chunk(base, "film.mp4", 0, 12, b"abcdef", fp, True)[0] == 200
        assert post_chunk(base, "film.mp4", 2, 12, b"cdefgh", fp, True)[0] == 200
        assert (root / "incoming" / "film.mp4.part").read_bytes() == b"abcdefgh"
        assert post_chunk(base, "film.mp4", 0, 12, b"BAD", fp, True)[0] == 409
        assert post_chunk(base, "film.mp4", 8, 12, b"ijkl", fp, True)[0] == 200
        assert (root / "incoming" / "film.mp4").read_bytes() == b"abcdefghijkl"
        metadata = json.loads((root / "incoming" / "film.mp4.upload.json").read_text())
        assert metadata["fingerprint"] == fp
        assert metadata["total"] == 12
        assert [x["name"] for x in json.loads(get(base, "/files")[2])["files"]] == ["film.mp4"]


def test_wrong_fingerprint_or_chunk_hash_cannot_change_partial(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        fp = "b" * 64
        assert post_chunk(base, "film.mp4", 0, 10, b"hello", fp, True)[0] == 200
        part = root / "incoming" / "film.mp4.part"
        assert post_chunk(base, "film.mp4", 5, 10, b"world", "c" * 64, True)[0] == 409
        assert post_chunk(base, "film.mp4", 5, 10, b"world", fp, "0" * 64)[0] == 422
        assert post_chunk(base, "film.mp4", 6, 10, b"orld", fp, True)[0] == 409
        assert part.read_bytes() == b"hello"
        assert post_chunk(base, "film.mp4", 5, 10, b"world", fp, True)[0] == 200
        assert (root / "incoming" / "film.mp4").read_bytes() == b"helloworld"
        assert json.loads(get(base, "/status?name=film.mp4")[2])["fingerprint"] == fp


def test_legacy_partial_is_not_reused_with_new_fingerprint(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as (base, root):
        (root / "incoming").mkdir()
        (root / "incoming" / "film.mp4.part").write_bytes(b"keep me")
        assert post_chunk(base, "film.mp4", 7, 12, b"extra", "d" * 64, True)[0] == 409
        assert (root / "incoming" / "film.mp4.part").read_bytes() == b"keep me"


def test_concurrent_writers_to_same_name_do_not_mix_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with server(tmp_path, monkeypatch) as (base, root):
        barrier = Barrier(2)

        def send(data, fp):
            barrier.wait(timeout=3)
            return post_chunk(base, "film.mp4", 0, 6, data, fp, True)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(send, b"AAA", "a" * 64)
            b = pool.submit(send, b"BBB", "b" * 64)
            assert sorted([a.result(), b.result()]) == [200, 409]
        assert (root / "incoming" / "film.mp4.part").read_bytes() in (b"AAA", b"BBB")
