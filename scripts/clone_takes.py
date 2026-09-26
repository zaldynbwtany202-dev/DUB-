#!/usr/bin/env python3
"""Clone the narrator onto every take, in one pass over a whole directory.

The single-take version recomputes the narrator's spectrum each time, which is
wasteful over seventy-eight takes and worse, inconsistent: each conversion would
be fitted to a slightly different measurement of the same man. Here the target is
measured once, from two minutes of his separated voice, and every take is moved
toward that one fixed target.

Two corrections per take, in this order:

  pitch   the measured median F0 difference, applied with rubberband. The default
          preserves formants, so the tract stays where it is and only the note
          changes -- the voice is the same man speaking higher, not a different
          man. --formant-shifted moves the formants with the pitch, which is a
          bigger change and is offered because the narrator is a higher voice and
          his formants are higher too; which of the two measures closer is decided
          by the envelope distance reported below, not by preference.

  timbre  the difference between the narrator's long-term spectral envelope and
          the take's, truncated to forty cepstral coefficients and applied
          overlap-add with the original phase. The truncation is what keeps this
          from transplanting his vowels onto these words.

    .venv/bin/python scripts/clone_takes.py --slug into-the-wild \\
        --in work/into-the-wild/takes --out work/into-the-wild/takes-clone \\
        [--formant preserved|shifted] [--strength 1.0] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from voice_convert import (  # noqa: E402
    apply_envelope, decode, ffmpeg, long_term_envelope, median_f0,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="into-the-wild")
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", dest="dst", type=Path, required=True)
    ap.add_argument("--target", type=Path, default=None,
                    help="narrator recording; defaults to the first separated stem")
    ap.add_argument("--sr", type=int, default=22050)
    ap.add_argument("--cepstra", type=int, default=40)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--formant", choices=["preserved", "shifted"], default="preserved")
    ap.add_argument("--limit", type=int, default=10 ** 9)
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()

    import numpy as np
    import soundfile as sf

    target = a.target
    if target is None:
        stems = sorted((ROOT / "work" / a.slug / "stems").glob("vocal-0*.mp3"))
        if not stems:
            raise SystemExit("لا صوت معزول للراوي — لا هدف للاستنساخ")
        target = stems[0]
    takes = sorted(list(a.src.glob("*.mp3")) + list(a.src.glob("*.wav")))[: a.limit]
    if not takes:
        raise SystemExit(f"لا تسجيلات في {a.src}")
    a.dst.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="clone-takes-"))
    tgt_wav = tmp / "target.wav"
    decode(target, tgt_wav, t=120.0, sr=a.sr)
    tgt, _ = sf.read(str(tgt_wav))
    tgt = np.asarray(tgt, float)
    f_tgt = median_f0(tgt, a.sr)
    env_t, frames_t = long_term_envelope(tgt, a.sr, cepstra=a.cepstra)
    print(f"  الهدف: {target.name} · النبرة {f_tgt:.1f} هرتز · الطابع من {frames_t} إطارًا")
    print(f"  التسجيلات: {len(takes)} · الوضع: formant={a.formant} · القوة {a.strength}")
    print()
    print("  التسجيل   النبرة قبل  بعد     تحويل   طابع قبل   طابع بعد  أقرب")

    rows = []
    for p in takes:
        decoded = tmp / (p.stem + "-dec.wav")
        decode(p, decoded, sr=a.sr)
        x, _ = sf.read(str(decoded))
        x = np.asarray(x, float)

        f_src = median_f0(x, a.sr)
        env_s, frames_s = long_term_envelope(x, a.sr, cepstra=a.cepstra)
        before = float(np.mean(np.abs(env_t - env_s)))

        shifted_path = tmp / (p.stem + "-shift.wav")
        semis = 0.0
        if f_src > 0 and f_tgt > 0:
            # The shifter delivers slightly less than it is asked for -- measured,
            # about 97 percent of the requested ratio -- so the factor is corrected
            # against the result rather than assumed. Two attempts is enough; a
            # third has never been needed and would only cost time.
            factor = f_tgt / f_src
            for attempt in range(3):
                if abs(12 * np.log2(factor)) < 0.05:
                    break
                subprocess.run(
                    [ffmpeg(), "-v", "error", "-i", str(p),
                     "-af", f"rubberband=pitch={factor:.6f}"
                            f":formant={a.formant}:pitchq=quality",
                     "-ar", str(a.sr), "-ac", "1", "-c:a", "pcm_s16le",
                     "-y", str(shifted_path)], check=True)
                probe, _ = sf.read(str(shifted_path))
                f_mid = median_f0(np.asarray(probe, float), a.sr)
                if f_mid <= 0:
                    break
                residual = f_tgt / f_mid
                if abs(12 * np.log2(residual)) < 0.4:
                    break
                factor *= residual
            semis = 12 * np.log2(factor)
        else:
            shifted_path = decoded

        y, _ = sf.read(str(shifted_path))
        y = np.asarray(y, float)

        # Envelope after the pitch move, so the correction is measured against what
        # will actually be heard rather than against the pre-shift take.
        env_y, _ = long_term_envelope(y, a.sr, cepstra=a.cepstra)
        ratio = env_t - env_y
        out = apply_envelope(y, a.sr, ratio, strength=a.strength)
        f_out = median_f0(out, a.sr)
        env_o, _ = long_term_envelope(out, a.sr, cepstra=a.cepstra)
        after = float(np.mean(np.abs(env_t - env_o)))

        dest = a.dst / (p.stem + ".wav")
        sf.write(str(dest), out.astype(np.float32), a.sr)
        closed = 100 * (1 - after / before) if before > 0 else 0
        print(f"  {p.stem}  {f_src:7.1f}  {f_out:6.1f}  {semis:+5.2f}  "
              f"{before:8.3f}  {after:8.3f}  {closed:4.0f}%", flush=True)
        rows.append({"take": p.stem, "f0_before": round(f_src, 1),
                     "f0_after": round(f_out, 1), "semitone_shift": round(semis, 2),
                     "envelope_before_db": round(before, 3),
                     "envelope_after_db": round(after, 3),
                     "closed_pct": round(closed)})

    if rows:
        import statistics as st
        print()
        print("  الوسيط: النبرة %.1f ← %.1f هرتز (الهدف %.1f) · "
              "الطابع %.3f ← %.3f dB/بن · أُغلق %.0f%%" % (
                  st.median(r["f0_before"] for r in rows),
                  st.median(r["f0_after"] for r in rows), f_tgt,
                  st.median(r["envelope_before_db"] for r in rows),
                  st.median(r["envelope_after_db"] for r in rows),
                  st.median(r["closed_pct"] for r in rows)))
    if a.report:
        a.report.write_text(json.dumps({
            "slug": a.slug, "target": str(target), "f0_target_hz": round(f_tgt, 1),
            "formant": a.formant, "strength": a.strength, "cepstra": a.cepstra,
            "takes": rows,
            "method": "cepstral long-term spectral envelope transfer + pitch match",
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
