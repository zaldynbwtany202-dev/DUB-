#!/usr/bin/env python3
"""verify_voice.py -- is this recording the locked film-dub voice?

`voice_id` is session-scoped. The «voice-01» that narrated doctor-lecture does
not resolve in a later session, so the voice has to be re-registered with
add_voice and the user picks again from a fresh pair -- which makes a silent
substitution possible: a different voice model, an identical workflow, and 49
takes recorded before anybody notices. This script is the gate.

What measurement ruled out
-------------------------
Pitch. The first version gated on median F0, and the numbers killed it:
documented masculine voice-00 spans 154-198 Hz across four takes (median 184),
while the locked film voice spans 190-211 Hz (median 197.5). A different voice
sits *inside* the bank's pitch range, so any F0 window wide enough to accept the
bank also accepts that voice. F0 is reported as a supporting cue only, and no
gender is inferred from it.

Every timbre feature tried. voice_metric_study.py measured seven feature sets
against all the audio in this repo -- 49 takes of the locked voice, 28 of
voice-00, 4 of voice-33, i.e. 1176 same-voice pairs and 1568 different-voice
pairs. The best (z-scored MFCC mean+spread+centroid) puts the *closest*
different-voice pair at 7.13 and the *farthest* same-voice pair at 12.99: the
distributions overlap, margin 0.55x, equal-error rate 5.1%. Long-term average
spectrum, raw and standardised, did worse (0.28-0.31x). An earlier
nearest-neighbour gate calibrated on 8 bank takes looked clean (20.1 vs 33.2)
and then got 3 of 7 held-out verdicts wrong, including accepting a voice-00 take
at 10.4. Small calibration sets hide the overlap; the full study does not.

So there is NO automatic gate, and this script does not pretend to be one. What
it does: measure a probe against the bank and against the other documented
voices, print the numbers with the known error rate attached, and build an A/B
audio file so the decision is made by an ear -- the user's, which is the only
authority this project has ever accepted for voice identity.

voice-33 is also excluded as a reference: its own four locked takes are 34-195
apart in these metrics, wider than the gap between two different voices.

What is used
------------
Nearest neighbour on the spectral envelope: mean and spread of MFCC 1-12 plus the
spectral centroid, measured over 40 ms voiced frames at 16 kHz. A probe is
compared with every take in the bank and the smallest distance wins, which makes
the reading about the closest thing the voice ever did rather than about an
average. The bank's own leave-one-out spread and the distance to the other
documented voices are printed alongside, so the number can be read against the
overlap that the study measured.

Measurement only. Nothing here resamples, filters or processes audio: the
standing rule for these recordings is atempo-then-mux and nothing else.

usage:
  verify_voice.py --remap                     # re-measure the bank, write the lock
  verify_voice.py --probe takes/seg-0009.mp3  # measure one recording against the bank
  verify_voice.py --ab takes/probe.mp3        # build an A/B listening file
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "library/voices/film-dub-narrator"
LOCK = VOICE_DIR / "LOCKED_VOICE.json"
SR = 16000
FRAME = int(0.040 * SR)
HOP = int(0.020 * SR)
F0_MIN, F0_MAX = 70.0, 350.0
N_MFCC = 12
N_MELS = 26

# Calibration negatives: das-full's voice-00, documented masculine and internally
# consistent (0-40 within itself, 61+ from the film bank). Six are used to
# calibrate; the other 26 stay held out for testing the gate.
NEGATIVES = [("voice-00 das-full", f"dubs/das-full/takes-v2/seg-{i:04d}.mp3")
             for i in range(6)]

# Excluded on measurement, not on preference -- see the module docstring.
EXCLUDED = {
    "voice-33 (library/voices/locked-narrator/)":
        "its four locked takes are 34-195 apart in this metric, wider than the gap "
        "between two different voices, so they cannot calibrate a gate; a voice-33 "
        "sample false-accepted at 38.4 against a 39.6 mean-distance threshold",
}


def ffmpeg_exe() -> str:
    d = ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries"
    exe = next(d.glob("ffmpeg-linux-*"), None)
    if exe is None:
        raise SystemExit("no bundled ffmpeg -- run scripts/bootstrap_das_env.sh first")
    return str(exe)


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _voiced(x: np.ndarray) -> list[np.ndarray]:
    frames = [x[i:i + FRAME] for i in range(0, len(x) - FRAME, HOP)]
    if not frames:
        return []
    rms = np.array([float(np.sqrt(np.mean(f ** 2))) for f in frames])
    return [f for f, r in zip(frames, rms) if r > 0.25 * float(rms.max() or 1.0)]


def f0_median(frames: list[np.ndarray]) -> float | None:
    lo, hi = int(SR / F0_MAX), int(SR / F0_MIN)
    out = []
    for f in frames:
        f = f - f.mean()
        if float(np.dot(f, f)) <= 0:
            continue
        ac = np.correlate(f, f, mode="full")[len(f) - 1:][lo:hi]
        if len(ac) == 0:
            continue
        ac = ac / (ac[0] if ac[0] > 0 else 1.0)
        best = int(np.argmax(ac))
        if float(ac[best]) < 0.3:
            continue
        earlier = np.flatnonzero(ac[:best + 1] >= 0.85 * float(ac[best]))
        out.append(SR / ((int(earlier[0]) if len(earlier) else best) + lo))
    return round(float(np.median(out)), 1) if len(out) > 20 else None


def _mel_filters(n_fft: int) -> np.ndarray:
    to_mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)        # noqa: E731
    to_hz = lambda m: 700.0 * (10 ** (m / 2595.0) - 1.0)         # noqa: E731
    pts = to_hz(np.linspace(to_mel(0), to_mel(SR / 2), N_MELS + 2))
    bins = np.floor((n_fft + 1) * pts / SR).astype(int)
    fb = np.zeros((N_MELS, n_fft // 2 + 1))
    for i in range(N_MELS):
        l, c, r = bins[i], bins[i + 1], bins[i + 2]
        if c > l:
            fb[i, l:c] = (np.arange(l, c) - l) / (c - l)
        if r > c:
            fb[i, c:r] = (r - np.arange(c, r)) / (r - c)
    return fb


def timbre(x: np.ndarray) -> list[float]:
    """Mean and spread of MFCC 1..12 plus the spectral centroid: 25 numbers."""
    frames = _voiced(x)
    if len(frames) < 20:
        return []
    sig = np.array([f * np.hanning(len(f)) for f in frames])
    spec = np.abs(np.fft.rfft(sig, n=1024)) ** 2
    logmel = np.log(np.maximum(spec @ _mel_filters(1024).T, 1e-10))   # (frames, N_MELS)
    n = logmel.shape[1]
    k = np.arange(N_MFCC + 1)[:, None]                               # coefficient 0 is energy
    nn = np.arange(n)[None, :]
    dct = np.cos(np.pi * k * (2 * nn + 1) / (2 * n))                 # (N_MFCC+1, N_MELS)
    mfcc = logmel @ dct[1:].T                                        # (frames, N_MFCC)
    freqs = np.linspace(0, SR / 2, spec.shape[1])
    centroid = float((spec * freqs).sum(axis=1).mean() / max(spec.sum(axis=1).mean(), 1e-9))
    # axis=0 averages over frames; axis=1 would return one number per frame and a
    # vector whose length depends on how long the take happens to be.
    return [round(float(v), 4) for v in
            list(mfcc.mean(axis=0)) + list(mfcc.std(axis=0)) + [centroid]]


def fingerprint(path: Path) -> dict:
    p = Path(path).resolve()
    try:
        rel = str(p.relative_to(ROOT))
    except ValueError:                     # a probe outside the repo is still measurable
        rel = str(p)
    x = pcm(p)
    frames = _voiced(x)
    return {"file": rel, "seconds": round(len(x) / SR, 2),
            "voiced_frames": len(frames), "f0_median_hz": f0_median(frames),
            "timbre": timbre(x)}


def dist(a: list[float], b: list[float]) -> float:
    return float(np.linalg.norm(np.array(a) - np.array(b)))


def nearest(vec: list[float], pool: list[dict]) -> tuple[float, str]:
    hits = [(dist(vec, p["timbre"]), p["file"]) for p in pool if p["timbre"]]
    return min(hits) if hits else (float("inf"), "")


def remap() -> int:
    bank = [fingerprint(t) for t in sorted(VOICE_DIR.glob("seg-*.mp3"))]
    bank = [b for b in bank if b["timbre"]]
    if len(bank) < 3:
        raise SystemExit(f"need at least 3 measurable bank takes, found {len(bank)}")

    # leave-one-out: how far does the same voice get from its own bank?
    loo = []
    for i, b in enumerate(bank):
        d, who = nearest(b["timbre"], bank[:i] + bank[i + 1:])
        loo.append({"file": b["file"], "nearest_other_take": who, "distance": round(d, 3)})
    pos_max = max(x["distance"] for x in loo)

    negs = []
    for name, rel in NEGATIVES:
        p = ROOT / rel
        if not p.exists():
            continue
        f = fingerprint(p)
        if not f["timbre"]:
            continue
        d, who = nearest(f["timbre"], bank)
        f.update({"name": name, "distance_to_bank": round(d, 3), "nearest_bank_take": who})
        negs.append(f)
    if not negs:
        raise SystemExit("no negative voices available to calibrate the gate")
    neg_min = min(n["distance_to_bank"] for n in negs)

    thr = round((pos_max + neg_min) / 2, 3)
    sep = neg_min > pos_max
    f0s = [b["f0_median_hz"] for b in bank if b["f0_median_hz"]]

    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    lock["fingerprint"] = {
        "method": ("nearest-neighbour Euclidean distance on MFCC 1-12 mean+spread and "
                   "spectral centroid, over 40 ms voiced frames at 16 kHz; median F0 "
                   "is reported as a supporting cue and never gates"),
        "status": ("ADVISORY ONLY. scripts/voice_metric_study.py measured seven "
                   "feature sets on 1176 same-voice and 1568 different-voice pairs: "
                   "the best margin is 0.55x with a 5.1% equal-error rate, i.e. the "
                   "distributions overlap and no threshold decides correctly. The "
                   "numbers below describe this voice; they do not identify it. The "
                   "ear decides, against voice-film-bank.mp3."),
        "bank": bank,
        "leave_one_out": loo,
        "same_voice_max_distance": round(pos_max, 3),
        "other_voice_min_distance": round(neg_min, 3),
        "midpoint_for_reference_only": thr,
        "separates_from_known_voices": sep,
        "study": "dubs/doctor-lecture/voice-metric-study.json",
        "negatives": negs,
        "excluded_references": EXCLUDED,
        "f0_median_hz": round(float(np.median(f0s)), 1) if f0s else None,
        "f0_range_hz": [min(f0s), max(f0s)] if f0s else None,
        "f0_note": ("F0 does not identify these voices and does not settle gender: "
                    "documented masculine voice-00 spans 154-198 Hz while this bank "
                    "spans 190-211 Hz. The bank audio is the authority on what this "
                    "voice sounds like."),
    }
    LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"=== البنك المقفل: {len(bank)} عيّنات ===")
    for x in sorted(loo, key=lambda z: -z["distance"]):
        print(f"  {Path(x['file']).name}: أبعد مسافة إلى بقية البنك {x['distance']}")
    print(f"\nأقصي مسافة للصوت نفسه (leave-one-out): {pos_max:.3f}")
    print(f"أدني مسافة لصوت مختلف ({len(negs)} عيّنات voice-00): {neg_min:.3f}")
    print(f"يتداخلان؟ {'لا' if sep else 'نعم — ولا عتبة تحسم؛ الدراسة قيست 5.1٪ خطأ متساوٍ'}")
    print(f"وسيط F0: {lock['fingerprint']['f0_median_hz']} هرتز "
          f"(المدى {lock['fingerprint']['f0_range_hz']})")
    print(f"\nكُتب في {LOCK.relative_to(ROOT)} — أرقام وصفية، لا بوابة")
    return 0


def probe(path: Path) -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    fp = lock["fingerprint"]
    f = fingerprint(path)
    if not f["timbre"]:
        print(f"لا يمكن قياس {path.name}: إطارات مجهورة أقل من 20", file=sys.stderr)
        return 2
    d_bank, who = nearest(f["timbre"], fp["bank"])
    d_neg, neg = min(((dist(f["timbre"], n["timbre"]), n["name"]) for n in fp["negatives"]),
                     default=(float("inf"), ""))
    print(f"=== {path.name} ===")
    print(f"  أقرب مسافة إلى البنك المقفل : {d_bank:.3f} ← {Path(who).name}")
    print(f"  للصوت نفسه عبر البنك (المدى): حتى {fp['same_voice_max_distance']}")
    print(f"  أقرب مسافة إلى صوت مختلف    : {d_neg:.3f} ← {neg} "
          f"(أدني ما بلغه صوت مختلف: {fp['other_voice_min_distance']})")
    print(f"  وسيط F0                     : {f['f0_median_hz']} هرتز "
          f"(البنك {fp['f0_median_hz']}) — دلالة مساندة فقط")
    near = d_bank <= fp["same_voice_max_distance"]
    print(f"\nقراءة رقمية: {'أقرب من أبعد مسافة للصوت نفسه' if near else 'أبعد من مدى الصوت نفسه'}"
          f" — لكن المدى يتداخل مع الأصوات الأخرى (دراسة: هامش 0.55×، خطأ 5.1٪).")
    print("القرار للأذن: قارن بهذا الملف ثم اسمع البنك")
    print(f"  python scripts/verify_voice.py --ab {path}")
    print(f"  أو استمع إلى {VOICE_DIR.relative_to(ROOT)}/voice-film-bank.mp3")
    return 0


def ab(path: Path) -> int:
    """Build an A/B listening file: a bank take, then the probe. Copy, no DSP."""
    p = Path(path).resolve()
    if not p.exists():
        raise SystemExit(f"no such file: {p}")
    ref = VOICE_DIR / "voice-film-bank.mp3"
    if not ref.exists():
        raise SystemExit("the bank is missing -- run --remap first")
    outdir = ROOT / ".preview/ab"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"voice-ab-{p.stem}.mp3"
    listing = outdir / f".{p.stem}.txt"
    listing.write_text(f"file '{ref}'\nfile '{p}'\n", encoding="utf-8")
    r = subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", str(out)],
                       capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if r.returncode != 0:
        print(r.stderr[-800:], file=sys.stderr)
        return 1
    print(f"البنك المرجعي (دقيقتان) ثم العيّنة، في ملف واحد:\n  {out.relative_to(ROOT)}")
    print(f"يُسمع من المعاينة: /ab/{out.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remap", action="store_true", help="re-measure the bank and calibrate")
    ap.add_argument("--probe", type=Path, help="measure one recording against the bank")
    ap.add_argument("--ab", type=Path, help="build an A/B listening file: bank then probe")
    a = ap.parse_args()
    if a.remap:
        return remap()
    if a.probe:
        return probe(a.probe)
    if a.ab:
        return ab(a.ab)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
