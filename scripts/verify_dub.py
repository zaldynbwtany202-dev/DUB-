#!/usr/bin/env python
"""Verify a finished dub before it is presented: measure the file, not the intent.

The audit (scripts/audit_dub.py) judges the takes before the build. This judges
what the build actually produced, because the two can disagree: a role assigned
the wrong start second, a master trimmed to the wrong length, a mix that came out
0.4 LU loud, or a picture that was re-encoded instead of copied all look fine in a
log and are only visible in the file.

    python3 scripts/verify_dub.py --slug into-the-wild \
        --out previews/voice13-created.mp4 \
        --placement work/into-the-wild/build/placement-6-25.json \
        --master work/into-the-wild/build/into-the-wild-master-seg.wav \
        --source library/into-the-wild/source.local.mp4 --offset 136.60

Exit 0 only when every check passes; 2 when one fails.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

POLICY = Path("config/dubbing-qa.json")


def ffmpeg() -> list[str]:
    import imageio_ffmpeg
    return [imageio_ffmpeg.get_ffmpeg_exe()]


def ffprobe_duration(path: Path) -> float:
    """Read the container duration ffmpeg prints in its input banner.

    Asking for the decode summary instead was the first attempt and it returned
    0.00 for a perfectly good file: at -v error ffmpeg prints no progress line at
    all, and a zero here fails the duration check for the wrong reason.
    """
    out = subprocess.run(ffmpeg() + ["-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def frame(path: Path, ss: float) -> bytes:
    return subprocess.run(ffmpeg() + ["-v", "error", "-i", str(path), "-ss", f"{ss:.3f}",
                                      "-frames:v", "1", "-f", "rawvideo",
                                      "-pix_fmt", "rgb24", "-"],
                          capture_output=True).stdout


def psnr(a: bytes, b: bytes) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return float("inf")
    n = min(len(a), len(b))
    x = np.frombuffer(a[:n], dtype=np.uint8).astype(np.float64)
    y = np.frombuffer(b[:n], dtype=np.uint8).astype(np.float64)
    mse = float(((x - y) ** 2).mean())
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def loudness(path: Path) -> dict:
    out = subprocess.run(ffmpeg() + ["-v", "info", "-i", str(path),
                                     "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    lufs = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", out)
    peaks = re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", out)
    return {"lufs": float(lufs[-1]) if lufs else None,
            "true_peak": float(peaks[-1]) if peaks else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out", type=Path, required=True, help="الملف المُسلَّم")
    ap.add_argument("--placement", type=Path, default=None)
    ap.add_argument("--master", type=Path, default=None, help="الصوت المُسلَّم (wav)")
    ap.add_argument("--source", type=Path, default=None)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()

    pol = json.loads(POLICY.read_text(encoding="utf-8"))
    tim, lvl, pic = pol["timing"], pol["level"], pol["picture"]
    checks, rows = [], {}

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    # 1. placement: what the assembler did with the takes
    if a.placement and a.placement.exists():
        pl = json.loads(a.placement.read_text(encoding="utf-8"))
        gs = pl["groups"]
        max_late = max([g["late"] for g in gs], default=0.0)
        gaps = [max(0.0, b["start"] - g["end"]) for g, b in zip(gs, gs[1:])]
        max_gap = max(gaps, default=0.0)
        clamped = [g["group"] for g in gs if g.get("clamped")]
        tempo_max = max([g["tempo"] for g in gs], default=0.0)
        last_end = max([g["end"] for g in gs], default=0.0)
        rows.update({"max_late": round(max_late, 3), "max_gap": round(max_gap, 3),
                     "tempo_max": round(tempo_max, 3), "clamped": clamped,
                     "last_end": round(last_end, 3), "groups": len(gs)})
        check("lateness", max_late <= float(tim["max_lateness_seconds"]),
              f"أقصى تأخير {max_late:.2f} ث")
        check("silence_gap", max_gap <= float(tim["max_silence_gap_seconds"]),
              f"أطول صمت {max_gap:.2f} ث")
        check("tempo_ceiling", not clamped and tempo_max <= float(tim["atempo_ceiling"]) + 1e-9,
              f"أقصى سرعة {tempo_max:.2f}×" + (f" · مقصوصة: {clamped}" if clamped else ""))
    else:
        last_end = 0.0

    # 2. duration: the picture must be as long as the voice actually runs
    out_dur = ffprobe_duration(a.out)
    rows["out_seconds"] = round(out_dur, 2)
    if last_end:
        expect = last_end - a.offset
        check("duration", abs(out_dur - expect) <= 0.35,
              f"الملف {out_dur:.2f} ث · المتوقّع {expect:.2f} ث")
    else:
        check("duration", out_dur > 1.0, f"الملف {out_dur:.2f} ث")

    # 3. loudness of what actually ships
    if a.master and a.master.exists():
        l = loudness(a.master)
        rows.update({"lufs": l["lufs"], "true_peak": l["true_peak"]})
        if l["lufs"] is not None:
            dev = abs(l["lufs"] - float(lvl["master_lufs"]))
            check("loudness", dev <= float(lvl["lufs_tolerance"]) + 1e-9,
                  f"{l['lufs']:.2f} LUFS (المطلوب {lvl['master_lufs']}±{lvl['lufs_tolerance']})")
        if l["true_peak"] is not None:
            check("true_peak", l["true_peak"] <= float(lvl["true_peak_max_dbtp"]) + 1e-9,
                  f"{l['true_peak']:.2f} dBTP (السقف {lvl['true_peak_max_dbtp']})")
    if a.report and a.report.exists():
        rep = json.loads(a.report.read_text(encoding="utf-8"))
        rows["report"] = str(a.report)
        rows["report_mix_lufs"] = rep.get("measured_mix", {}).get("output_i")
        rows["report_true_peak"] = rep.get("measured_mix", {}).get("output_tp")

    # 4. picture: frame 0 of the deliverable against the source at the same second
    src = a.source or Path("library") / a.slug / "source.local.mp4"
    if src.exists():
        p = psnr(frame(a.out, 0.0), frame(src, a.offset))
        rows["first_frame_psnr"] = "inf" if p == float("inf") else round(p, 2)
        ok = p == float("inf") or p >= 45.0
        check("picture_copy", ok, f"أول إطار PSNR {'∞' if p == float('inf') else f'{p:.1f} د.ب'}")

    print(f"التحقّق — {a.out.name} ({out_dur:.2f} ث)\n")
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name:<16} {detail}")
    bad = [n for n, ok, _ in checks if not ok]
    print(f"\n  الحكم: {'مقبول للتسليم' if not bad else 'مرفوض: ' + '، '.join(bad)}")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(
            {"slug": a.slug, "out": str(a.out), "checks": [
                {"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
             "measured": rows, "verdict": "pass" if not bad else "fail", "failed": bad},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {a.json}")
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
