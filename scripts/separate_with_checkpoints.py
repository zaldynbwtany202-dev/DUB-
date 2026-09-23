#!/usr/bin/env python3
"""Separate the background music, committing every finished segment.

The separation is the most expensive thing this project does: about forty-five
minutes per 280 seconds of film on the two cores this sandbox has, which is more
than five hours for the whole runtime. The first attempt wrote everything into
.cache and was one wipe away from losing all of it -- and a wipe is not
hypothetical here, it has happened twelve times.

So the run checkpoints after every segment:

  * When a segment's music appears, it is encoded to a 160k mp3 under stems/ and
    committed and pushed before the next segment starts. A wipe can cost at most
    the segment in flight, never the finished ones.
  * On startup, any segment whose wav is missing but whose mp3 exists is restored
    by decoding, so a restart resumes instead of starting over. This is why
    segment zero can be seeded from the mp3 the earlier run left behind: same
    model, same settings, already paid for.
  * The join into one bed happens at the end and is itself committed as
    stems/background.mp3.

Usage
    .venv/bin/python scripts/separate_with_checkpoints.py \\
        --source library/into-the-wild/source.local.mp4 \\
        [--slug into-the-wild] [--segment 280] [--bitrate 160k]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "arena/01a09fa1-dub"


def ffmpeg() -> str:
    import glob
    hits = glob.glob(str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    if not hits:
        raise SystemExit("ffmpeg غير موجود — شغّل scripts/rebuild_env.sh أولًا")
    return hits[0]


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def commit_and_push(paths: list[Path], message: str) -> bool:
    git("add", "-A", *[str(p) for p in paths])
    staged = git("diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        return False
    git("commit", "-q", "-m", message)
    pushed = git("push", "-q", "origin", BRANCH)
    print(f"    ✓ حُفظ و{'رُفع' if pushed.returncode == 0 else 'بقي محليًا (الرفع فشل)'}", flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--segment", type=float, default=280.0)
    ap.add_argument("--crossfade", type=float, default=0.5)
    ap.add_argument("--bitrate", default="160k")
    ap.add_argument("--work", type=Path, default=None)
    a = ap.parse_args()

    work = a.work or (ROOT / ".cache/separation" / a.slug)
    seg_dir = work / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    stems = ROOT / "work" / a.slug / "stems"
    stems.mkdir(parents=True, exist_ok=True)
    out = work / "music-full.wav"
    ff = ffmpeg()

    total = None
    err = subprocess.run([ff, "-i", str(a.source)], capture_output=True, text=True).stderr
    for line in err.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration: ")[1].split(",")[0].split(":")
            total = int(h) * 3600 + int(m) * 60 + float(s)
            break
    if total is None:
        raise SystemExit("لا يمكن قراءة مدة المصدر")
    n = int(total // a.segment) + (1 if total % a.segment else 0)
    print(f"  المصدر {total:.1f}ث · {n} مقطعًا × {a.segment:.0f}ث")

    # ---- restore what a previous life already computed ------------------------
    for i in range(n):
        music = seg_dir / f"seg-{i:02d}-music.wav"
        saved = stems / f"music-{i:02d}.mp3"
        if not music.is_file() and saved.is_file():
            print(f"  [مقطع {i}] استرجاع من {saved.name}", flush=True)
            subprocess.run([ff, "-y", "-v", "error", "-i", str(saved), "-c:a", "pcm_s16le",
                            str(music)], check=True)

    print("  --- الفصل يعمل؛ كل مقطع يُحفظ عند اكتماله ---", flush=True)
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/separate_background_segmented.py"),
         "--source", str(a.source), "--out", str(out), "--segment", str(a.segment),
         "--crossfade", str(a.crossfade), "--work", str(work)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    saved: set[int] = set()
    last = time.monotonic()
    while proc.poll() is None:
        line = proc.stdout.readline()
        if line:
            print("   " + line.rstrip(), flush=True)
        for f in sorted(seg_dir.glob("seg-*-music.wav")):
            i = int(f.name.split("-")[1])
            if i in saved:
                continue
            mp3 = stems / f"music-{i:02d}.mp3"
            if not mp3.exists():
                subprocess.run([ff, "-y", "-v", "error", "-i", str(f), "-ar", "44100", "-ac", "2",
                                "-c:a", "libmp3lame", "-b:a", a.bitrate, str(mp3)], check=True)
            saved.add(i)
            print(f"  [مقطع {i}] اكتمل · {mp3.stat().st_size/1e6:.1f} م.بايت", flush=True)
            commit_and_push([mp3], f"Keep the separated music for segment {i}\n\n"
                                   f"Segment {i} of {n}, checked in the moment it finished so a wipe "
                                   f"cannot take it. The wav stays local; the mp3 is the copy that "
                                   f"survives, at {a.bitrate}.")
        if time.monotonic() - last > 600:
            print(f"  ... ما زال يعمل ({len(saved)}/{n} محفوظة)", flush=True)
            last = time.monotonic()
        if not line:
            time.sleep(2)

    remaining = proc.wait()
    if remaining != 0:
        print(f"  فشل الفصل (رمز {remaining})", flush=True)
        return remaining

    # Anything finished after the child's last line still needs saving.
    for f in sorted(seg_dir.glob("seg-*-music.wav")):
        i = int(f.name.split("-")[1])
        if i in saved:
            continue
        mp3 = stems / f"music-{i:02d}.mp3"
        subprocess.run([ff, "-y", "-v", "error", "-i", str(f), "-ar", "44100", "-ac", "2",
                        "-c:a", "libmp3lame", "-b:a", a.bitrate, str(mp3)], check=True)
        saved.add(i)
        commit_and_push([mp3], f"Keep the separated music for segment {i}")

    # ---- the joined bed, committed as one file -------------------------------
    bed = stems / "background.mp3"
    if out.is_file() and not bed.exists():
        print("  وسم الخريطة الكاملة background.mp3", flush=True)
        subprocess.run([ff, "-y", "-v", "error", "-i", str(out), "-ar", "44100", "-ac", "2",
                        "-c:a", "libmp3lame", "-b:a", a.bitrate, str(bed)], check=True)

    report = {
        "source": str(a.source), "segments": n, "segment_s": a.segment, "bitrate": a.bitrate,
        "bed_mp3": str(bed.relative_to(ROOT)), "bed_mp3_bytes": bed.stat().st_size if bed.exists() else 0,
        "segments_saved": sorted(saved), "wav_local": str(out),
        "wav_duration_s": None,
    }
    if out.is_file():
        err = subprocess.run([ff, "-i", str(out)], capture_output=True, text=True).stderr
        for line in err.splitlines():
            if "Duration:" in line:
                h, m, s = line.split("Duration: ")[1].split(",")[0].split(":")
                report["wav_duration_s"] = round(int(h) * 3600 + int(m) * 60 + float(s), 2)
                break
    (stems / "separation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if bed.exists():
        commit_and_push([stems / "separation-report.json", bed],
                        "Join the separated music into one bed for the film")
    print(f"  انتهى الفصل · الخريطة {report['bed_mp3_bytes']/1e6:.1f} م.بايت", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
