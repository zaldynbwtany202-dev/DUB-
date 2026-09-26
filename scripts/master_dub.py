#!/usr/bin/env python3
"""Master the dub: a broadcast-quality voice over music that yields to it.

The dub was assembled correctly and mixed plainly -- voice at a fixed gain over
music at a fixed gain, with a limiter at the end. That is a mix, but it is not a
master, and the difference is audible on the two things that make a narrated film
sound professional:

  * The voice is even. Take-to-take level varies, sibilance rides up where the
    time-stretch sharpened it, and the low mids carry the room of whatever room
    the synthesis imagined. High-pass, a cut at 250 Hz, a presence lift at 3 kHz,
    a de-esser and gentle compression make forty separately generated takes sound
    like one narrator in one booth.

  * The music ducks. In the current mix the bed holds its level while the narrator
    talks over it, so the words fight the strings. A sidechain compressor driven
    by the voice pulls the bed down a few dB only while there are words, which is
    what every broadcast mix does and what makes speech sit in front.

Loudness is measured rather than guessed: a first pass reads the programme's
integrated loudness, and the second pass normalises to the target with a true-peak
ceiling. One-pass normalisation guesses, and on a film with quiet stretches it
guesses wrong -- it rides the gain on the loud parts and leaves the quiet parts
quiet.

Usage
    .venv/bin/python scripts/master_dub.py --voice voice.wav --music music.wav \\
        --out mix.wav [--lufs -16] [--duck-db 9] [--music-db -4] [--report out.json]

With --voice only, the voice is mastered on its own with no music, which is what
the assembly step wants when the bed is added later.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A narrator's spectrum, shaped for intelligibility on small speakers: rumble out,
# boxiness down, presence up. Values are conservative on purpose -- a dub that
# sounds equalised is a worse dub.
VOICE_CHAIN = (
    "highpass=f=85,"
    "equalizer=f=250:width_type=q:w=1:g=-2.5,"
    "equalizer=f=3200:width_type=q:w=1.2:g=+2.5,"
    "deesser=i=0.4:m=0.5:f=0.5,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=150:makeup=2"
)


def ffmpeg() -> str:
    import glob
    hits = glob.glob(str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    if not hits:
        raise SystemExit("ffmpeg غير موجود — شغّل scripts/rebuild_env.sh")
    return hits[0]


FF = ffmpeg()


def run(args: list[str]) -> subprocess.CompletedProcess:
    """Always captured, always text: the loudness report arrives on stderr and is
    read as a string, and a filter error has to reach the caller's traceback."""
    return subprocess.run([FF, "-hide_banner", "-nostats"] + args,
                          capture_output=True, text=True)


def measure(path: Path, target_i: float, tp: float, lra: float) -> dict:
    """First loudness pass: what is this programme actually doing?"""
    p = run(["-i", str(path), "-af",
             f"loudnorm=I={target_i}:TP={tp}:LRA={lra}:print_format=json",
             "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def apply_loudnorm_second_pass(path: Path, out: Path, target_i: float, tp: float,
                               lra: float, measured: dict, extra: str = "") -> None:
    """Second pass with the measured values, so the gain is set by data."""
    pre = extra + "," if extra else ""
    if not measured:
        # No measurement means one pass, which is honest: it is what the filter
        # can do alone. Better a mix that is a dB or two off than a failed run.
        chain = f"{pre}loudnorm=I={target_i}:TP={tp}:LRA={lra}"
    else:
        chain = (f"{pre}loudnorm=I={target_i}:TP={tp}:LRA={lra}"
                 f":measured_I={measured.get('input_i', target_i)}"
                 f":measured_TP={measured.get('input_tp', tp)}"
                 f":measured_LRA={measured.get('input_lra', lra)}"
                 f":measured_thresh={measured.get('input_thresh', -70)}"
                 f":offset={measured.get('target_offset', 0)}"
                 f":linear=true:print_format=summary")
    run(["-i", str(path), "-af", chain, "-ar", "48000", "-ac", "2",
         "-c:a", "pcm_s16le", "-y", str(out)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--voice", type=Path, required=True, help="assembled voice track")
    ap.add_argument("--plain-voice", action="store_true",
                    help="no EQ, de-esser or compression on the voice -- for a human "
                         "recording, which has already been mixed by someone who knew "
                         "what they were doing. Shaping it again only makes it worse.")
    ap.add_argument("--music", type=Path, default=None, help="music bed; omit to master voice alone")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lufs", type=float, default=-16.0, help="integrated loudness target")
    ap.add_argument("--tp", type=float, default=-1.5, help="true-peak ceiling in dBTP")
    ap.add_argument("--lra", type=float, default=11.0, help="loudness range")
    ap.add_argument("--duck-db", type=float, default=9.0,
                    help="how far the music drops under the voice")
    ap.add_argument("--music-db", type=float, default=-4.0,
                    help="static level of the bed relative to unity")
    ap.add_argument("--music-start", type=float, default=0.0,
                    help="where in the bed to start; a partial assembly needs this")
    ap.add_argument("--music-dur", type=float, default=None,
                    help="seconds of the bed to use; defaults to the rest of the file")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()

    if not a.voice.is_file():
        raise SystemExit(f"لا ملف صوت: {a.voice}")
    if a.music and not a.music.is_file():
        raise SystemExit(f"لا ملف موسيقى: {a.music}")

    work = Path("/tmp/master-dub")
    work.mkdir(parents=True, exist_ok=True)
    voice_mastered = work / "voice-mastered.wav"

    # --- the voice: shaped, then levelled against its own measurement ---------
    print("  الصوت: تنظيف وضبط ديناميكي", flush=True)
    m = measure(a.voice, a.lufs, a.tp, a.lra)
    if m:
        print(f"    المقاس: {m.get('input_i')} LUFS · قمة {m.get('input_tp')} dBTP · "
              f"مدى {m.get('input_lra')}", flush=True)
    apply_loudnorm_second_pass(a.voice, voice_mastered, a.lufs, a.tp, a.lra, m,
                               extra="" if a.plain_voice else VOICE_CHAIN)

    if not a.music:
        run(["-i", str(voice_mastered), "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s16le", "-y", str(a.out)])
        print(f"  ✓ صوت مُعالَج بلا موسيقى → {a.out}", flush=True)
        return 0

    # --- the bed: static level, then ducked by the voice ----------------------
    print(f"  الموسيقى: خفض {a.duck_db:.0f} د.ب تحت الكلام", flush=True)
    mix = work / "mix.wav"
    # The bed is the whole film, so a partial assembly has to say where in it to
    # start -- otherwise the first minute of a twelve-minute cut is scored with
    # the music of the opening titles.
    if a.music_dur is not None:
        trim = (f",atrim=start={a.music_start:.3f}:end={a.music_start + a.music_dur:.3f}"
                f",asetpts=N/SR/TB")
    elif a.music_start:
        trim = f",atrim=start={a.music_start:.3f},asetpts=N/SR/TB"
    else:
        trim = ""
    # The voice is split: one copy is heard, the other drives the compressor and
    # is never mixed, which is the whole point of a sidechain.
    fc = (
        f"[0:a]{'' if a.plain_voice else VOICE_CHAIN + ','}loudnorm=I={a.lufs}:TP={a.tp}:LRA={a.lra}"
        f":measured_I={m.get('input_i', a.lufs)}:measured_TP={m.get('input_tp', a.tp)}"
        f":measured_LRA={m.get('input_lra', a.lra)}:measured_thresh={m.get('input_thresh', -70)}"
        f":offset={m.get('target_offset', 0)}:linear=true[voice];"
        f"[voice]asplit=2[voiceout][voicesc];"
        f"[1:a]volume={a.music_db}dB{trim},volume=0dB[music];"
        f"[music][voicesc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350"
        f":makeup=1:level_sc=1.5[ducked];"
        f"[voiceout][ducked]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[mix]"
    )
    run(["-i", str(a.voice), "-i", str(a.music), "-filter_complex", fc,
         "-map", "[mix]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
         "-y", str(mix)])

    # --- final loudness on the mix, limiter last ------------------------------
    print("  المزيج: ضبط المستوى النهائي", flush=True)
    mm = measure(mix, a.lufs, a.tp, a.lra)
    if mm:
        print(f"    قبل الضبط: {mm.get('input_i')} LUFS · قمة {mm.get('input_tp')} dBTP", flush=True)
    apply_loudnorm_second_pass(mix, a.out, a.lufs, a.tp, a.lra, mm,
                               extra="alimiter=limit=0.94:attack=5:release=50")
    if mm:
        print(f"    بعد الضبط: {a.lufs} LUFS · سقف {a.tp} dBTP", flush=True)

    if a.report:
        a.report.write_text(json.dumps({
            "voice": str(a.voice), "music": str(a.music), "out": str(a.out),
            "target_lufs": a.lufs, "true_peak_dbtp": a.tp, "loudness_range": a.lra,
            "duck_db": a.duck_db, "music_db": a.music_db,
            "voice_chain": VOICE_CHAIN, "measured_voice": m, "measured_mix": mm,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  ✓ المزيج المُعالَج → {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
