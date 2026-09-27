#!/usr/bin/env python
"""Numbered samples of the *human* professional voices that already live in the library.

The user asked for human professional voice samples. Nothing can be downloaded
(every outside host answers 000), so the samples come from what he already gave
us: the narrator of Into the Wild, the narrator of the doctor/lecture recap,
the 0911-1 recap, the Arabic cartoon dub of Sindbad, the hajj story, the Arabic
revoice, the studio demo and the short test clip.

No processing beyond loudness: no EQ, no compressor, no pitch move, no timbre
conversion -- the same rule the synthetic voice lives under. Each sample is
preceded by N short tones, N being its number on the page, so the ear can tell
which one is which without a screen.

  python3 scripts/build_human_voices.py --scan     # rank windows, print them
  python3 scripts/build_human_voices.py --build    # write previews/human-voices.mp3
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
sys.path.insert(0, str(HERE))
import imageio_ffmpeg  # noqa: E402
from voice_convert import median_f0  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
WORK = Path("/tmp/hv")
AUD = Path("work/into-the-wild/auditions")
OUT = Path("previews/human-voices.mp3")
LABELS = AUD / "human-voices.txt"
PEAK = 0.72          # every sample is scaled to this peak, and nothing else
TONES = 660.0        # the counting tone that announces the sample number

# name -> (arabic label, source, note)
SOURCES = {
    "narrator-film": ("راوي فيلم «Into the Wild» نفسه", "work/into-the-wild/stems/vocal-%02d.mp3"),
    "doctor":        ("راوي ملخص «الدكتور بيقتل طالبه»", "doctor.wav"),
    "0911":          ("راوي ملخص «Into the Wild» (0911)", "0911.wav"),
    "sindbad":       ("دبلجة كرتون السندباد العربية", "sindbad.wav"),
    "hajj":          ("راوي قصة «حلم الحج»", "hajj.wav"),
    "snaptik":       ("راوي «المرأة التي تدرك أنها ميتة»", "snaptik.wav"),
    "prostudio":     ("عرض ProStudio العربي", "prostudio.wav"),
    "shorts":        ("مقطع الاختبار القصير", "shorts.wav"),
}
LENGTH = 9.0     # seconds of speech per sample


def rms_env(x: np.ndarray, hop: int = 256) -> np.ndarray:
    n = len(x) // hop
    e = x[: n * hop].reshape(n, hop)
    return np.sqrt((e ** 2).mean(axis=1) + 1e-12)


def speech_score(env: np.ndarray) -> np.ndarray:
    """Syllabic (2-8 Hz) modulation of the envelope, minus the slow drift."""
    if env.size < 64:
        return np.zeros_like(env)
    e = np.log(env + 1e-6)
    e = e - e.mean()
    w = np.hanning(len(e))
    spec = np.abs(np.fft.rfft(e * w))
    freqs = np.fft.rfftfreq(len(e), d=1.0 / (SR / 256.0))
    fast = spec[(freqs >= 2.0) & (freqs <= 8.0)].sum()
    slow = spec[(freqs < 1.0)].sum() + 1e-9
    return np.full(env.shape, float(fast / slow))


def load(path: str | Path) -> tuple[np.ndarray, int]:
    x = sf.read(str(path), dtype="float32", always_2d=False)[0]
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, SR


def candidates(x: np.ndarray, length: float = LENGTH, step: float = 0.5) -> list[tuple]:
    """Non-overlapping windows ranked by how much they sound like steady speech."""
    hop = 256
    env = rms_env(x)
    win = int(length)
    # These films are mastered loud: the music bed never drops, so a fixed
    # multiple of the quiet percentile finds nothing. The line is drawn inside
    # the loud range itself, and what is "speech" is the part above it.
    p10, p90 = np.percentile(env, 10), np.percentile(env, 90)
    floor = p10 + 0.35 * (p90 - p10)
    out = []
    for a in np.arange(0.0, max(0.0, len(x) / SR - length - 0.1), step):
        seg = env[int(a * SR / hop): int((a + length) * SR / hop)]
        if seg.size < 8:
            continue
        live = float((seg > floor).mean())            # how much of it is speech
        mid = float(np.median(seg))
        if mid < 0.01 or live < 0.35:
            continue
        sc = float(speech_score(seg).mean()) * live * np.sqrt(mid)
        out.append((round(sc, 4), round(float(a), 2), round(live, 2), round(mid, 4)))
    out.sort(reverse=True)
    picked = []
    for c in out:                                     # keep them apart
        if all(abs(c[1] - p[1]) >= length for p in picked):
            picked.append(c)
        if len(picked) >= 12:
            break
    return picked


def cut(x: np.ndarray, start: float, length: float) -> np.ndarray:
    a, b = int(start * SR), int((start + length) * SR)
    seg = x[a:b]
    if seg.size < int(length * SR):
        return np.pad(seg, (0, int(length * SR) - seg.size))
    fade = int(0.012 * SR)
    seg = seg.copy()
    seg[:fade] *= np.linspace(0, 1, fade)
    seg[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.abs(seg).max()) + 1e-9
    return seg * (PEAK / peak)


def tones(n: int, on: float = 0.16, off: float = 0.14) -> np.ndarray:
    out = []
    k = np.arange(int(on * SR)) / SR
    beep = 0.34 * np.sin(2 * np.pi * TONES * k) * np.hanning(int(on * SR))
    for i in range(n):
        out.append(beep)
        out.append(np.zeros(int((off if i < n - 1 else 0.45) * SR), dtype="float32"))
    return np.concatenate(out).astype("float32")


def narrator_window(index: int = 0, start: float = 25.0) -> tuple[np.ndarray, int]:
    p = WORK / "narrator-film.wav"
    if not p.exists():
        subprocess.run([FF, "-v", "error", "-y", "-i",
                        str(Path("work/into-the-wild/stems") / f"vocal-{index:02d}.mp3"),
                        "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(p)], check=True)
    return load(p)


def resolve(key: str) -> tuple[np.ndarray, int]:
    if key == "narrator-film":
        return narrator_window()
    p = WORK / SOURCES[key][1]
    return load(p)


def scan() -> None:
    for key in SOURCES:
        x, _ = resolve(key)
        print(f"\n### {key}  ({len(x) / SR:.1f} s)")
        for sc, a, live, mid in candidates(x)[:6]:
            print(f"   حياة={live:<5} وسيط={mid:<8} نتيجة={sc:<8} عند {a:8.2f} ث")


def build(picks: dict[str, float]) -> None:
    parts, report = [], []
    for n, key in enumerate(SOURCES, start=1):
        if key not in picks:
            continue
        x, _ = resolve(key)
        seg = cut(x, picks[key], LENGTH)
        f0 = median_f0(seg, SR)
        label, src = SOURCES[key]
        report.append((n, key, label, picks[key], f0))
        print(f"  {n:02d} {key:<14} من {picks[key]:7.2f} ث · {f0:6.1f} هرتز · {label}")
        parts.append(tones(n))
        parts.append(seg)
        parts.append(np.zeros(int(0.85 * SR), dtype="float32"))
    out = np.concatenate(parts)
    tmp = Path("/tmp/human-voices.wav")
    sf.write(str(tmp), out, SR, subtype="PCM_16")
    subprocess.run([FF, "-v", "error", "-y", "-i", str(tmp),
                    "-c:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)
    print(f"\n{OUT}  {len(out) / SR:.1f} s  {OUT.stat().st_size / 1e6:.1f} MB")

    lines = ["عيّنات أصوات بشرية احترافية — الرقم يُعلَن بعدد النغمات القصيرة قبله",
             "=" * 72]
    for n, key, label, at, f0 in report:
        lines.append(f"{n:02d}  {label}")
        lines.append(f"     من الثانية {at:.2f} · وسيط النبرة {f0:.0f} هرتز · "
                     f"المصدر {SOURCES[key][1]}")
        lines.append("")
    lines.append("لا معالجة إطلاقًا غير ضبط المستوى: لا EQ، لا ضغط، لا تسريع، "
                 "لا تغيير نبرة ولا طابع.")
    LABELS.write_text("\n".join(lines), encoding="utf-8")
    print(f"{LABELS} written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--pick", default="")   # key=start,key=start
    a = ap.parse_args()
    if a.scan:
        scan()
    elif a.build:
        picks = {k: float(v) for k, v in
                 (kv.split("=") for kv in a.pick.split(",") if kv)}
        build(picks)
    else:
        ap.print_help()
