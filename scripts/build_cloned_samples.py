#!/usr/bin/env python
"""Cloned samples: the engine's words wearing each human narrator's voice.

The user's request: «اريدك انت ان تولد عينات مستنسخة منها وانا اختار» -- the human
voices in his library are real narrators, and he wants samples *cloned from them* to
choose between. The neural route is closed in this sandbox (every host that carries
XTTS/Seed-VC weights answers 000), so the clone here is the classical one, and it is
honest about what it is:

      content   the engine voice-13, speaking one fixed Egyptian line
   +  pitch     moved to the reference narrator's measured median F0, with
                rubberband formant=preserved, capped at +-6 semitones and corrected
                against the result instead of the request
   +  timbre    the difference between the narrator's long-term spectrum and the
                take's own, truncated to 40 cepstral coefficients and applied
                overlap-add with the original phase -- so the colour moves and the
                words stay where they were

Each reference is a **solo** window of that narrator (no turn-taking inside it), found
by measurement, because a timbre reference taken from a dialogue is two people.

    python3 scripts/build_cloned_samples.py                # scan only
    python3 scripts/build_cloned_samples.py --build        # write the reel + parts
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from voice_convert import (ffmpeg, long_term_envelope, median_f0,  # noqa: E402
                           apply_envelope)
from measure_voice_pitch import report as pitch_report  # noqa: E402
from build_human_samples2 import SOURCES, load, runs_of, turns_in  # noqa: E402

SR = 22050
WORK = Path("/tmp/clone-samples")
AUD = Path("work/into-the-wild/auditions")
OUT = Path("previews/cloned-voices.mp3")
LABELS = AUD / "cloned-voices.txt"
PARTS = Path("work/into-the-wild/auditions/cloned")
README = AUD / "cloned-voices.json"
TEST_TAKE = Path("work/into-the-wild/takes-voice13/g000.mp3")
TEST_SECONDS = 11.0
PEAK = 0.72
TONES = 660.0
ORDER = ["original-narrator", "doctor", "hajj", "prostudio", "shorts",
         "0911", "snaptik", "animals"]
WINDOW = 12.0

# References that are better than a window cut from the film. The doctor's narrator
# was approved by the user on 2026-09-17 and its own bank is kept at
# library/voices/film-dub-narrator with a documented median of 206 Hz -- cleaner than
# anything measured out of the film mix (the raw film measures ~112 Hz on windows
# where two pitch methods disagree, i.e. a polluted reference). The human recordings
# themselves are never modified here: they are read as a target, nothing else.
REF_OVERRIDE = {
    "doctor": Path("library/voices/film-dub-narrator/voice-film-bank.mp3"),
}


def solo_window(x: np.ndarray, sr: int, runs: list[dict]) -> dict:
    """The best window where one narrator speaks alone: the timbre reference."""
    span = len(x) / sr
    best = None
    for t0 in np.arange(0, max(0.1, span - WINDOW), 0.5):
        t1 = t0 + WINDOW
        n_turn, n_runs = turns_in(runs, t0, t1)
        speech = sum(min(r["t1"], t1) - max(r["t0"], t0)
                     for r in runs if r["t1"] > t0 and r["t0"] < t1)
        frac = speech / WINDOW
        if n_turn > 0 or frac < 0.6 or n_runs < 1:
            continue
        score = frac + 0.05 * n_runs
        if best is None or score > best["score"]:
            best = {"t0": round(float(t0), 2), "frac": round(float(frac), 2),
                    "score": round(float(score), 2)}
    return best or {"t0": 0.0, "frac": 0.0, "score": 0.0}


def robust_f0(x: np.ndarray, sr: int) -> float:
    """Reference pitch with the octave mistakes filtered out, then the octave chosen.

    voice_convert.median_f0 has no octave guard: measured on the doctor's window it
    returned 105.5 Hz for a narrator whose own approved reference sits near 206 Hz --
    an octave down, and shifting the engine voice to that would have moved it the
    wrong way. Here the per-frame pitches that sit more than a third of an octave
    from the window's own median are dropped first (the same rule the film's
    measurements use), and afterwards the octave is resolved by asking which
    interpretation the engine voice can actually reach.
    """
    r = pitch_report("ref", x, sr, None, False)
    return float(r.get("median") or 0.0)


def nearest_octave(f_ref: float, f_content: float, max_shift: float = 6.0) -> tuple[float, str]:
    """The octave of a target that is reachable from the content's own pitch."""
    best, why = f_ref, "كما قيست"
    best_cost = abs(12 * np.log2(max(f_ref, 1e-6) / f_content))
    for factor, name in ((2.0, "أوكتاف أعلى"), (0.5, "أوكتاف أسفل")):
        cand = f_ref * factor
        cost = abs(12 * np.log2(cand / f_content))
        if cost < best_cost - 0.01:
            best, best_cost, why = cand, cost, name
    if best_cost > max_shift:
        why += f" · يحتاج {best_cost:.1f} (يُقصّ عند {max_shift:g})"
    return best, why


def prep_test() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "content.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-t", f"{TEST_SECONDS}",
                    "-i", str(TEST_TAKE), "-ar", str(SR), "-ac", "1",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def shift_to(x: np.ndarray, f_target: float, work: Path, formant="preserved",
             max_shift=6.0) -> tuple[np.ndarray, float]:
    """Rubberband the take to the target's note, corrected against the result."""
    f_src = median_f0(x, SR)
    if f_src <= 0 or f_target <= 0:
        return x, 0.0
    factor = f_target / f_src
    cap = 2 ** (max_shift / 12.0)
    factor = min(max(factor, 1 / cap), cap)
    src = work / "shift-in.wav"
    dst = work / "shift-out.wav"
    sf.write(str(src), x.astype(np.float32), SR)
    total = factor
    for _ in range(3):
        if abs(12 * np.log2(total)) < 0.05:
            break
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(src),
                        "-af", f"rubberband=pitch={total:.6f}:formant={formant}"
                               ":pitchq=quality",
                        "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
                       check=True)
        probe, _ = sf.read(str(dst))
        f_mid = median_f0(np.asarray(probe, float), SR)
        if f_mid <= 0:
            break
        residual = f_target / f_mid
        if abs(12 * np.log2(residual)) < 0.4:
            break
        total *= residual
    y, _ = sf.read(str(dst))
    return np.asarray(y, float), 12 * np.log2(total)


def tone_reel(n: int, sr: int, on: float = 0.16, off: float = 0.14) -> np.ndarray:
    gap = np.zeros(int(off * sr), dtype=np.float32)
    beep = 0.5 * np.sin(2 * np.pi * TONES * np.arange(int(on * sr)) / sr).astype(np.float32)
    return np.concatenate([np.concatenate([beep, gap]) for _ in range(n)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--strength", type=float, default=0.9)
    ap.add_argument("--cepstra", type=int, default=40)
    ap.add_argument("--json", default=str(README))
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)

    content_path = prep_test()
    x, _ = sf.read(str(content_path))
    x = np.asarray(x, float)
    f_content = median_f0(x, SR)
    env_content, _ = long_term_envelope(x, SR, cepstra=a.cepstra)
    print(f"المحتوى: {TEST_TAKE.name} · {len(x) / SR:.1f} ث · نبرته {f_content:.1f} هرتز")
    print(f"القوة {a.strength} · سيبسترال {a.cepstra} · إزاحة النبرة مسقوفة ±6 أنصاف\n")
    print(f"{'صوت':>18}{'مرجع':>10}{'نبرة المرجع':>13}{'نبرة بعد':>10}{'إزاحة':>8}"
          f"{'طابع قبل':>10}{'بعد':>8}{'أُغلق':>7}")

    rows, reel, labels = [], [], []
    for n, key in enumerate(ORDER, start=1):
        if key not in SOURCES:
            continue
        try:
            src_x, sr, label = load(key)
        except SystemExit as exc:
            print(f"  ! {key}: {exc}")
            continue
        override = REF_OVERRIDE.get(key)
        if override is not None and override.exists():
            wav = WORK / f"{key}-bank.wav"
            if not wav.exists():
                subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(override),
                                "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le",
                                str(wav)], check=True)
            src_x, sr = sf.read(str(wav), dtype="float32", always_2d=False)
            if getattr(src_x, "ndim", 1) > 1:
                src_x = src_x.mean(axis=1)
            win = {"t0": 0.0, "frac": 1.0, "score": 1.0}
            t0 = 0.0
            ref = np.asarray(src_x, float)[: int(WINDOW * sr)].copy()
            label = label + " (من بنكه المعتمد)"
        else:
            runs = runs_of(src_x, sr)
            win = solo_window(src_x, sr, runs)
            t0 = win["t0"]
            ref = src_x[int(t0 * sr):int((t0 + WINDOW) * sr)].copy()
        f_raw = robust_f0(ref, sr)
        f_ref, octave_note = nearest_octave(f_raw, f_content)
        env_ref, _ = long_term_envelope(ref, sr, cepstra=a.cepstra)

        shifted, semis = shift_to(x, f_ref, WORK)
        env_shifted, _ = long_term_envelope(shifted, SR, cepstra=a.cepstra)
        before = float(np.mean(np.abs(env_ref - env_shifted)))
        ratio = env_ref - env_shifted
        out = apply_envelope(shifted, SR, ratio, strength=a.strength)
        f_out = median_f0(out, SR)
        env_out, _ = long_term_envelope(out, SR, cepstra=a.cepstra)
        after = float(np.mean(np.abs(env_ref - env_out)))
        closed = 100 * (1 - after / before) if before > 0 else 0.0

        p = float(np.abs(out).max()) or 1.0
        out = out * (PEAK / p)
        part = PARTS / f"clone-{n:02d}-{key}.mp3"
        sf.write(str(WORK / "part.wav"), out.astype(np.float32), SR)
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(WORK / "part.wav"),
                        "-b:a", "160k", str(part)], check=True)

        rows.append({"n": n, "key": key, "label": label, "ref_start": t0,
                     "ref_frac": win["frac"], "ref_hz_raw": round(f_raw, 1),
                     "octave": octave_note, "ref_hz": round(f_ref, 1),
                     "out_hz": round(f_out, 1), "semitone_shift": round(semis, 2),
                     "envelope_before_db": round(before, 3),
                     "envelope_after_db": round(after, 3),
                     "closed_pct": round(closed), "file": str(part)})
        print(f"{label[:18]:>18}{t0:>10.1f}{f_ref:>13.1f}{f_out:>10.1f}{semis:>+8.2f}"
              f"{before:>10.3f}{after:>8.3f}{closed:>6.0f}%   {octave_note}")

        if a.build:
            reel += [tone_reel(n, SR), out.astype(np.float32)]
            labels.append(f"{n:02d} {label} — نبرة المرجع {f_ref:.0f} هرتز · "
                          f"إزاحة {semis:+.2f} نصف نغمة · طابع أُغلق {closed:.0f}% · "
                          f"{octave_note}")

    Path(a.json).write_text(json.dumps(
        {"content": {"take": str(TEST_TAKE), "seconds": round(len(x) / SR, 2),
                     "f0_hz": round(f_content, 1)},
         "strength": a.strength, "cepstra": a.cepstra, "rows": rows},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not a.build:
        print(f"\n  → {a.json} (بلا ملف صوتي: --build)")
        return 0

    y = np.concatenate([c.astype(np.float32) for c in reel])
    sf.write(str(WORK / "reel.wav"), y, SR)
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(WORK / "reel.wav"),
                    "-b:a", "160k", str(OUT)], check=True)
    LABELS.write_text("\n".join(labels) + "\n", encoding="utf-8")
    print(f"\n  ✓ {OUT} · {len(rows)} عيّنات مستنسخة · {len(y) / SR:.1f} ث · "
          f"{OUT.stat().st_size} بايت")
    print(f"  ✓ الأصوات المفردة في {PARTS}/")
    print(f"  → {LABELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
