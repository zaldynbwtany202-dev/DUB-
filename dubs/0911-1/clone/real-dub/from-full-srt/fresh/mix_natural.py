#!/usr/bin/env python3
"""Natural-speed dubbed speech. Stretch picture. Never mix original audio."""
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
from youtube_auto_dub.island_sync import speech_islands  # noqa: E402
from youtube_auto_dub.stem_split import decode_stereo, split_center  # noqa: E402

HERE = Path(__file__).resolve().parent
VIDEO = ROOT / "library/0911-1/source.mp4"
OUT = HERE / "final-dub-fresh.mp4"
SR = 24000
PAUSE = 0.55

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
    sf.write(HERE / "aligned.wav", cat, SR, subtype="PCM_24")
    sf.write(wav, cat, SR, subtype="PCM_24")

    # Original is 32.25s; stretch picture to the natural dubbed length.
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
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(HERE / "final-dub-fresh.mp3")],
        check=True,
        capture_output=True,
    )

    out_a = decode(OUT)
    src = decode(VIDEO)
    left, right = decode_stereo(VIDEO, SR)
    voice, _ = split_center(left, right, SR, strength=1.8)
    c = corr(out_a, np.pad(voice, (0, max(0, len(out_a) - len(voice))))[: len(out_a)])
    isl = speech_islands(out_a, SR, floor_db=-34.0, min_dur=0.20, merge_gap=0.35)
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(OUT)], capture_output=True, text=True).stderr
    audio_streams = streams.count("Audio:")
    print("duration", round(dur, 3), "corr", round(c, 4), "islands", len(isl), "audio_streams", audio_streams)
    print("spans", [(round(a, 2), round(b, 2)) for a, b in spans])
    print("detected", [(round(a / SR, 2), round(b / SR, 2)) for a, b in isl])
    if audio_streams != 1:
        raise RuntimeError(f"expected 1 audio stream, got {audio_streams}")
    if abs(c) > 0.05:
        raise RuntimeError(f"original still in mix corr={c:.3f}")
    if len(isl) > 12:
        raise RuntimeError(f"too chopped: {len(isl)} islands")

    srt = "\n\n".join(
        f"{i}\n{stamp(a)} --> {stamp(b)}\n{TEXTS[i-1]}" for i, (a, b) in enumerate(spans, 1)
    ) + "\n"
    (HERE / "final-dub-fresh.srt").write_text(srt, encoding="utf-8")
    meta = {
        "voice_id": "voice-27",
        "method": "natural_speed_stretch_picture_dub_only",
        "duration": round(dur, 3),
        "tempo": 1.0,
        "corr_original_vocals": round(c, 4),
        "islands_detected": len(isl),
        "audio_streams": audio_streams,
        "original_voice_in_mix": False,
        "word_chopping": False,
        "merge": "forbidden",
        "output": str(OUT.relative_to(ROOT)),
    }
    (HERE / "final-dub-fresh.agent.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS", OUT, OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
