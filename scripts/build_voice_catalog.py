#!/usr/bin/env python3
"""Build the voice shop: 100 slots, real audio where a voice exists, an honest plan where it does not.

Two rules shape this file, and both come from demands the user has already made:

1. **A sample must be the thing you would actually get.** Every processed row carries its
   exact ffmpeg chain in `chain`, and the mix step applies that same chain to the finished
   narration. So "mature, deep, warm" is not an adjective in a table - it is a reproducible
   filter, measurable before and after.
2. **A slot that cannot be voiced says so.** The registered voices were tested against region
   accents (ar-SA, ar-MA, ar-AE, ar-DZ, ar-KW, ar-LB, ar-IQ, ar-TN, ar-JO) and every one of
   them refused them; only bare Arabic and the Egyptian tag they were registered with are
   honoured. So a Saudi or Moroccan row stays `queued` with that reason written on it - never
   an Egyptian clip pitch-bent and presented as a dialect.

    python scripts/build_voice_catalog.py --project hajj-dream-2108415 --total 100 \
        --seconds 10 --json docs/voice-shop.json --audio-dir docs/voice-shop
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe  # noqa: E402

SR = 44100
# name, pitch shift in semitones, amplitude tremor, what the row is actually doing
AGES = [
    ("يافع", 4.0, 0.0, "pitch أعلى بأربع أنصاف نغمة، لا صوت طفل حقيقي"),
    ("شاب", 1.5, 0.0, "pitch أعلى بدرجة ونصف"),
    ("رجل", 0.0, 0.0, "الخام كما سُجّل، بدون أي معالجة"),
    ("مسن", -3.5, 0.35, "pitch أخفض بثلاث أنصاف + ارتعاش مستوى خفيف"),
]
TONES = {
    "محايد": "",
    "دافئ": "bass=g=2.5:f=220:t=s,treble=g=-1.5:f=4200:t=s",
    "ساطع": "treble=g=3:f=3200:t=s,highpass=f=95",
    "عميق": "bass=g=4.5:f=120:t=s,lowpass=f=7200",
}
GENDER_NOTE = ("كل ما مسجل من الأصوات في هذه الجلسة رجولي؛ الإناث تُطلب بمباريات على المنصة، "
               "ولا تُصطنع بتغيير النبرة — لن يُعرض صوت رجل معدّل على أنه صوت امرأة")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mono(path: Path, seconds: float) -> np.ndarray:
    raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-t", f"{seconds:.2f}",
                          "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def take_facts(audio: np.ndarray) -> dict:
    from youtube_auto_dub import affect

    facts = {"seconds": round(len(audio) / SR, 2)}
    for key, value in affect.pitch_stats(audio, SR).items():
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            facts[key] = round(float(value), 2)
    facts["level_dbfs"] = round(20 * math.log10(max(float(np.sqrt((audio ** 2).mean())), 1e-9)), 2)
    return facts


def chain_for(age: str, tone: str) -> str:
    shift, tremor = next((a[1], a[2]) for a in AGES if a[0] == age)
    parts = []
    if abs(shift) > 0.01:  # pitch only: rate up then tempo back, so the length does not move
        factor = 2 ** (shift / 12.0)
        parts.append(f"asetrate={SR}*{factor:.5f},aresample={SR},atempo={1/factor:.5f}")
    if tremor > 0.01:
        parts.append(f"tremolo=f=5.5:d={tremor:.2f}")
    if TONES.get(tone):
        parts.append(TONES[tone])
    return ",".join(parts)


def render_variant(source: Path, out: Path, chain: str, seconds: float) -> dict:
    filters = [f"atrim=0:{seconds:.2f}", "aresample=44100",
               "aformat=sample_fmts=fltp:channel_layouts=mono"]
    filters.append(chain if chain else "anull")
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=9")
    done = subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(source),
                           "-af", ",".join(filters), "-c:a", "libmp3lame", "-b:a", "96k", str(out)],
                          capture_output=True)
    if done.returncode:
        raise SystemExit((done.stderr or done.stdout).decode()[-500:])
    info = sf.info(out)
    from youtube_auto_dub import affect

    # Measure the rendered file, not the intention: if the label says the pitch went up four
    # semitones, that number has to be readable in the output or the row is a lie.
    audio = np.asarray(sf.read(out)[0], dtype=np.float32)
    stats = affect.pitch_stats(audio, info.samplerate)
    return {"preview_seconds": round(info.frames / info.samplerate, 2), "bytes": out.stat().st_size,
            "sha256": sha256(out)[:16],
            "preview": {"f0_mean": stats.get("f0_mean"), "f0_spread": stats.get("f0_spread"),
                        "level_dbfs": round(20 * math.log10(max(float(np.sqrt((audio ** 2).mean())), 1e-9)), 2)}}


def chain_check(chain: str) -> dict:
    """Prove what the chain does by running a 200 Hz tone through it.

    Pitch on real speech is a bad instrument for this - the estimator follows formants and
    gates, so a +4-semitone row measured on a sentence read a change of +2.4. A reference
    tone answers the actual question: how far did the pitch move, and did the length move.
    """
    out = subprocess.run([ffmpeg_exe(), "-v", "error", "-f", "lavfi",
                          "-i", "sine=frequency=200:sample_rate=44100:duration=2", "-af",
                          (chain if chain else "anull") + ",aresample=44100", "-ac", "1",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(out, dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    peak = float(np.fft.rfftfreq(len(audio), 1 / SR)[int(np.argmax(spectrum))])
    return {"applied_semitones": round(12 * math.log2(peak / 200.0), 2) if peak > 0 else None,
            "duration_ratio": round(len(audio) / (2 * SR), 3), "reference_hz": round(peak, 1)}


def discover(project: str, extra: list[str]) -> list[tuple[str, Path]]:
    base = ROOT / "dubs" / project / "voice-tests"
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    groups = [("gallery/voice-*.flac", "قارئ النص الكامل"),
              ("voice-0*-c*.flac", "اختبار اللهجة"),
              ("voice-0*-s*.flac", "عيّنة مشهدية")]
    for pattern, kind in groups:
        for path in sorted(base.glob(pattern)):
            if path not in seen:
                seen.add(path)
                out.append((f"{kind} · {path.stem}", path))
    for path in sorted((ROOT / "dubs" / project / "takes-dense").glob("part-0[0-2].flac")):
        if path not in seen:
            seen.add(path)
            out.append((f"سرد كثيف · {path.stem}", path))
    for pattern in extra:
        for path in sorted(Path(pattern).parent.glob(Path(pattern).name)):
            if path.suffix == ".flac" and path not in seen:
                seen.add(path)
                out.append((f"مضاف · {path.stem}", path))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="hajj-dream-2108415")
    ap.add_argument("--total", type=int, default=100)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--extra", nargs="*", default=[])
    ap.add_argument("--json", type=Path, default=Path("docs/voice-shop.json"))
    ap.add_argument("--audio-dir", type=Path, default=Path("docs/voice-shop"))
    ap.add_argument("--dialects", nargs="*", default=["سعودي", "إماراتي", "كويتي", "بحريني",
                                                      "شامي", "عراقي", "يمني", "مغربي",
                                                      "جزائري", "تونسي", "سوداني", "فصحى"])
    args = ap.parse_args()

    args.audio_dir.mkdir(parents=True, exist_ok=True)
    takes = discover(args.project, args.extra)
    if not takes:
        raise SystemExit(f"no raw takes found under dubs/{args.project}/voice-tests")

    # Cross every take with every age and tone, then walk the list in a stride so the first
    # rows are spread across voices instead of being four variants of whichever take came first.
    combos = [(label, path, age, tone)
              for label, path in takes
              for age, _shift, _tremor, _note in AGES
              for tone in TONES]
    stride = max(1, len(takes))
    order = sorted(range(len(combos)), key=lambda i: (i % stride, i // stride))

    # Real dialects and female voices need voices of their own from the platform, so their
    # slots are held open here rather than filled with a lookalike. Everything else is audio.
    plan_items = [(f"لكنة {d}", "dialect", d) for d in args.dialects]
    plan_items += [("صوت أنثى (فصحى)", "gender", "أنثى"), ("صوت أنثى (مصري)", "gender", "أنثى"),
                   ("صوت أنثى (شامي)", "gender", "أنثى")]
    held = min(len(plan_items), max(0, args.total - 60))
    ready_slots = args.total - held

    CHECKS: dict[str, dict] = {}
    rows: list[dict] = []
    for counter, index in enumerate(order[:ready_slots], start=1):
        label, path, age, tone = combos[index]
        audio = mono(path, args.seconds)
        chain = chain_for(age, tone)
        out = args.audio_dir / f"sample-{counter:03d}.mp3"
        info = render_variant(path, out, chain, args.seconds)
        facts = take_facts(audio)
        info["chain_check"] = CHECKS.setdefault(chain, chain_check(chain))
        note = next(a[3] for a in AGES if a[0] == age)
        rows.append({
            "id": counter,
            "title": f"{label} · {age} · نبرة {tone}",
            "gender": "ذكر", "age": age, "dialect": "مصري", "tone": tone,
            "base_voice": path.stem, "source_take": str(path.relative_to(ROOT)),
            "chain": chain or "anull",
            "chain_note": (note if age != "رجل" else "") + ("؛ " + f"نبرة {tone}" if tone != "محايد" else ""),
            "raw": not chain, "status": "ready", "expected_f0_delta_st": round(next(a[1] for a in AGES if a[0] == age), 2), "facts": facts, **info,
            "preview_url": f"voice-shop/{out.name}",
        })

    for what, kind, value in plan_items[:held]:
        row = {
            "id": len(rows) + 1,
            "title": f"{what} — لم يُسجَّل بعد",
            "gender": "أنثى" if kind == "gender" else "ذكر",
            "age": "رجل" if kind == "dialect" else "شاب",
            "dialect": value if kind == "dialect" else "فصحى",
            "tone": "محايد", "base_voice": None, "status": "queued",
            "preview": None, "bytes": 0, "chain": "anull", "chain_note": "", "facts": {},
            "chain_check": {"applied_semitones": 0.0, "duration_ratio": 1.0, "reference_hz": 200.0},
        }
        if kind == "dialect":
            row["why"] = (f"الأصوات المسجّلة على المنصة رفضت وسم اللهجة المطلوبة لـ«{value}»، "
                          "ولم يُقبل سوى ar و ar-EG. يلزم battle صوت جديد بنفس اللهجة ثم يُضاف "
                          "مقطعه الحقيقي هنا.")
        else:
            row["why"] = ("لا يوجد صوت أنثوي مسجّل في هذه الجلسة؛ يُطلب بإضافة صوت من المنصة. "
                          "لن يُصنع صوت امرأة من صوت رجل بالتعديل.")
        row["note"] = "لا يعرض المنزّل بصوت وهمي — يبقى محجوزًا حتى يُسجَّل"
        rows.append(row)

    ready = sum(1 for row in rows if row["status"] == "ready")
    doc = {
        "built_by": "scripts/build_voice_catalog.py",
        "project": args.project,
        "total_slots": len(rows), "ready": ready, "queued": len(rows) - ready,
        "gender_note": GENDER_NOTE,
        "how_the_choice_is_used": ("الضغط على «اختر هذه العينة» يكتب docs/voice-choice.json في "
                                   "الفرع؛ الدبلجة تُبنى من نفس السلسلة: الصوت + chain، لا وصف "
                                   "لفظي. لو تغيّر الصوت أو الجلسة، السلسلة تُعاد من هنا."),
        "samples": rows,
    }
    args.json.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_slots": len(rows), "ready": ready, "queued": len(rows) - ready,
                      "distinct_base_takes": len(takes), "json": str(args.json),
                      "audio_dir": str(args.audio_dir),
                      "audio_bytes": sum(row.get("bytes", 0) for row in rows)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
