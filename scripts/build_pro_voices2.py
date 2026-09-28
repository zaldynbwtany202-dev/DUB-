#!/usr/bin/env python3
"""A second plate of candidate voices -- measured on the film's own opening line.

The first plate could only be judged by ear. This one also answers the question
the film build actually asks: how much does this voice have to be sped up to fit
the original timing? The opening group has 365 characters and a 20.42 s window
(about 18 characters a second, which is fast), so a slow-reading voice ends up at
atempo 1.6x and sounds strained, while a fast-reading one needs almost nothing.
Two takes per voice are compared for that, and for pitch steadiness -- the engine
jumps the tone between takes, and the steadiest take is the one the film can use.

    python3 scripts/build_pro_voices2.py --build
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

from build_pro_voice_samples import (SR, TONES, asr, level, normalize,  # noqa: E402
                                     score, tone_reel)
from measure_voice_pitch import report as pitch_report  # noqa: E402

SRC = ROOT / "work/into-the-wild/auditions/pro-voices2"
WINDOW = 20.42          # seconds the opening group must fit into
KNOWN = ("بقول لك ايه تيجي نهرب بكلمك بجد مش انت الدنيا صعبه حواليك مشاكل البيت كترت "
         "ما كملتش مع البنت اللي بتحبها واكيد شغال وظيفه ما بتحبهاش ولسه ما حققتش حلمك "
         "اللي نفسك فيه يلا بينا يا عم خلينا نخلص من الحياه المقرفه دي تعال نروح الصحرا "
         "نربنا معزتين ولا حاجه حياه بسيطه ما فيهاش قلق او مشاكل مش انت كل شويه تتردد "
         "انك عايز تعيش لوحدك وان نعيم الجهل احسن بكتير من جحيم الوعي")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "previews/voice-pro2.mp3")
    ap.add_argument("--json", type=Path,
                    default=ROOT / "work/into-the-wild/auditions/voice-pro2.json")
    ap.add_argument("--txt", type=Path,
                    default=ROOT / "work/into-the-wild/auditions/voice-pro2.txt")
    a = ap.parse_args()

    work = ROOT / "work/into-the-wild/build/pro-voices2"
    work.mkdir(parents=True, exist_ok=True)
    chars = len(KNOWN)

    rows, reel, labels, picks = [], [], [], {}
    n = 0
    for voice in ("v20", "v21", "v22"):
        takes = []
        for k in (1, 2):
            path = SRC / f"{voice}-{k}.mp3"
            x, sr = sf.read(str(path), dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            if sr != SR:
                import scipy.signal as sp
                x = sp.resample_poly(x, SR, sr).astype(np.float32)
            p = pitch_report(path.stem, x, SR, None, True)
            secs = len(x) / SR
            takes.append({"file": path.name, "seconds": round(secs, 2),
                          "chars_per_s": round(chars / secs, 2),
                          "atempo_needed": round(secs / WINDOW, 3),
                          "median_hz": p["median"], "p10_p90": [p["p10"], p["p90"]],
                          "drift_semis": p["drift_semis"], "audio": x})
        med = float(np.median([t["median_hz"] for t in takes]))
        for t in takes:
            t["semis_from_median"] = round(float(12 * np.log2(t["median_hz"] / med)), 2)
        # The film wants the take that needs the least speed-up and sits closest to
        # the pair's own median tone: one criterion, not two, so the choice is not
        # "fastest but wobbly".
        best = sorted(takes, key=lambda t: (t["atempo_needed"] + 0.4 * abs(t["semis_from_median"])))[0]
        picks[voice] = best["file"]

        n += 1
        text = asr(SRC / best["file"], work)
        sc = score(text, KNOWN)
        rows.append({"n": n, "voice": f"voice-{int(voice[1:])}", "takes": [
                     {k: v for k, v in t.items() if k != "audio"} for t in takes],
                     "picked": best["file"], "asr": text, "score": sc,
                     "atempo_needed": best["atempo_needed"]})
        reel += [tone_reel(n, SR), level(best["audio"])]
        labels.append(
            f"{n:02d} voice-{int(voice[1:])} — افتتاحية الفيلم كاملة — نبرة {best['median_hz']:.0f} هرتز · "
            f"طبيعي {best['seconds']:.1f} ث ({best['chars_per_s']:.1f} حرف/ث) · تسريع مطلوب {best['atempo_needed']:.2f}× · "
            f"انتشار الأخذتين {abs(takes[0]['median_hz'] - takes[1]['median_hz']) / best['median_hz'] * 100:.0f}% · "
            f"مطابقة {sc['char_match']:.0%} · أخطاء {sc['word_errors']}\n    {text}")
        spread = abs(takes[0]["median_hz"] - takes[1]["median_hz"])
        print(f"{n:02d} voice-{int(voice[1:])}  طبيعي {best['seconds']:>5.1f}s  "
              f"({best['chars_per_s']:>4.1f} حرف/ث)  تسريع {best['atempo_needed']:.2f}×  "
              f"نبرة {best['median_hz']:>6.1f} Hz  انتشار {spread:.1f} Hz  "
              f"مطابقة {sc['char_match']:.0%} أخطاء {sc['word_errors']}")

    # The human narrator, as the anchor for what "correct" sounds like; different text.
    n += 1
    ref = work / "narrator-ref.mp3"
    x, sr = sf.read(str(ref), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        import scipy.signal as sp
        x = sp.resample_poly(x, SR, sr).astype(np.float32)
    p = pitch_report("narrator", x, SR, None, True)
    reel += [tone_reel(n, SR), level(x)]
    labels.append(f"{n:02d} الراوي البشري الأصلي — شاهد النطق الصحيح (نص مختلف) — {p['median']:.0f} هرتز")
    print(f"{n:02d} الراوي البشري — {p['median']:.1f} Hz")

    a.json.write_text(json.dumps({"window_s": WINDOW, "chars": chars, "picks": picks,
                                  "rows": rows}, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    a.txt.write_text("\n".join(labels) + "\n", encoding="utf-8")
    if not a.build:
        print("  (بلا ملف: --build)")
        return 0
    wav = work / "voice-pro2.wav"
    sf.write(str(wav), np.concatenate(reel), SR, subtype="PCM_16")
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav),
                    "-c:a", "libmp3lame", "-b:a", "160k", str(a.out)], check=True)
    print(f"  ✓ {a.out.relative_to(ROOT)} ({a.out.stat().st_size / 1024:.0f} ك.بايت)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
