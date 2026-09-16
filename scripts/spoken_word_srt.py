#!/usr/bin/env python3
"""Word SRT from actual pronunciation times.

Whisper Small is used ONLY for when each spoken token happens.
Final words come from the YouTube unique transcript (NB2YcTh_L6k), not Whisper.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
CLI = ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ROOT / ".cache/models/whisper-ggml/ggml-small.bin"
WAV = ROOT / "library/das-full/full.wav"
YT = ROOT / "library/das-full/youtube/unique.txt"
WORK = ROOT / "library/das-full/align"
CHUNK = 45.0  # short slices keep Arabic; 5-min slices collapsed language
VIDEO_END = 3142.38
OUT_DIR = ROOT / "library/das-full/youtube"
PREFIX = "NB2YcTh_L6k"
# The constants above are the das-full defaults. --slug/--wav/--yt/--work/--end
# rebind them so the same word-from-YouTube / times-from-audio alignment runs on
# any project (doctor-lecture, shorts-test, ...). Nothing else changes.


def stamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def norm(s: str) -> str:
    s = re.sub(r"[^\w\u0600-\u06FF]+", "", s, flags=re.UNICODE)
    return (
        s.replace("ة", "ه")
        .replace("ى", "ي")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .replace("ء", "")
    )


def split_chunks() -> list[tuple[int, Path, float]]:
    WORK.mkdir(parents=True, exist_ok=True)
    n = int((VIDEO_END + CHUNK - 0.01) // CHUNK)
    out = []
    for i in range(n):
        start = i * CHUNK
        path = WORK / f"chunk-{i:02d}.wav"
        if not path.is_file() or path.stat().st_size < 1000:
            subprocess.run(
                [
                    str(FFMPEG), "-y", "-v", "error",
                    "-ss", f"{start:.3f}", "-t", f"{CHUNK:.3f}",
                    "-i", str(WAV), "-ac", "1", "-ar", "16000", str(path),
                ],
                check=True,
            )
        out.append((i, path, start))
    return out


def run_whisper(wav: Path, of: Path) -> None:
    js = of.with_suffix(".json")
    if js.is_file() and js.stat().st_size > 1000:
        try:
            data = json.loads(js.read_bytes().decode("utf-8", errors="surrogateescape"))
            n_ar = sum(
                1
                for s in data.get("transcription") or []
                if any("\u0600" <= c <= "\u06FF" for c in (s.get("text") or ""))
            )
            if n_ar >= 8:
                return
        except Exception:
            pass
        js.unlink(missing_ok=True)
    log = of.with_suffix(".log")
    with log.open("w") as err:
        subprocess.run(
            [
                str(CLI), "-m", str(MODEL), "-f", str(wav),
                "-l", "ar", "-t", "2", "-bs", "1",
                "-ml", "1", "-sow", "-ojf", "-np",
                "-of", str(of),
            ],
            check=True,
            stdout=err,
            stderr=err,
        )


def spoken_from_json(path: Path, offset: float) -> list[dict]:
    data = json.loads(path.read_bytes().decode("utf-8", errors="surrogateescape"))
    rows = []
    for seg in data.get("transcription") or []:
        text = (seg.get("text") or "").strip()
        if not text or text.startswith("[") or text.startswith("(_"):
            continue
        if not any("\u0600" <= c <= "\u06FF" for c in text):
            continue
        t0 = seg["offsets"]["from"] / 1000.0 + offset
        t1 = seg["offsets"]["to"] / 1000.0 + offset
        if t1 <= t0:
            t1 = t0 + 0.06
        rows.append({"t0": t0, "t1": t1, "w": text})
    return rows


def align(spoken: list[dict], yt: list[str]) -> list[dict]:
    a = [norm(s["w"]) for s in spoken]
    b = [norm(w) for w in yt]
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                s = spoken[i1 + k]
                out.append({"w": yt[j1 + k], "t0": s["t0"], "t1": s["t1"], "how": "spoken"})
        elif op == "replace":
            if i2 == i1:
                t0 = spoken[i1]["t0"] if i1 < len(spoken) else (out[-1]["t1"] if out else 0.0)
                t1 = t0 + 0.08 * max(j2 - j1, 1)
            else:
                t0 = spoken[i1]["t0"]
                t1 = spoken[i2 - 1]["t1"]
            n = max(j2 - j1, 1)
            span = max(t1 - t0, 0.06 * n)
            for k in range(j2 - j1):
                a0 = t0 + span * k / n
                a1 = t0 + span * (k + 1) / n
                out.append({"w": yt[j1 + k], "t0": a0, "t1": a1, "how": "replace"})
        elif op == "insert":
            t0 = spoken[i1]["t0"] if i1 < len(spoken) else (out[-1]["t1"] if out else 0.0)
            if out:
                t0 = max(t0, out[-1]["t1"])
            n = max(j2 - j1, 1)
            for k in range(j2 - j1):
                a0 = t0 + 0.07 * k
                a1 = a0 + 0.07
                out.append({"w": yt[j1 + k], "t0": a0, "t1": a1, "how": "insert"})
        # delete: extra whisper tokens (music/noise) — skip
    return out


def write_srt(words: list[dict], path: Path) -> None:
    lines = []
    prev = 0.0
    for i, w in enumerate(words, 1):
        t0 = max(float(w["t0"]), prev)
        t1 = max(float(w["t1"]), t0 + 0.04)
        if i < len(words):
            nxt = max(float(words[i]["t0"]), t0 + 0.04)
            t1 = min(t1, max(t0 + 0.04, nxt - 0.01))
        w["t0"], w["t1"] = t0, t1
        prev = t1
        lines.append(f"{i}\n{stamp(t0)} --> {stamp(t1)}\n{w['w']}\n")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    global WAV, YT, WORK, CHUNK, VIDEO_END, OUT_DIR, PREFIX
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wav", type=Path, help="16k mono audio to time (default: das-full full.wav)")
    ap.add_argument("--yt", type=Path, help="flowing transcript whose WORDS are authoritative")
    ap.add_argument("--work", type=Path, help="scratch dir for chunk wavs + per-chunk ASR json")
    ap.add_argument("--end", type=float, help="audio duration in seconds")
    ap.add_argument("--chunk", type=float, default=CHUNK, help="slice seconds (45 keeps Arabic)")
    ap.add_argument("--out-dir", type=Path, help="where <prefix>.spoken.srt/.json are written")
    ap.add_argument("--prefix", default=PREFIX, help="output file prefix")
    a = ap.parse_args()
    if a.wav: WAV = a.wav
    if a.yt: YT = a.yt
    if a.work: WORK = a.work
    if a.end: VIDEO_END = a.end
    if a.chunk: CHUNK = a.chunk
    if a.out_dir: OUT_DIR = a.out_dir
    if a.prefix: PREFIX = a.prefix
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"wav={WAV}\nyt={YT}\nwork={WORK}\nend={VIDEO_END} chunk={CHUNK}\n"
        f"out={OUT_DIR / (PREFIX + '.spoken.srt')}",
        flush=True,
    )

    yt = YT.read_text(encoding="utf-8").split()
    chunks = split_chunks()
    spoken: list[dict] = []
    for i, wav, offset in chunks:
        of = WORK / f"chunk-{i:02d}"
        print(f"CHUNK {i:02d} offset={offset:.1f} -> {wav.name}", flush=True)
        run_whisper(wav, of)
        part = spoken_from_json(of.with_suffix(".json"), offset)
        spoken.extend(part)
        print(f"  tokens={len(part)} total={len(spoken)} last_t={spoken[-1]['t1'] if spoken else 0:.2f}", flush=True)
        # incremental dump so a kill still leaves a usable prefix
        (WORK / "spoken.json").write_text(
            json.dumps({"n": len(spoken), "words": spoken}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    aligned = align(spoken, yt)
    out_dir = OUT_DIR
    write_srt(aligned, out_dir / f"{PREFIX}.spoken.srt")
    payload = {
        "source_video": f"{PREFIX} / {WAV}",
        "words_from": "youtube-watch-page-unique",
        "times_from": "whisper.cpp-small-max-len-1-on-full.wav",
        "not_whisper_script": True,
        "spoken_tokens": len(spoken),
        "youtube_words": len(yt),
        "aligned": len(aligned),
        "words": [
            {"i": i, "w": w["w"], "t0": round(w["t0"], 3), "t1": round(w["t1"], 3), "how": w["how"]}
            for i, w in enumerate(aligned)
        ],
    }
    (out_dir / f"{PREFIX}.spoken.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )
    print(
        f"DONE spoken={len(spoken)} yt={len(yt)} aligned={len(aligned)} "
        f"t0={aligned[0]['t0']:.3f} t_last={aligned[-1]['t1']:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
