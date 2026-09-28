#!/usr/bin/env python3
"""Dub a NEW video with the locked voice -- one command from a file to a plan.

The locked recipe (config/voice-lock.json) already knows the voice, the
treatment and the mastering. What a new film needs before that is its own
timing: the words come from a transcript (here: the local Whisper hears the
video), and the times come from that same pass -- but the transcript is a
DRAFT the user corrects, never a final script. This script does the mechanical
half in one go:

  1. register the file (copy into library/<slug>/source.mp4, hash, measure)
  2. pull 16 kHz mono audio
  3. transcribe with the local Whisper: words + word-level times -> an SRT
  4. plan recording groups from that SRT (intake_new_video.py does the cutting)
  5. copy the plan to work/<slug>/groups.json, where the locked build reads it
  6. print the first batch of group texts, ready to generate

    python3 scripts/dub_new_video.py --source incoming/film.mp4 --slug my-film
    python3 scripts/dub_new_video.py --slug my-film --batch 2 --per-batch 10

Nothing is recorded here and no take is spent: the output is a plan plus the
texts of the next batch.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402

WCLI = ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ROOT / ".cache/models/whisper-ggml/ggml-small.bin"
VOICE_RATE = 11.62          # measured speaking rate of voice-21 (chars/s)


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-").lower()
    return (s[:40] or "new-video")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def transcribe(wav: Path, out: Path, lang: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run([str(WCLI), "-m", str(MODEL), "-f", str(wav), "-l", lang,
         "-bo", "5", "-sow", "-osrt", "-otxt", "-of", str(out)])
    return out.with_suffix(".srt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=None,
                    help="الفيديو (افتراضًا: الأحدث في incoming/)")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--lang", default="ar")
    ap.add_argument("--chars-per-s", type=float, default=VOICE_RATE)
    ap.add_argument("--tempo-ceiling", type=float, default=1.60)
    ap.add_argument("--max-span", type=float, default=24.0)
    ap.add_argument("--max-chars", type=int, default=380)
    ap.add_argument("--batch", type=int, default=1, help="رقم الدفعة (10 أخذات كل دفعة)")
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--skip-asr", action="store_true")
    a = ap.parse_args()

    src = a.source
    if src is None:
        cands = sorted((ROOT / "incoming").glob("*"), key=lambda p: p.stat().st_mtime)
        cands = [c for c in cands if c.is_file() and c.suffix.lower() in
                 {".mp4", ".mov", ".mkv", ".webm", ".m4v"}]
        if not cands:
            raise SystemExit("لا فيديو في incoming/ — ارفعه أولًا.")
        src = cands[-1]
    slug = a.slug or slugify(src.stem)

    # 1. register (intake copies the file and writes meta.json)
    run([sys.executable, str(ROOT / "scripts/intake_new_video.py"),
         "--source", str(src), "--slug", slug])
    meta = json.loads((ROOT / "library" / slug / "meta.json").read_text(encoding="utf-8"))
    dur = float(meta.get("duration") or 0)
    print(f"  الفيلم: {slug} · {dur:.1f} ث · {meta.get('width')}x{meta.get('height')} · "
          f"{meta.get('audio_hz')} هرتز {meta.get('audio_channels')}")

    # 2. audio + 3. transcript draft
    cache = ROOT / ".cache" / slug
    wav = cache / "audio-16k.wav"
    cache.mkdir(parents=True, exist_ok=True)
    if not wav.exists():
        run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(ROOT / "library" / slug / "source.mp4"),
             "-ac", "1", "-ar", "16000", str(wav)])
    srt = cache / "asr.srt"
    if not a.skip_asr:
        print("  تفريغ آلي (كلمات + توقيت) …")
        transcribe(wav, cache / "asr", a.lang)
        if not srt.exists():
            raise SystemExit("لم يُنتج التفريغ ملف SRT.")
        lines = sum(1 for ln in srt.read_text(encoding="utf-8").splitlines() if "-->" in ln)
        print(f"  مقاطع التفريغ: {lines}")

    # 4. plan groups from the draft transcript
    run([sys.executable, str(ROOT / "scripts/intake_new_video.py"), "--slug", slug,
         "--source", str(ROOT / "library" / slug / "source.mp4"), "--srt", str(srt),
         "--chars-per-s", str(a.chars_per_s), "--tempo-ceiling", str(a.tempo_ceiling),
         "--max-span", str(a.max_span), "--max-chars", str(a.max_chars)])

    plan_path = ROOT / "dubs" / slug / "groups.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    groups = plan["groups"]
    work = ROOT / "work" / slug
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "groups.json").exists():
        shutil.copy2(plan_path, work / "groups.json")
    (work / "takes").mkdir(exist_ok=True)

    over = [g["i"] for g in groups if g["predicted_tempo"] > a.tempo_ceiling]
    print(f"\n  المجموعات: {len(groups)} · أخذات مطلوبة: {len(groups)} · "
          f"جولات بعشرة: {-(-len(groups) // 10)} · فوق السقف: {len(over)}")

    lo = (a.batch - 1) * a.per_batch
    hi = min(lo + a.per_batch, len(groups))
    if lo >= len(groups):
        print(f"  الدفعة {a.batch} لا شيء فيها (النهاية {len(groups)}).")
        return 0
    print(f"\n  الدفعة {a.batch}: المجموعات {lo} → {hi - 1}")
    for g in groups[lo:hi]:
        print(f"\n--- g{g['i']:03d}  ({g['t0']:.2f}→{g['t1']:.2f} · {g['window']:.2f} ث · "
              f"{g['chars']} حرفًا · سرعة مقدّرة {g['predicted_tempo']:.2f}×)")
        print(g["text"])
    print("\n  → التوقيت من الصوت، والكلمات من التفريغ الآلي: يمرّ عليها المستخدم للتصحيح "
          "قبل التوليد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
