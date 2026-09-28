#!/usr/bin/env python3
"""A professional sample plate for the new Egyptian-accent voices.

The request was: professional samples, with correct pronunciation. The engine
voices are chosen by ear through an audition (add_voice), and that audition is
run with the Egyptian accent tag so the voice model behind it supports Egyptian
Arabic instead of reading the text by fusha rules. This script then measures the
result instead of describing it:

  1. every sample is transcribed by the local Whisper (small) with -l ar, so the
     pronunciation can be ranked by how much of the sentence survived the
     round-trip;
  2. every sample's pitch is measured (median, 10-90 range, drift inside it);
  3. the plate is written as one MP3 with a 660 Hz marker before each sample --
     the number of beeps is the sample number -- plus a JSON record and a text
     label list.

Whisper is a comparison, not a judge: it also mishears correct Egyptian. It is
used here only to rank, never to pass or fail.

    python3 scripts/build_pro_voice_samples.py --build
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from measure_voice_pitch import report as pitch_report  # noqa: E402

SR = 22050
TONES = 660.0
VOICES = ROOT / "work/into-the-wild/auditions/pro-voices"
NARRATOR_BANK = ROOT / "library/voices/film-dub-narrator/voice-film-bank.mp3"


def ffmpeg() -> list[str]:
    import imageio_ffmpeg
    return [imageio_ffmpeg.get_ffmpeg_exe()]


def whisper_cli() -> Path:
    return ROOT / ".cache/tools/whisper.cpp/build/bin/whisper-cli"


def asr(path: Path, work: Path) -> str:
    """Transcribe one sample; returns the text as the local Whisper heard it."""
    wav = work / (path.stem + ".16k.wav")
    subprocess.run(ffmpeg() + ["-y", "-v", "error", "-i", str(path),
                               "-ar", "16000", "-ac", "1", str(wav)], check=True)
    out = subprocess.run([str(whisper_cli()), "-m", str(ROOT / ".cache/models/whisper-ggml/ggml-small.bin"),
                          "-f", str(wav), "-l", "ar", "-nt", "-t", "2"],
                         capture_output=True, text=True, check=True)
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return " ".join(lines).strip()


SENTENCE = ("بقول لك ايه تيجي نهرب بكلمك بجد مش انت الدنيا صعبه حواليك مشاكل البيت "
            "كترت ما كملتش مع البنت اللي بتحبها واكيد شغال وظيفه ما بتحبهاش")
NARRATION = ("الحكايه اللي جايه دي حقيقيه وكل كلمه فيها حصلت بجد واحد ساب بيته وشغله "
             "وفلوسه ومشي في الصحرا لوحده مش عشان يهرب بس عشان يعرف هو مين")
KNOWN = {"v17-a": SENTENCE, "v18-a": SENTENCE, "v19-a": SENTENCE,
         "v17-b": NARRATION, "v18-b": NARRATION, "v19-b": NARRATION}


def normalize(text: str) -> str:
    """Fold the spellings that are not pronunciation: hamza forms, ta marbuta,
    alef maqsura, tashkeel. ق/ج stay apart on purpose -- they are the sounds."""
    text = re.sub(r"[\u064B-\u0652\u0640]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return re.sub(r"\s+", " ", text).strip()


def score(asr_text: str, expected: str | None) -> dict | None:
    if not expected:
        return None
    want, got = normalize(expected), normalize(asr_text)
    same = difflib.SequenceMatcher(None, want.replace(" ", ""), got.replace(" ", ""),
                                   autojunk=False).ratio()
    ops = [o for o in difflib.SequenceMatcher(None, want.split(), got.split(),
                                              autojunk=False).get_opcodes() if o[0] != "equal"]
    wrong = []
    for tag, i1, i2, j1, j2 in ops:
        wrong.append(((" ".join(want.split()[i1:i2]) or "—"),
                      (" ".join(got.split()[j1:j2]) or "—")))
    return {"char_match": round(same, 3), "word_errors": len(wrong),
            "diff": [{"expected": w, "heard": h} for w, h in wrong]}


def tone_reel(n: int, sr: int, on: float = 0.16, off: float = 0.14) -> np.ndarray:
    gap = np.zeros(int(off * sr), dtype=np.float32)
    beep = 0.5 * np.sin(2 * np.pi * TONES * np.arange(int(on * sr)) / sr).astype(np.float32)
    return np.concatenate([np.concatenate([beep, gap]) for _ in range(n)])


def level(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    top = float(np.max(np.abs(x))) or 1.0
    return (x * (peak / top)).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true", help="اكتب الملف الصوتي")
    ap.add_argument("--out", type=Path, default=ROOT / "previews/pro-voices.mp3")
    ap.add_argument("--json", type=Path,
                    default=ROOT / "work/into-the-wild/auditions/pro-voices.json")
    ap.add_argument("--txt", type=Path,
                    default=ROOT / "work/into-the-wild/auditions/pro-voices.txt")
    ap.add_argument("--narrator", type=float, nargs=2, default=(24.0, 36.0),
                    metavar=("START", "END"), help="مقطع المرجع البشري بالثواني")
    a = ap.parse_args()

    work = ROOT / "work/into-the-wild/build/pro-voices"
    work.mkdir(parents=True, exist_ok=True)

    items: list[tuple[str, Path, float | None]] = []
    for n, voice in enumerate(("v17", "v18", "v19"), start=1):
        items.append((f"voice-{16 + n} — الجملة التي اشتكيت من نطقها",
                      VOICES / f"{voice}-a.mp3", None))
        items.append((f"voice-{16 + n} — سطر سردي جديد (نطق مختلف)",
                      VOICES / f"{voice}-b.mp3", None))
    ref = work / "narrator-ref.mp3"
    subprocess.run(ffmpeg() + ["-y", "-v", "error", "-ss", f"{a.narrator[0]}",
                               "-to", f"{a.narrator[1]}", "-i", str(NARRATOR_BANK),
                               "-ac", "1", str(ref)], check=True)
    items.append(("المرجع البشري — الراوي الأصلي (نطق صحيح بالتعريف)",
                  ref, a.narrator[1] - a.narrator[0]))

    rows, reel, labels = [], [], []
    for n, (label, path, _) in enumerate(items, start=1):
        x, sr = sf.read(str(path), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != SR:
            import scipy.signal as sp
            x = sp.resample_poly(x, SR, sr).astype(np.float32)
        p = pitch_report(path.stem, x, SR, None, True)
        text = asr(path, work)
        sc = score(text, KNOWN.get(path.stem))
        x = level(x)
        reel += [tone_reel(n, SR), x]
        rows.append({"n": n, "label": label, "file": str(path.relative_to(ROOT)),
                     "seconds": round(len(x) / SR, 2),
                     "median_hz": p["median"], "p10_p90": [p["p10"], p["p90"]],
                     "drift_semis": p["drift_semis"], "asr": text, "score": sc})
        tail = (f"مطابقة {sc['char_match']:.0%} · أخطاء {sc['word_errors']}" if sc else "بلا نص مرجعي")
        labels.append(f"{n:02d} {label} — {p['median']:.0f} هرتز · {len(x) / SR:.1f} ث · {tail}\n"
                      f"    {text}")
        print(f"{n:02d} {label[:40]:<42}{len(x) / SR:>6.1f}s {p['median']:>7.1f}Hz  "
              f"{tail:<22}{text[:52]}")

    a.json.write_text(json.dumps({"target_sr": SR, "tones_hz": TONES, "rows": rows},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    a.txt.write_text("\n".join(labels) + "\n", encoding="utf-8")
    print(f"\n  → {a.json.relative_to(ROOT)}\n  → {a.txt.relative_to(ROOT)}")
    if not a.build:
        print("  (بلا ملف صوتي: --build)")
        return 0

    wav = work / "pro-voices.wav"
    sf.write(str(wav), np.concatenate(reel), SR, subtype="PCM_16")
    subprocess.run(ffmpeg() + ["-y", "-v", "error", "-i", str(wav),
                               "-c:a", "libmp3lame", "-b:a", "160k", str(a.out)], check=True)
    print(f"  ✓ {a.out.relative_to(ROOT)}  ({a.out.stat().st_size / 1024:.0f} ك.بايت)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
