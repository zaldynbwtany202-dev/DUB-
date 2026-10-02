#!/usr/bin/env python
"""Dub a film with the locked voice -- the recipe that was approved, unchanged.

The voice, and the way it is treated, are frozen in config/voice-lock.json. This
script reads that file and does exactly what it says, nothing else. The point is
that a new film gets the same voice the user approved without anyone re-deciding
anything: no timbre conversion, no silence cutting, atempo for timing, plain
mastering, video copied.

    # what a new film needs first: work/<slug>/groups.json  (+ the source film)
    python scripts/dub_locked_voice.py --slug my-film --takes work/my-film/takes \
        --start 0 --end 20 --out previews/my-film-dub.mp4 --music work/my-film/stems/background-80k.mp3

    python scripts/dub_locked_voice.py --slug my-film --takes ... --check   # recipe only

The build is gated, in this order and with no exceptions for a delivered file:

  1. scripts/audit_dub.py  -- takes vs config/dubbing-qa.json. A hard violation
     (a take missing, a tempo above the locked ceiling, lateness, a silence gap,
     a pitch jump past the hard band, level spread) stops the build here.
  2. scripts/assemble_dub.py --placement ...  -- the run is written with a record
     of where every group actually landed.
  3. scripts/verify_dub.py  -- the finished file is measured: lateness, gaps,
     duration, loudness, true peak, and that frame 0 is the source frame at the
     same second (PSNR infinite). A failure stops delivery.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOCK = Path("config/voice-lock.json")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--takes", required=True, help="directory of takes, one per group")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--out", type=Path, default=None, help="mp4 to write; omit for audio only")
    ap.add_argument("--source", type=Path, default=None,
                    help="source film; defaults to library/<slug>/source.local.mp4")
    ap.add_argument("--music", type=Path, default=None, help="music bed; omit for voice alone")
    ap.add_argument("--video-start", type=float, default=None,
                    help="seconds into the film where this run of groups begins; "
                         "defaults to the first group's own t0, so a middle run is "
                         "cut from the right place instead of always from 0:00")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--check", action="store_true", help="print the recipe and stop")
    ap.add_argument("--no-gate", action="store_true",
                    help="تجاوز بوابة الجودة (للتشخيص فقط — لا يُسلَّم عمل خرج من هنا)")
    ap.add_argument("--word-align", type=Path, default=None,
                    help="ملف توقيتات الأصل: يُقسَّم كل مقطع من الأخذة عند صمتاته وتُحلّ "
                         "سرعاته معًا ليلامس مواضع كلمات الأصل داخل نافذته بالضبط "
                         "(انظر scripts/align_by_words.py)")
    ap.add_argument("--word-align-alt", type=Path, default=None,
                    help="ملف توقيت ثانٍ؛ لكل مجموعة يُختار ما يعطي خطأ ربط أقل (اختيار مقيس)")
    ap.add_argument("--qa", type=Path, default=Path("config/dubbing-qa.json"))
    a = ap.parse_args()

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    work = Path("work") / a.slug
    build = work / "build"
    build.mkdir(parents=True, exist_ok=True)

    offset = a.video_start
    if offset is None and (work / "groups.json").exists():
        grp = json.loads((work / "groups.json").read_text(encoding="utf-8"))["groups"]
        offset = float(grp[a.start]["t0"]) if a.start < len(grp) else 0.0
    offset = max(0.0, float(offset or 0.0))

    print(f"  الصوت المعتمد: {lock['voice_id']} · {lock['name']}")
    for step, cfg in (("التجميع", lock["assembly"]), ("الماستر", lock["mastering"])):
        print(f"  {step}: " + " · ".join(f"{k}={v}" for k, v in cfg.items()))
    if a.check:
        return 0

    if not (work / "groups.json").exists():
        raise SystemExit(f"  ✗ لا توجد خطة: {work / 'groups.json'} — خطّط الفيلم أولًا")

    if not a.no_gate:
        print("\n  البوابة: تدقيق الأخذات قبل البناء")
        aud_cmd = [sys.executable, str(HERE / "audit_dub.py"), "--slug", a.slug,
                   "--takes", str(a.takes), "--start", str(a.start),
                   "--end", str(a.end), "--gate",
                   "--json", str(work / "qa" / f"audit-{a.start}-{a.end - 1}.json")]
        if a.word_align:
            aud_cmd.append("--elastic")          # التدقيق يحاكي المُجمِّع المستعمل فعلًا
        aud = subprocess.run(aud_cmd)
        if aud.returncode == 2:
            raise SystemExit("  ✗ البوابة أوقفت البناء: شرط صارم مرفوض (انظر التدقيق أعلاه). "
                             "ولّد مرشحين للمجموعات المرشّحة ثم أعد المحاولة.")
        if aud.returncode != 0:
            print("  ! التدقيق يرى مرشحين أفضل — البناء يمضي، والحكم في التقرير.", file=sys.stderr)

    voice = build / f"{a.slug}-voice.wav"
    asm = lock["assembly"]
    placement = build / f"{a.slug}-placement-{a.start}-{a.end - 1}.json"
    if a.word_align:
        cmd = [sys.executable, str(HERE / "align_by_words.py"), str(work),
               "--takes", str(a.takes), "--timed", str(a.word_align),
               "--out", str(voice), "--placement", str(placement),
               "--start", str(a.start), "--end", str(a.end),
               "--max-tempo", str(asm["max_tempo"]),
               "--min-tempo", str(min(1.0, float(asm.get("min_tempo", 1.0))))]
        if a.word_align_alt:
            cmd += ["--timed-alt", str(a.word_align_alt)]
        print(f"\n  تجميع بملاءمة مرنة لكلمات الأصل (بلا قصّ كلام · قطع عند الصمت فقط)")
    else:
        cmd = [sys.executable, str(HERE / "assemble_dub.py"), str(work),
               "--out", str(voice), "--start", str(a.start), "--end", str(a.end),
               "--stretch", asm["stretcher"], "--takes", str(a.takes),
               "--max-tempo", str(asm["max_tempo"])]
        if not asm["tighten"]:
            cmd.append("--no-tighten")      # cutting silence measurably changes the voice
        if asm["pack"]:
            cmd.append("--pack")
        if float(asm.get("min_tempo", 1.0)) < 1.0:
            cmd += ["--min-tempo", str(asm["min_tempo"])]
        cmd += ["--placement", str(placement)]
        print(f"\n  تجميع ({asm['stretcher']} · بلا قصّ صمت · بلا تحويل)")
    run(cmd)

    mst = lock["mastering"]
    mastered = build / f"{a.slug}-master.wav"
    cmd = [sys.executable, str(HERE / "master_dub.py"), "--voice", str(voice),
           "--out", str(mastered), "--lufs", str(mst["lufs"]),
           "--tp", str(mst["true_peak_dbtp"])]
    if mst["plain_voice"]:
        cmd.append("--plain-voice")         # the chain is for human recordings only
    if a.music:
        secs = _duration(voice)
        cmd += ["--music", str(a.music), "--music-start", f"{offset:.3f}",
                "--music-dur", f"{secs:.3f}",
                "--duck-db", str(mst["duck_db"]), "--music-db", str(mst["music_db"])]
    if a.report:
        cmd += ["--report", str(a.report)]
    print("  المعالجة (plain — بلا EQ ولا ضغط)")
    run(cmd)

    if a.out:
        src = a.source or Path("library") / a.slug / "source.local.mp4"
        if not src.exists():
            raise SystemExit(f"  ✗ لا يوجد فيلم مصدر: {src}")
        # assemble_dub writes a film-aligned track: a run that starts at 2:16 has
        # 2:16 of silence in front of it. So the audio is trimmed to the run while
        # the picture is seeked to the same second -- never the other way round,
        # or the voice would sit a whole run-length away from the picture.
        secs = _duration(mastered) - offset if offset > 0.05 else _duration(voice)
        audio = mastered
        if offset > 0.05:
            audio = build / f"{a.slug}-master-seg.wav"
            run(_ffmpeg() + ["-y", "-v", "error", "-ss", f"{offset:.3f}",
                             "-i", str(mastered), "-c:a", "pcm_s16le", str(audio)])
        print(f"  اللصق بالصورة (-c:v copy · aac 192k · {secs:.2f} ثانية · "
              f"من الثانية {offset:.2f} في الفيلم)")
        run(_ffmpeg() + ["-y", "-v", "error", "-ss", f"{offset:.3f}", "-i", str(src),
                         "-i", str(audio),
                         "-t", f"{secs:.3f}", "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "copy", "-c:a", "aac",
                         "-b:a", lock["mux"]["audio_bitrate"], str(a.out)])
        print(f"  ✓ {a.out}")

        if not a.no_gate:
            print("\n  البوابة: التحقّق من الملف الناتج")
            ver = subprocess.run([sys.executable, str(HERE / "verify_dub.py"), "--slug", a.slug,
                                  "--out", str(a.out), "--placement", str(placement),
                                  "--master", str(audio), "--source", str(src),
                                  "--offset", f"{offset:.3f}",
                                  "--json", str(work / "qa" / f"verify-{a.start}-{a.end - 1}.json")]
                                 + (["--report", str(a.report)] if a.report else []))
            if ver.returncode != 0:
                raise SystemExit("  ✗ الملف لم يجتز التحقّق — لا يُسلَّم. انظر التفاصيل أعلاه.")
    else:
        print(f"  ✓ {mastered}")
    return 0


def _duration(path: Path) -> float:
    import soundfile as sf
    return float(sf.info(str(path)).duration)


def _ffmpeg() -> list[str]:
    import imageio_ffmpeg
    return [imageio_ffmpeg.get_ffmpeg_exe()]


if __name__ == "__main__":
    raise SystemExit(main())
