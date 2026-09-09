#!/usr/bin/env python3
"""Turn a click on the voice shop into an instruction the dub can be built from.

The page runs in a browser and writes `docs/voice-choice.json` into the branch. That file is
untrusted input - it arrives through a token from a machine we do not control - so this script
never executes what the browser asked for. It looks the slot number up in the catalog
(`docs/voice-shop.json`), which is generated here, and applies **that** chain. A browser that
sent its own filter string gets a refusal, not a shell.

    python scripts/set_voice_choice.py --project hajj-dream-2108415
    python scripts/set_voice_choice.py --project hajj-dream-2108415 --narration in.wav --out out.wav
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALLOWED_FILTERS = ("asetrate", "aresample", "atempo", "bass", "treble", "highpass", "lowpass",
                   "tremolo", "anull", "loudnorm")


def load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"لا يوجد ملف: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def chain_is_safe(chain: str) -> bool:
    for part in (p for p in chain.split(",") if p.strip()):
        name = part.split("=", 1)[0].strip()
        if name not in ALLOWED_FILTERS:
            return False
        if any(ch in part for ch in ('"', "'", ";", "`", "\n")):
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="hajj-dream-2108415")
    ap.add_argument("--choice", type=Path, default=ROOT / "docs" / "voice-choice.json")
    ap.add_argument("--catalog", type=Path, default=ROOT / "docs" / "voice-shop.json")
    ap.add_argument("--narration", type=Path, help="existing narration wav to re-voice with the chain")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--write-state", action="store_true",
                    help="record the choice under dubs/<project>/timing-repair/voice-choice.json")
    args = ap.parse_args()

    choice = load(args.choice)
    catalog = load(args.catalog)
    slot = choice.get("sample_id")
    row = next((s for s in catalog["samples"] if s["id"] == slot), None)
    if row is None:
        raise SystemExit(f"العينة رقم {slot} غير موجودة في الكتالوج المولّد — لن ننفّذ طلبًا من المتصفح")

    if row.get("status") != "ready":
        raise SystemExit(f"العينة رقم {slot} محجوزة وما اتسجلتش بعد: {row.get('why', '')}")

    browser_chain, repo_chain = choice.get("chain"), row.get("chain")
    if browser_chain not in (None, repo_chain):
        print(f"⚠ المتصفح بعت سلسلة مختلفة ({browser_chain!r}) — بنستخدم سلسلة الكتالوج "
              f"({repo_chain!r}) لأن الملف المولّد هو المرجع.")
    chain = repo_chain or "anull"
    if not chain_is_safe(chain):
        raise SystemExit(f"سلسلة الفلاتر في الكتالوج فيها فلتر غير مسموح: {chain!r}")

    preview = ROOT / "docs" / row["preview_url"]
    digest = hashlib.sha256(preview.read_bytes()).hexdigest()[:16] if preview.is_file() else None
    if digest and row.get("sha256") and digest != row["sha256"]:
        print(f"⚠ معاينة العينة اتغيرت عن اللي اتولّد ({row['sha256']} → {digest}) — "
              "أعد بناء الكتالوج قبل التشغيل.")

    record = {
        "schema": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_id": slot,
        "project": args.project,
        "base_take": row["source_take"],
        "platform_voice": row["base_voice"],
        "dialect": row["dialect"], "age": row["age"], "tone": row["tone"], "gender": row["gender"],
        "chain": chain,
        "chain_check": row.get("chain_check"),
        "regenerate_note": (
            "منصة الصوت بتلفظ ar و ar-EG فقط في الجلسة دي؛ لو هتتسجل أسطر جديدة استخدم نفس "
            "voice_id ده ثم طبّق السلسلة دي على الميكس (مش على المصدر)."),
        "source_choice_file": str(args.choice.relative_to(ROOT)) if args.choice.is_relative_to(ROOT) else str(args.choice),
    }

    print(json.dumps({"choice": record}, ensure_ascii=False, indent=2))

    if args.write_state:
        out = ROOT / "dubs" / args.project / "timing-repair" / "voice-choice.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n✓ اتسجّل في {out.relative_to(ROOT)}")

    if args.narration:
        if not args.narration.is_file():
            raise SystemExit(f"ملف السرد مش موجود: {args.narration}")
        out = args.out or args.narration.with_name(args.narration.stem + "-voiced.wav")
        filters = (chain + "," if chain and chain != "anull" else "") + "loudnorm=I=-16:TP=-1.5:LRA=9"
        from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe

        command = [ffmpeg_exe(), "-y", "-v", "error", "-i", str(args.narration), "-af", filters,
                   "-ar", "44100", "-ac", "1", str(out)]
        print("\n$ " + " ".join(shlex.quote(c) for c in command))
        done = subprocess.run(command, capture_output=True)
        if done.returncode:
            raise SystemExit(done.stderr.decode()[-400:])
        print(f"✓ اتكتب المعالج في {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
