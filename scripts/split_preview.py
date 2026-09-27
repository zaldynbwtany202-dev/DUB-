#!/usr/bin/env python
"""Keep the film preview in the repository without ever hitting GitHub's file cap.

GitHub refuses any single file over 100 MB, and the dub preview of this film grows
about 4.9 MB per minute of film: at 46 of 78 groups it was already 83.9 MB, so the
next build would have been rejected by the remote *after* the work was done. The
choice is not "save it or not" -- everything the user cares about is saved -- it is
*how* the bytes are stored.

So the preview is stored as byte-range parts beside it, with a manifest that pins
each part's size and SHA-256 and the whole file's SHA-256. The playable file stays
on disk next to them for the server to serve; the repository carries the parts.

    python3 scripts/split_preview.py previews/voice13-created.mp4          # split
    python3 scripts/split_preview.py previews/voice13-created.mp4 --check   # verify only
    python3 scripts/split_preview.py --restore previews/voice13-created.mp4 # rejoin

The join is concatenation, not a re-encode: the rebuilt file is identical bit for
bit. Nothing is deleted by either command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

PART_MB = 80          # comfortably under the 100 MB cap, with room for the manifest
CHUNK = 8 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def parts_dir(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".parts")


def split(path: Path, part_mb: int = PART_MB) -> dict:
    out = parts_dir(path)
    out.mkdir(parents=True, exist_ok=True)
    limit = part_mb << 20
    records, index = [], 0
    with path.open("rb") as fh:
        while True:
            data = fh.read(limit)
            if not data:
                break
            index += 1
            part = out / f"{path.name}.part{index:03d}"
            part.write_bytes(data)
            records.append({"file": part.name, "bytes": len(data), "sha256": sha256(part)})
    manifest = {"source": path.name, "bytes": path.stat().st_size,
                "sha256": sha256(path), "parts": records,
                "note": "GitHub يرفض ملفًا أكبر من 100 م.بايت؛ هذه الأجزاء هي الملف نفسه "
                        "مقطّعة بايتات، وتُعاد بـ--restore بلا إعادة ترميز"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1)
                                       + "\n", encoding="utf-8")
    print(f"  ✓ {len(records)} جزءًا في {out} · حجم الملف {manifest['bytes'] / 1048576:.1f} م.بايت")
    return manifest


def check(path: Path) -> int:
    out = parts_dir(path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for r in manifest["parts"]:
        p = out / r["file"]
        if not p.exists() or p.stat().st_size != r["bytes"] or sha256(p) != r["sha256"]:
            bad.append(r["file"])
    total = sum(r["bytes"] for r in manifest["parts"])
    ok = not bad and total == manifest["bytes"]
    print(f"  {'✓' if ok else '✗'} الأجزاء {len(manifest['parts'])} · مجموعها {total} بايت "
          f"(الأصل {manifest['bytes']})" + (f" · تالف: {bad}" if bad else ""))
    return 0 if ok else 2


def restore(path: Path) -> int:
    out = parts_dir(path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if check(path) != 0:
        return 2
    tmp = path.with_suffix(path.suffix + ".rebuild")
    with tmp.open("wb") as dst:
        for r in manifest["parts"]:
            with (out / r["file"]).open("rb") as src:
                shutil.copyfileobj(src, dst, CHUNK)
    if sha256(tmp) != manifest["sha256"]:
        tmp.unlink()
        print("  ✗ المجموع لا يطابق بصمة الأصل — لم أستبدل شيئًا")
        return 2
    tmp.replace(path)
    print(f"  ✓ أُعيد بناء {path} ({manifest['bytes'] / 1048576:.1f} م.بايت) مطابقًا بصمةً")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, nargs="?")
    ap.add_argument("--part-mb", type=int, default=PART_MB)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", type=Path, default=None)
    a = ap.parse_args()
    if a.restore:
        return restore(a.restore)
    if not a.path or not a.path.exists():
        print("  ✗ لا ملف")
        return 2
    if a.check:
        return check(a.path)
    split(a.path, a.part_mb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
