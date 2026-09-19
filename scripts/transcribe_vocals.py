#!/usr/bin/env python
"""Transcribe the isolated vocal stem to get word timings the mix would not give.

Running whisper on the mix costs it the boundaries: only about 15 percent of the
script's words end up anchored by a real detection, the rest is interpolated.
Those interpolated edges are what make fine retiming worse than none. On the
separated dialogue stem the same model yields 6469 words for a 7004-word script,
so 93 percent of words rest on a measured edge.

Each separated segment is 110.5 s with a 0.5 s crossfade, so segment i starts at
i * 110.0 s of source. Timings are taken per segment and shifted by that offset
rather than transcribing one concatenated file, because mp3 round-tripping added
ten seconds of encoder padding across the twenty-one segments.

Results are committed as they are produced: this takes about seventy minutes and
the sandbox wipes without warning.

Usage:
    python scripts/transcribe_vocals.py SLUG [--resume] [--commit-every 3]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

SEG_S, STRIDE_S = 110.5, 110.0
WHISPER = ".cache/tools/whisper.cpp/build/bin/whisper-cli"
MODEL = ".cache/models/whisper-ggml/ggml-small.bin"
BRANCH = "arena/01a09fa1-dub"


def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s)
    for x, y in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"),
                 ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(x, y)
    return s.strip()


def words_from_whisper(path):
    d = json.loads(Path(path).read_bytes().decode("utf-8", "surrogateescape"))
    out, cur = [], None
    for seg in d.get("transcription", []):
        for t in seg.get("tokens", []):
            txt = t.get("text", "")
            o = t.get("offsets", {})
            a, b = o.get("from", 0) / 1000.0, o.get("to", 0) / 1000.0
            if not txt.strip("[]") or txt.startswith("["):
                continue
            if txt.startswith(" ") or cur is None:
                if cur:
                    out.append(cur)
                cur = [txt.strip(), a, b]
            else:
                cur[0] += txt
                cur[2] = b
        if cur:
            out.append(cur)
            cur = None
    return [{"w": w, "t0": a, "t1": b} for w, a, b in out if norm(w)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--work", default=".cache/voc/segments")
    ap.add_argument("--seg-dir", default=".cache/separation/segmented/segments")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--commit-every", type=int, default=3)
    ap.add_argument("--limit", type=int, default=21)
    a = ap.parse_args()

    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    seg = Path(a.seg_dir)
    out = Path("work") / a.slug / "vocal-asr.json"

    done = {}
    if a.resume and out.exists():
        try:
            raw = out.read_bytes().decode("utf-8", "surrogateescape")
            done = {d["segment"]: d for d in json.loads(raw)["segments"]}
        except Exception as e:
            print(f"  ملف سابق تالف ({type(e).__name__}) — أُعيد من الصفر")
            done = {}

    since_commit = 0
    for i in range(a.limit):
        key = f"{i:02d}"
        voc = seg / f"work-{key}" / "estimated-dialogue.wav"
        if not voc.exists():
            print(f"  [{key}] لا ملف — تخطّي")
            continue
        if key in done:
            print(f"  [{key}] منجز ({len(done[key]['words'])} كلمة)")
            continue
        of = work / f"seg-{key}"
        t0 = time.time()
        subprocess.run([WHISPER, "-m", MODEL, "-f", str(voc), "-l", "ar",
                        "-t", str(a.threads), "-ojf", "-sow", "-ml", "160",
                        "-of", str(of)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = words_from_whisper(f"{of}.json")
        off = i * STRIDE_S
        for w in ws:
            w["t0"] = round(w["t0"] + off, 3)
            w["t1"] = round(w["t1"] + off, 3)
        done[key] = {"segment": key, "offset_s": off, "words": ws}
        print(f"  [{key}] {len(ws):4d} كلمة · {time.time()-t0:.0f}ث · عند {off:.0f}ث", flush=True)

        # whisper emits lone surrogates; utf-8 refuses them outright
        out.write_bytes(json.dumps({"slug": a.slug, "model": "ggml-small",
                                    "segment_s": SEG_S, "stride_s": STRIDE_S,
                                    "segments": [done[k] for k in sorted(done)]},
                                   ensure_ascii=False, indent=1).encode("utf-8", "surrogateescape"))
        since_commit += 1
        if since_commit >= a.commit_every:
            subprocess.run(["git", "add", "-A", str(out)], check=True)
            r = subprocess.run(["git", "commit", "-q", "-m",
                                f"Vocal-stem transcription through segment {key} ({len(done)}/21)\n\n"
                                f"Transcribed from the separated dialogue stem, shifted by the segment\n"
                                f"stride. Committed incrementally so a sandbox wipe cannot take it."],
                               capture_output=True, text=True)
            if r.returncode == 0:
                p = subprocess.run(["git", "push", "origin", BRANCH],
                                   capture_output=True, text=True)
                print(f"      ✓ حُفظ {len(done)}/21" + ("" if p.returncode == 0 else " (الدفع فشل)"))
            since_commit = 0

    total = sum(len(s["words"]) for s in done.values())
    print(f"\n  الإجمالي: {total} كلمة من {len(done)} مقطع")


if __name__ == "__main__":
    main()
