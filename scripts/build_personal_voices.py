#!/usr/bin/env python3
"""The personal plate: fresh voices saying the project's own line, measured.

One segment per candidate on the same sentence (the one from the clip the user
uploaded), plus the current locked voice for comparison and the human narrator as
the anchor for "what correct sounds like". Every segment carries its own numbers:
pitch, how fast it reads (the clip's window needs about 11.2 characters a second),
and how the local Whisper heard it against the written line.

    .venv/bin/python scripts/build_personal_voices.py --build
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from build_pro_voice_samples import SR, asr, level, score, tone_reel  # noqa: E402
from measure_voice_pitch import report as pitch_report  # noqa: E402

CLIP_TEXT = ("إن الإنسان لو مش هيبقى مبسوط بفنجان قهوة عمره ما هيبقى مبسوط لو عنده حتى يخت "
             "يعني فكرة ان انت تستخلص الانبساط أو السعادة من الحاجة اللي انت بتمر بيها "
             "أو من الوضع اللي انت فيه دلوقتي دي مهارة واختيار")
CLIP_WINDOW = 14.24

SEGMENTS = [
    ("voice-23 — سطر المقطع المرفوع (مرشّح جديد)", "work/personal-voices/v23-clip.mp3", CLIP_TEXT),
    ("voice-24 — سطر المقطع المرفوع (مرشّح جديد)", "work/personal-voices/v24-clip.mp3", CLIP_TEXT),
    ("voice-25 — سطر المقطع المرفوع (مرشّح جديد)", "work/personal-voices/v25-clip.mp3", CLIP_TEXT),
    ("voice-21 — الصوت الحالي على السطر نفسه (للمقارنة)", "work/clip-happiness/takes/g000.mp3", CLIP_TEXT),
    ("الراوي البشري الأصلي — شاهد النطق الصحيح (نص مختلف)",
     "work/into-the-wild/build/pro-voices2/narrator-ref.mp3", None),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "previews/voice-personal.mp3")
    ap.add_argument("--json", type=Path, default=ROOT / "work/personal-voices/personal-voices.json")
    ap.add_argument("--txt", type=Path, default=ROOT / "work/personal-voices/personal-voices.txt")
    a = ap.parse_args()

    work = ROOT / "work/personal-voices"
    work.mkdir(parents=True, exist_ok=True)
    reel, rows, labels = [], [], []
    for n, (label, rel, expected) in enumerate(SEGMENTS, start=1):
        path = ROOT / rel
        x, sr = sf.read(str(path), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != SR:
            import scipy.signal as sp
            x = sp.resample_poly(x, SR, sr).astype(np.float32)
        p = pitch_report(path.stem, x, SR, None, True)
        secs = len(x) / SR
        text = asr(path, work)
        sc = score(text, expected)
        cps = round(len(expected.replace(" ", "")) / secs, 2) if expected else None
        need = round(secs / CLIP_WINDOW, 3) if expected else None
        reel += [tone_reel(n, SR), level(x)]
        rows.append({"n": n, "label": label, "file": rel, "seconds": round(secs, 2),
                     "median_hz": p["median"], "p10_p90": [p["p10"], p["p90"]],
                     "drift_semis": p["drift_semis"], "chars_per_s": cps,
                     "atempo_needed": need, "asr": text, "score": sc})
        parts = [f"{n:02d} {label}", f"نبرة {p['median']:.0f} هرتز", f"{secs:.1f} ث"]
        if cps:
            parts += [f"{cps:.1f} حرف/ث", f"تسريع مطلوب {need:.2f}×"]
        if sc:
            parts += [f"مطابقة {sc['char_match']:.0%}", f"أخطاء {sc['word_errors']}"]
        labels.append(" — ".join(parts) + f"\n    {text}")
        print(" — ".join(parts))
        print(f"    {text}")

    a.json.write_text(json.dumps({"clip_text": CLIP_TEXT, "clip_window_s": CLIP_WINDOW,
                                  "rows": rows}, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    a.txt.write_text("\n".join(labels) + "\n", encoding="utf-8")
    if not a.build:
        print("  (بلا ملف: --build)")
        return 0
    wav = work / "voice-personal.wav"
    sf.write(str(wav), np.concatenate(reel), SR, subtype="PCM_16")
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav),
                    "-c:a", "libmp3lame", "-b:a", "160k", str(a.out)], check=True)
    print(f"  ✓ {a.out.relative_to(ROOT)} ({a.out.stat().st_size / 1024:.0f} ك.بايت)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
