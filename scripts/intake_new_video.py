#!/usr/bin/env python3
"""Intake a new video for dubbing: register it, measure it, and turn an SRT into a
recording plan.

The division of labour is the one this repository already approved for Das:
**words come from a script (here: the SRT the user supplies), times come from the
audio**. Whisper may run, but only to produce timings — never words.

Steps
  1. resolve the source file (--source, else the newest video dropped in the
     workspace / inbox), hash it, and measure duration, picture and audio
  2. register it under library/<slug>/ with a meta.json
  3. extract 16 kHz mono audio into .cache/<slug>/ for alignment work
  4. optionally run whisper.cpp for a timing map (skipped when the SRT already
     carries usable times)
  5. optionally parse an SRT and cut it into recording groups that respect the
     pacing policy: no slowdown (min_tempo 1.0), an approved ceiling, real pauses
     never filled, and one group small enough for a single TTS take

Nothing is recorded here. The output is a plan plus a feasibility verdict, so a
bad script is caught before any clip is spent.

Usage
  python scripts/intake_new_video.py --slug my-video --source path.mp4 --srt path.srt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.source_sync import tts_safe_egyptian  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
SEARCH_DIRS = [ROOT, ROOT / "inbox", ROOT.parent, ROOT.parent / "downloads",
               ROOT.parent / "uploads", Path("/tmp")]

SILENCE_BREAK = 2.0     # a gap at least this long is a real pause; never bridge it
MIN_TAKE = 0.60         # shorter cues merge into a neighbour
EDGE_SLACK = 0.08       # a take must land this far inside its window


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def measure(path: Path) -> dict:
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    secs = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]) if dur else 0.0
    vid = re.search(r"Stream #\d+:\d+.*?Video: (\w+).*?(\d{2,5})x(\d{2,5})", err)
    aud = re.search(r"Stream #\d+:\d+.*?Audio: (\w+).*?(\d+) Hz, (\w+)", err)
    return {
        "duration_s": round(secs, 3),
        "width": int(vid[2]) if vid else None,
        "height": int(vid[3]) if vid else None,
        "video_codec": vid[1] if vid else None,
        "audio_codec": aud[1] if aud else None,
        "audio_hz": int(aud[2]) if aud else None,
        "audio_channels": aud[3] if aud else None,
        "decodes": bool(dur and vid),
    }


def find_source(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            raise SystemExit(f"المصدر غير موجود: {p}")
        return p
    best: tuple[float, Path] | None = None
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in VIDEO_SUFFIXES or not p.is_file():
                continue
            if any(part in {".git", ".venv", "dubs", "library", ".cache", "node_modules"}
                   for part in p.parts):
                continue
            mtime = p.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, p)
    if best is None:
        raise SystemExit("لم أجد فيديو جديداً. أرسله أو مرّر --source PATH.")
    return best[1]


def parse_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\ufeff", "")
    stamp = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", raw):
        m = stamp.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = block[m.end():].strip()
        text = re.sub(r"<[^>]+>", " ", text)              # <i>, <b>, font tags
        text = re.sub(r"\{[^}]*\}", " ", text)            # {\an8} style tags
        text = re.sub(r"^\s*>>.*$", " ", text, flags=re.M)  # speaker arrows
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        cues.append({"t0": round(t0, 3), "t1": round(max(t1, t0 + 0.01), 3),
                     "text": tts_safe_egyptian(text)})
    cues.sort(key=lambda c: c["t0"])
    # drop exact duplicates (some exports repeat a line for two speakers)
    out: list[dict] = []
    for c in cues:
        if out and out[-1]["text"] == c["text"] and c["t0"] - out[-1]["t1"] < 0.05:
            out[-1]["t1"] = c["t1"]
        else:
            out.append(c)
    return out


def plan_groups(cues: list[dict], *, max_span: float, max_chars: int,
                chars_per_s: float, ceiling: float) -> list[dict]:
    """Cut cues into takes. A real pause always breaks a group; otherwise grow
    until the span or char budget is hit. A group that cannot fit the ceiling is
    split at its widest internal gap rather than spoken faster than approved."""
    groups: list[dict] = []
    cur: list[dict] = []

    def span(cs: list[dict]) -> float:
        return cs[-1]["t1"] - cs[0]["t0"]

    def chars(cs: list[dict]) -> int:
        return len(" ".join(c["text"] for c in cs).replace(" ", ""))

    def tempo(cs: list[dict]) -> float:
        room = span(cs) - EDGE_SLACK
        return (chars(cs) / chars_per_s) / room if room > 0 else float("inf")

    def flush(cs: list[dict]) -> None:
        if not cs:
            return
        while len(cs) > 1 and tempo(cs) > ceiling:
            gaps = [(cs[i + 1]["t0"] - cs[i]["t1"], i) for i in range(len(cs) - 1)]
            _, cut = max(gaps)
            emit(cs[:cut + 1])
            cs = cs[cut + 1:]
        emit(cs)

    def emit(cs: list[dict]) -> None:
        groups.append({
            "i": len(groups), "t0": cs[0]["t0"], "t1": cs[-1]["t1"],
            "window": round(cs[-1]["t1"] - cs[0]["t0"], 3),
            "cues": len(cs), "chars": chars(cs),
            "predicted_tempo": round(tempo(cs), 3),
            "text": tts_safe_egyptian(" ".join(c["text"] for c in cs).strip()),
        })

    for c in cues:
        if cur:
            gap = c["t0"] - cur[-1]["t1"]
            big = (span(cur + [c]) > max_span or chars(cur + [c]) > max_chars
                   or gap >= SILENCE_BREAK)
            if big:
                flush(cur)
                cur = []
        cur.append(c)
    flush(cur)

    # merge neighbours that are too short to record on their own
    merged: list[dict] = []
    for g in groups:
        if merged and g["window"] < MIN_TAKE and merged[-1]["window"] + g["window"] <= max_span:
            prev = merged[-1]
            prev["t1"] = g["t1"]
            prev["window"] = round(prev["t1"] - prev["t0"], 3)
            prev["cues"] += g["cues"]
            prev["text"] = tts_safe_egyptian(prev["text"] + " " + g["text"])
            prev["chars"] = len(prev["text"].replace(" ", ""))
            room = prev["window"] - EDGE_SLACK
            prev["predicted_tempo"] = round((prev["chars"] / chars_per_s) / room, 3) if room > 0 else None
        else:
            merged.append(dict(g))
    for k, g in enumerate(merged):
        g["i"] = k
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--slug")
    ap.add_argument("--srt")
    ap.add_argument("--whisper", action="store_true",
                    help="run whisper.cpp for a timing map (timings only, never words)")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--max-span", type=float, default=24.0)
    ap.add_argument("--max-chars", type=int, default=380)
    ap.add_argument("--chars-per-s", type=float, default=11.79,
                    help="measured speaking rate of the recording voice")
    ap.add_argument("--tempo-ceiling", type=float, default=1.60)
    args = ap.parse_args()

    src = find_source(args.source)
    info = measure(src)
    if not info["decodes"]:
        raise SystemExit(f"الملف لا يُفكّ ترميزه: {src}")
    slug = args.slug or re.sub(r"[^a-z0-9-]+", "-", src.stem.lower()).strip("-")[:48] or "new-video"

    lib = ROOT / "library" / slug
    lib.mkdir(parents=True, exist_ok=True)
    target = lib / "source.mp4"
    if src.resolve() != target.resolve():
        if not target.exists():
            shutil.copy2(src, target)
    cache = ROOT / ".cache" / slug
    cache.mkdir(parents=True, exist_ok=True)
    wav = cache / "audio-16k.wav"
    if not wav.exists():
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(target),
                        "-ac", "1", "-ar", "16000", str(wav)], check=True, capture_output=True)

    digest = sha256(target)
    meta_path = lib / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update({"slug": slug, "title": meta.get("title") or src.stem,
                 "original_name": src.name, "size": target.stat().st_size,
                 "sha256": digest, "duration": info["duration_s"],
                 "width": info["width"], "height": info["height"],
                 "source": f"library/{slug}/source.mp4",
                 "audio_hz": info["audio_hz"], "audio_channels": info["audio_channels"],
                 "intake": "scripts/intake_new_video.py", "status": "intake"})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dubs = ROOT / "dubs" / slug
    dubs.mkdir(parents=True, exist_ok=True)
    report: dict = {"slug": slug, "source": str(target.relative_to(ROOT)),
                    "sha256": digest, **info, "audio_wav": str(wav.relative_to(ROOT))}

    cli = ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli"
    model = ROOT / ".cache/models/whisper-ggml/ggml-small.bin"
    if args.whisper and cli.exists() and model.exists():
        subprocess.run([str(cli), "-m", str(model), "-f", str(wav), "-l", args.lang,
                        "-bo", "5", "-sow", "-otxt", "-osrt", "-oj",
                        "-of", str(cache / "asr")], check=True, capture_output=True)
        report["asr_timing_only"] = str((cache / "asr.srt").relative_to(ROOT))
    elif args.whisper:
        report["asr_timing_only"] = "skipped: run scripts/bootstrap_local_whisper.py first"

    if args.srt:
        cues = parse_srt(Path(args.srt))
        if not cues:
            raise SystemExit(f"لا مقاطع في ملف الترجمة: {args.srt}")
        groups = plan_groups(cues, max_span=args.max_span, max_chars=args.max_chars,
                             chars_per_s=args.chars_per_s, ceiling=args.tempo_ceiling)
        for g in groups:
            (dubs / f"text-{g['i']:04d}.txt").write_text(g["text"] + "\n", encoding="utf-8")
        plan = {"slug": slug, "source": f"library/{slug}/source.mp4",
                "words_from": f"SRT supplied by the user ({Path(args.srt).name})",
                "times_from": "SRT cue offsets, checked against the measured audio duration",
                "voice_id": "voice-00", "pacing": "matched (per-take atempo onto original onsets)",
                "chars_per_s": args.chars_per_s, "tempo_ceiling": args.tempo_ceiling,
                "min_tempo": 1.0, "cues": len(cues), "groups": groups}
        (dubs / "groups.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        over = [g["i"] for g in groups if g["predicted_tempo"] > args.tempo_ceiling]
        under = [g["i"] for g in groups if g["predicted_tempo"] < 1.0]
        report.update({
            "srt": str(Path(args.srt)), "srt_cues": len(cues),
            "srt_last_cue_ends_at": cues[-1]["t1"],
            "srt_beyond_audio": round(max(0.0, cues[-1]["t1"] - info["duration_s"]), 3),
            "groups": len(groups),
            "speech_planned_s": round(sum(g["window"] for g in groups), 3),
            "coverage_ratio": round(sum(g["window"] for g in groups) / info["duration_s"], 4),
            "tempo_min": min(g["predicted_tempo"] for g in groups),
            "tempo_max": max(g["predicted_tempo"] for g in groups),
            "groups_over_ceiling": over, "groups_underfill_window": under,
            "feasible": not over,
            "clips_needed": len(groups),
            "recording_rounds_at_8_per_turn": max(1, -(-len(groups) // 8)),
        })

    (dubs / "intake-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("feasible", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
