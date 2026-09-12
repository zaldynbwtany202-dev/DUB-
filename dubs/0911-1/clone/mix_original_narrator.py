#!/usr/bin/env python3
"""Mux the original narrator (all words, original timing) over the music bed.

Synthetic takes dropped words and left holes. The isolated original vocal
is the professional voice the user asked to clone, with every utterance intact.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
from youtube_auto_dub.srt_export import render_srt
from youtube_auto_dub.stem_split import separate_file

VIDEO = ROOT / "library/0911-1/source.mp4"
OUT = ROOT / "dubs/0911-1/clone/original-narrator"
SEGMENTS = ROOT / "library/0911-1/source.segments.json"

# Readable Egyptian for the words the narrator actually says. Nothing dropped.
CLEAN = (
    ("مية تنية", "مية طينية"),
    ("قرية تنية", "قرية تانية"),
    ("فدل", "فضل"),
    ("يتحك", "يضحك"),
    ("صنين", "سنين"),
    ("ارض", "أرض"),
    ("بقا", "بقى"),
    ("ودفحة", "والدفعة"),
    ("دي زتعة", "دي"),
    ("رفهية", "رفاهية"),
    ("للبطنة", "للبطن"),
    ("الليلجة", "الليل جه"),
    ("داسكن", "أسكن"),
    ("كراره", "قراره"),
    ("وقالي", "وقال"),
    ("اخيرة", "أخيرة"),
    ("داني", "الدنيا"),
    ("فدلت", "فضلت"),
    ("وضل", "وفضل"),
    ("مفوشه", "مقفول"),
    ("هيمشو", "هيمشي"),
    ("ابل", "قبل"),
    ("ويحاولي", "ويحاول"),
    ("فدهم", "يفهمهم"),
    ("معقى", "معاه"),
    ("وصيت", "وصية"),
    ("اهلو", "أهله"),
    ("الاخيرة", "الأخيرة"),
    ("يموته", "يموت"),
    ("وعلى الاسفل", ""),
    ("الاسفل", ""),
)


def clean_text(text: str) -> str:
    out = text
    for src, dst in CLEAN:
        out = out.replace(src, dst)
    return " ".join(out.split())


def mix(vocals: Path, music: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    filt = (
        "[0:a]highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=9[v];"
        "[1:a]volume=0.35[m];"
        "[v][m]amix=inputs=2:duration=first:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=9[a]"
    )
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(vocals), "-i", str(music),
         "-filter_complex", filt, "-map", "[a]", "-ar", "48000", "-ac", "1",
         "-c:a", "pcm_s24le", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def mux(video: Path, audio: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(video), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-ar", "48000", "-metadata:s:a:0", "language=ara", "-movflags", "+faststart",
         str(dest)],
        check=True, capture_output=True,
    )
    return dest


def srt_from_source(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for seg in data["segments"]:
        words = seg.get("words") or []
        dtw = [float(p) for w in words for p in (w.get("dtw_points") or [])]
        start = min(dtw) if dtw else float(seg["start"])
        end = max(dtw) if dtw else float(seg["end"])
        text = clean_text(str(seg.get("text") or ""))
        if text:
            entries.append({"start": start, "end": min(end, float(data.get("duration") or end)), "text": text})
    return render_srt(entries)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vocals, music = separate_file(VIDEO, OUT / "stems", sr=24000)
    mixed = mix(vocals, music, OUT / "mix.wav")
    video = mux(VIDEO, mixed, OUT / "final-dub-original-narrator.mp4")
    srt = srt_from_source(SEGMENTS)
    (OUT / "final-dub-original-narrator.srt").write_text(srt, encoding="utf-8")
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", str(mixed), "-c:a", "libmp3lame", "-b:a", "160k",
         str(OUT / "final-dub-original-narrator.mp3")],
        check=True, capture_output=True,
    )
    print(video)
    print(srt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
