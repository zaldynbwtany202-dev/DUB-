#!/usr/bin/env python
"""The project's memory: core facts, with sources, and a drift check against reality.

This exists because of how the work actually fails here. The sandbox has wiped the
session repeatedly; every time, the repository survived and the *state of mind* did
not. NEXT.md explains the reasoning, but it is prose -- a new session has to read all
of it, and nothing notices when a fact in it stops being true (the deliverable was
rebuilt, the voice changed, the count moved on).

The design is borrowed from the AI-memory projects that do this well, and reduced to
what a single repository needs:

  core vs archival   (MemGPT/Letta)  MEMORY/core.json holds the few dozen facts that
                     must always be in view: what is locked, what is current, where
                     the deliverable is, what is open. Everything longer stays in
                     NEXT.md / STANDING.md and is read on demand.
  facts with sources (mem0)          every fact carries `since` and `source`, so a
                     claim can be traced to a commit, a run, or a sentence from the
                     user -- not to memory of a conversation.
  invalidation       (Zep/Graphiti)  a fact is never deleted; a new value supersedes
                     the old one and the chain stays readable (`supersedes`).
  drift check        (this repo)     `--verify` re-checks every checkable fact against
                     the filesystem: does the deliverable exist, does the BUILD stamp
                     match the file, is the locked voice still the one on disk. A
                     memory that cannot be wrong is not a memory, it is a claim.

    python3 scripts/memory.py --show            # what the system knows
    python3 scripts/memory.py --ask "صوت"        # retrieve
    python3 scripts/memory.py --verify          # memory vs reality
    python3 scripts/memory.py --fact voice.current=voice-15 --since 2026-09-27 \
        --source "اختيار المستمع من الاستماع" --status active
    python3 scripts/memory.py --extract         # append facts from the last commits
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "MEMORY" / "core.json"


def load() -> dict:
    if not CORE.exists():
        CORE.parent.mkdir(parents=True, exist_ok=True)
        return {"version": 1, "facts": []}
    return json.loads(CORE.read_text(encoding="utf-8"))


def save(d: dict) -> None:
    CORE.parent.mkdir(parents=True, exist_ok=True)
    CORE.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def active(d: dict, key: str | None = None) -> list[dict]:
    out = [f for f in d["facts"] if f.get("status", "active") == "active"]
    return [f for f in out if f["key"] == key] if key else out


def set_fact(d: dict, key: str, value: str, since: str, source: str,
             status: str = "active", check: str | None = None,
             note: str | None = None) -> dict:
    old = active(d, key)
    for f in old:                        # invalidation, never deletion
        f["status"] = "superseded"
        f["superseded_at"] = since
    fact = {"key": key, "value": value, "since": since, "source": source,
            "status": status}
    if check:
        fact["check"] = check
    if note:
        fact["note"] = note
    if old:
        fact["supersedes"] = [f"{f['value']} (منذ {f['since']})" for f in old]
    d["facts"].append(fact)
    return fact


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout


def check_fact(f: dict) -> tuple[bool | None, str]:
    """Return (ok, detail); None means 'not checkable', which is honest, not a pass."""
    c = f.get("check")
    if not c:
        return None, "بلا فحص آلي"
    kind, _, arg = c.partition(":")
    if kind == "file":
        p = ROOT / arg
        if not p.exists():
            return False, f"مفقود: {arg}"
        return True, f"موجود ({p.stat().st_size / 1048576:.1f} م.بايت)"
    if kind == "build":
        stamp = run(["git", "-C", str(ROOT), "show", "HEAD:scripts/upload_server.py"])
        import re
        m = re.search(r'BUILD = "(\d+)"', stamp)
        want = arg.strip()
        return (m.group(1) == want,
                f"الوسم في الكود {m.group(1) if m else '؟'} · المتوقّع {want}")
    if kind == "takes":
        n = len(list((ROOT / arg).glob("g*.mp3")))
        want = int(f.get("note", "0").split("=")[-1]) if f.get("note") else None
        if want is None:
            return n > 0, f"{n} أخذة"
        return n >= want, f"{n} أخذة (المطلوب ≥{want})"
    if kind == "json":
        path, _, dotted = arg.partition("#")
        p = ROOT / path
        if not p.exists():
            return False, f"مفقود: {path}"
        v = json.loads(p.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            v = v.get(part) if isinstance(v, dict) else None
        return True, f"{dotted} = {v}"
    return None, f"فحص غير معروف: {c}"


def cmd_show(d: dict) -> None:
    facts = active(d)
    print(f"ذاكرة النظام — {len(facts)} حقيقة نشطة · superseded: "
          f"{len(d['facts']) - len(facts)}\n")
    for f in facts:
        chain = f" ⟵ كان: {', '.join(f['supersedes'])}" if f.get("supersedes") else ""
        print(f"  {f['key']:26} {f['value']}")
        print(f"      منذ {f['since']} · {f['source']}{chain}")


def cmd_verify(d: dict) -> int:
    bad, unknown = [], 0
    print("تحقّق الذاكرة مقابل الواقع:\n")
    for f in active(d):
        ok, detail = check_fact(f)
        mark = "✓" if ok else ("?" if ok is None else "✗")
        if ok is None:
            unknown += 1
        elif not ok:
            bad.append(f["key"])
        print(f"  {mark} {f['key']:26} {detail}")
    print(f"\n  {'✓ لا انحراف' if not bad else '✗ انحراف: ' + '، '.join(bad)}"
          f" · {unknown} حقيقة بلا فحص آلي (تُراجَع بالقراءة، لا تُعدّ ناجحة)")
    return 2 if bad else 0


def cmd_ask(d: dict, needle: str) -> None:
    hits = [f for f in d["facts"]
            if needle in f["key"] or needle in str(f.get("value", ""))
            or needle in f.get("source", "")]
    print(f"نتائج «{needle}»: {len(hits)}\n")
    for f in sorted(hits, key=lambda x: x["since"]):
        tag = "" if f.get("status", "active") == "active" else f" [{f['status']}]"
        print(f"  {f['since']}  {f['key']} = {f['value']}{tag}")
        print(f"             المصدر: {f['source']}")
        if f.get("supersedes"):
            print(f"             حلّ محل: {', '.join(f['supersedes'])}")


def cmd_extract(d: dict) -> int:
    """Append a fact per BUILD commit, so history enters memory by itself."""
    log = run(["git", "log", "--pretty=%h|%ad|%s", "--date=short", "-25"])
    known = {f["value"] for f in d["facts"]}
    added = 0
    for line in log.strip().splitlines():
        sha, day, subject = line.split("|", 2)
        if not subject.startswith("BUILD") or subject in known:
            continue
        build = subject.split(":")[0].strip().replace("BUILD ", "")
        set_fact(d, f"build.{build}", subject, day, f"git {sha}")
        added += 1
    if added:
        save(d)
    print(f"  استُخرج {added} حقيقة من آخر 25 commitًا")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--ask", default=None)
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--fact", default=None, help="key=value")
    ap.add_argument("--since", default=str(date.today()))
    ap.add_argument("--source", default="تصريح مباشر")
    ap.add_argument("--status", default="active")
    ap.add_argument("--check", default=None)
    ap.add_argument("--note", default=None)
    a = ap.parse_args()
    d = load()

    if a.fact:
        key, _, value = a.fact.partition("=")
        if not key or not value:
            raise SystemExit("  ✗ الصيغة: --fact key=value")
        f = set_fact(d, key.strip(), value.strip(), a.since, a.source,
                     a.status, a.check, a.note)
        save(d)
        print(f"  ✓ {f['key']} = {f['value']}"
              + (f" (حلّ محل: {f['supersedes'][0]})" if f.get("supersedes") else ""))
        return 0
    if a.extract:
        return cmd_extract(d)
    if a.verify:
        return cmd_verify(d)
    if a.ask:
        cmd_ask(d, a.ask)
        return 0
    cmd_show(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
