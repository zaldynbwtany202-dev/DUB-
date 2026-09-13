#!/usr/bin/env python3
"""voice-33 mux only. No DSP."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "FFMPEG_BINARY",
    str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"),
)

from youtube_auto_dub.agent_tts import trim_silence  # noqa: E402
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402
from youtube_auto_dub.stem_split import decode_stereo, split_center  # noqa: E402

HERE = Path(__file__).resolve().parent
VIDEO = ROOT / "library/0911-1/source.mp4"
OUT = HERE / "final-dub-v33.mp4"
SR = 24000
PAUSE = 0.50

TEXTS = [
    "الارض بتاعته فضل يضحك ويبتسم لانه بعد ما قضى سنين طويله وهو بيشرب ميه طينيه في ارض المعركه اخيرا بقى قدامه ميه نظيفه بيقدر ان هو يشربها",
    "ودي في حد ذاتها تعتبر رفاهيه بالنسبه لبطلنا وعلى ما الليل جا داس كان اخد قراره وقال لنفسه ان هو هيعمل كل اللي يقدر عليه",
    "ويدي المكان ده فرصه اخيره لكن لو الدنيا فضلت مقفله وضلمه في وشه بعد يومين هيمشي وهيسيب المكان قبل ما يموت هنا",
    "ولو وصلت لكده هيدور على اي قريه ثانيه ويحاول يفيدهم باي طريقه علشان يفضل دايما محقق وصيه اهله الاخيره قبل ما يموتوا وعلى",
]


def decode(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(raw.stdout, dtype=np.float32)
    if not len(audio):
        raise RuntimeError(f"empty {path}")
    return audio


def stamp(seconds: float) -> str:
    total, ms = divmod(round(seconds * 1000), 1000)
    minutes, sec = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def lagged_corr(a: np.ndarray, b: np.ndarray, sr: int, max_lag: float = 0.6) -> tuple[float, float]:
    hop = max(1, int(sr * 0.01))
    max_s = int(max_lag * sr)
    best, best_lag = 0.0, 0
    a0 = a - a.mean()
    b0 = b - b.mean()
    a0 = a0 / (np.linalg.norm(a0) + 1e-12)
    b0 = b0 / (np.linalg.norm(b0) + 1e-12)
    for lag in range(-max_s, max_s + 1, hop):
        if lag < 0:
            aa, bb = a0[-lag:], b0[: len(a0) + lag]
        elif lag > 0:
            aa, bb = a0[: len(a0) - lag], b0[lag:]
        else:
            aa, bb = a0, b0
        n = min(len(aa), len(bb))
        if n < sr:
            continue
        c = float(np.dot(aa[:n], bb[:n]))
        if abs(c) > abs(best):
            best, best_lag = c, lag
    return best, best_lag / sr


def main() -> int:
    gap = np.zeros(int(SR * PAUSE), dtype=np.float32)
    parts: list[np.ndarray] = []
    spans: list[tuple[float, float]] = []
    t = 0.0
    for i in range(4):
        take, _ = trim_silence(decode(HERE / f"seg-{i:04d}.mp3"), SR)
        if i:
            parts.append(gap)
            t += PAUSE
        start = t
        parts.append(take.astype(np.float32))
        t += len(take) / SR
        spans.append((start, t))
    cat = np.concatenate(parts)
    peak = float(np.max(np.abs(cat)) or 1.0)
    if peak > 0.95:
        cat = cat * (0.95 / peak)
    dur = len(cat) / SR
    wav = HERE / "mixed.wav"
    sf.write(wav, cat, SR, subtype="PCM_24")

    factor = dur / 32.252
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-v", "error",
            "-i", str(VIDEO), "-i", str(wav),
            "-filter_complex", f"[0:v]setpts={factor:.6f}*PTS,fps=30[v]",
            "-map", "[v]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-metadata:s:a:0", "language=ara",
            "-t", f"{dur:.6f}", "-movflags", "+faststart",
            str(OUT),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(HERE / "final-dub-v33.mp3")],
        check=True,
        capture_output=True,
    )

    out_a = decode(OUT)
    left, right = decode_stereo(VIDEO, SR)
    voice, _ = split_center(left, right, SR, strength=1.8)
    voice_p = np.pad(voice, (0, max(0, len(out_a) - len(voice))))[: len(out_a)]
    c = corr(out_a, voice_p)
    lc, lag = lagged_corr(out_a[: int(32.25 * SR)], voice[: int(32.25 * SR)], SR)
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(OUT)], capture_output=True, text=True).stderr
    n_audio = streams.count("Audio:")
    print("dur", round(dur, 3), "corr", round(c, 4), "lagged", round(lc, 4), "lag", round(lag, 3), "streams", n_audio)
    if n_audio != 1:
        raise RuntimeError(f"audio streams {n_audio}")
    if abs(c) > 0.06 or abs(lc) > 0.08:
        raise RuntimeError(f"original still present corr={c:.3f} lagged={lc:.3f}")

    srt = "\n\n".join(
        f"{i}\n{stamp(a)} --> {stamp(b)}\n{TEXTS[i-1]}" for i, (a, b) in enumerate(spans, 1)
    ) + "\n"
    (HERE / "final-dub-v33.srt").write_text(srt, encoding="utf-8")
    meta = {
        "voice_id": "voice-33",
        "processing": "none",
        "tempo": 1.0,
        "duration": round(dur, 3),
        "corr_original_vocals": round(c, 4),
        "lagged_corr": round(lc, 4),
        "audio_streams": n_audio,
        "original_voice_in_mix": False,
        "merge": "forbidden",
        "output": str(OUT.relative_to(ROOT)),
    }
    (HERE / "final-dub-v33.agent.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS", OUT, OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
