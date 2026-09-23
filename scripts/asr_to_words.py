#!/usr/bin/env python3
"""Turn one whisper JSON into the word-timing format align_vocal_timings.py reads.

The Sindbad pipeline transcribed 110 s stems one at a time and stitched them with
a stride, so its converter carries a segment index and an offset. A film that
arrives whole has neither: there is one audio file, one whisper run, and every
timestamp is already absolute. This writes that single-run result in the same
shape so the existing aligner reads it unchanged.

Token times come from `t_dtw` when whisper was asked for it. That field is the
cross-attention alignment and it lands inside the token rather than at the edge
of the segment that contains it -- on a 150 s probe the segment-level offsets
put whole phrases a second wide, which is wider than the sync target. `t_dtw`
is written in units of ten milliseconds; without it the token offsets are used.

    python scripts/asr_to_words.py .cache/itw/pilot-asr.json \
        work/into-the-wild/vocal-asr.json --script work/into-the-wild/text/pilot-words.txt \
        --script-out work/into-the-wild/pilot-script.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from transcribe_vocals import words_from_whisper  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_bytes().decode("utf-8", "surrogateescape"))


def words_with_dtw(path: Path) -> list[dict]:
    """Tokens grouped into words, timed by the DTW alignment."""
    d = load(path)
    out, cur = [], None
    for seg in d.get("transcription", []):
        for t in seg.get("tokens", []):
            txt = t.get("text", "")
            if not txt.strip("[]") or txt.startswith("["):
                continue
            o = t.get("offsets", {})
            a, b = o.get("from", 0) / 1000.0, o.get("to", 0) / 1000.0
            dtw = t.get("t_dtw", -1)
            if dtw is not None and dtw >= 0:
                # t_dtw is the alignment position in centiseconds, and it marks
                # where the token starts rather than where its segment does.
                a = dtw / 100.0
                if b < a:
                    b = a
            if txt.startswith(" ") or cur is None:
                if cur and cur[0].strip():
                    out.append(cur)
                cur = [txt.strip(), a, b]
            else:
                cur[0] += txt
                cur[2] = max(cur[2], b)
    if cur and cur[0].strip():
        out.append(cur)
    return [{"w": w, "t0": round(a, 3), "t1": round(max(b, a + 0.02), 3)}
            for w, a, b in out if w.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("whisper_json", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--script", type=Path, default=None,
                    help="plain-text script; word tokens are written for the aligner")
    ap.add_argument("--script-out", type=Path, default=None)
    a = ap.parse_args()

    ws = words_with_dtw(a.whisper_json)
    if not ws:
        ws = words_from_whisper(a.whisper_json)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(json.dumps(
        {"slug": a.slug, "model": "ggml-small", "beam": 5, "dtw": True,
         "segments": [{"segment": "00", "offset_s": 0.0, "words": ws}]},
        ensure_ascii=False, indent=1).encode("utf-8", "surrogateescape"))
    span = (ws[-1]["t1"] - ws[0]["t0"]) if ws else 0.0
    print(f"  {len(ws)} كلمة · آخر توقيت {ws[-1]['t1']:.1f}ث" if ws else "  لا كلمات")
    if ws and span:
        print(f"  معدل {len(ws) / span:.2f} كلمة/ثانية")

    if a.script and a.script_out:
        text = a.script.read_text(encoding="utf-8")
        toks = [t for t in re.split(r"\s+", text.strip()) if t]
        a.script_out.write_bytes(json.dumps(
            {"slug": a.slug, "source": a.script.name,
             "words": [{"w": t, "t0": None, "t1": None} for t in toks]},
            ensure_ascii=False, indent=1).encode("utf-8", "surrogateescape"))
        print(f"  نص السكربت: {len(toks)} كلمة → {a.script_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
