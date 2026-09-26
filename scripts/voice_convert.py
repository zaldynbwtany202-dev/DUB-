#!/usr/bin/env python3
"""Convert a synthetic voice toward the film's narrator, in the timbre domain.

The neural route is closed here: XTTS-v2 needs 1.9 GB of weights, every host that
carries them is unreachable from this sandbox, and no PyPI package bundles a voice
model. So the conversion is done the way voice conversion worked before deep
learning, which is also the way that does not need a corpus of parallel training
data -- because the two speakers are saying substantially the same Arabic text.

The pitch is already there. Measured, voice-13 sits 0.45 semitones from the
narrator, which is below the threshold anyone can hear as a different person. What
is left is timbre: the shape of the vocal tract, visible in the long-term average
spectrum. Taking the long-term spectrum of the narrator and dividing it by the
long-term spectrum of the candidate gives, band by band, how much to lift and cut
to make one sound like the other.

Two things make that ratio meaningful rather than a trick:

  * Both speakers read the same script, so their long-term spectra see roughly the
    same distribution of phonemes. The difference that remains is the tract, not
    the words.
  * The ratio is smoothed hard before it is applied -- a cepstral truncation to
    about forty coefficients. A fine-grained ratio would transplant the narrator's
    individual vowels onto the candidate's words and sound like a collage; a
    smooth one moves the timbre and leaves the performance alone.

Pitch is corrected as far as it can be without artifact: rubberband with
formant=preserved, and only by the measured difference, which for a well-cast voice
is a fraction of a semitone.

    .venv/bin/python scripts/voice_convert.py --target work/<slug>/stems/vocal-*.mp3 \\
        --source take.mp3 --out converted.wav [--report report.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ffmpeg() -> str:
    hits = glob.glob(str(ROOT / ".venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    if not hits:
        raise SystemExit("ffmpeg غير موجود — شغّل scripts/rebuild_env.sh")
    return hits[0]


def decode(path: Path, dest: Path, ss: float = 0.0, t: float | None = None,
           sr: int = 22050) -> None:
    cmd = [ffmpeg(), "-v", "error"]
    if ss:
        cmd += ["-ss", f"{ss:.3f}"]
    if t:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", str(path), "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", "-y", str(dest)]
    subprocess.run(cmd, check=True)


def f0_track(x, sr, hop=0.01):
    import numpy as np
    win = int(0.04 * sr)
    h = int(hop * sr)
    out = []
    for i in range(0, max(1, len(x) - win), h):
        f = x[i:i + win]
        if np.sqrt(np.mean(f ** 2)) < 0.008:
            out.append(0.0)
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, "full")[win - 1:]
        lo, hi = int(sr / 350), int(sr / 70)
        if hi >= len(ac):
            out.append(0.0)
            continue
        seg = ac[lo:hi]
        lag = lo + int(np.argmax(seg))
        out.append(sr / lag if ac[lag] > 0.3 * ac[0] else 0.0)
    return np.array(out)


def median_f0(x, sr) -> float:
    import numpy as np
    t = f0_track(x, sr)
    v = t[t > 0]
    return float(np.median(v)) if v.size else 0.0


def long_term_envelope(x, sr, n_fft=1024, cepstra=40):
    """Smoothed spectral envelope of a long recording, in dB per bin.

    The cepstral truncation is what separates timbre from words: keeping the first
    few dozen coefficients keeps the vocal-tract shape and throws away the
    phoneme-by-phoneme detail that would otherwise be transplanted."""
    import numpy as np
    hop = n_fft // 4
    frames = max(1, (len(x) - n_fft) // hop)
    acc = np.zeros(n_fft // 2 + 1)
    used = 0
    for i in range(frames):
        f = x[i * hop:i * hop + n_fft]
        if np.sqrt(np.mean(f ** 2)) < 0.008:
            continue
        w = np.hanning(n_fft)
        m = np.abs(np.fft.rfft(f * w))
        if m.sum() < 1e-6:
            continue
        acc += np.log(m + 1e-10)
        used += 1
    if not used:
        raise SystemExit("لا كلام كافٍ لحساب الطابع الصوتي")
    logmean = acc / used
    ceps = np.fft.irfft(logmean, n=n_fft)[:cepstra]
    env = np.fft.rfft(ceps, n=n_fft).real
    return env, used


def apply_envelope(x, sr, ratio_env, n_fft=1024, strength=1.0):
    """Filter x so its smoothed spectrum moves toward the target by ratio_env.

    Overlap-add with a Hann window, and the unmodified phase is kept, so the
    words stay exactly where they were and only the colour changes."""
    import numpy as np
    hop = n_fft // 4
    w = np.hanning(n_fft)
    out = np.zeros(len(x) + n_fft)
    norm = np.zeros_like(out)
    gain = np.exp(np.clip(ratio_env * strength, -3.0, 3.0))   # cap at ~26 dB
    for i in range(0, max(1, len(x) - n_fft), hop):
        f = x[i:i + n_fft]
        if len(f) < n_fft:
            f = np.pad(f, (0, n_fft - len(f)))
        spec = np.fft.rfft(f * w)
        spec = spec * gain
        y = np.fft.irfft(spec, n=n_fft) * w
        out[i:i + n_fft] += y
        norm[i:i + n_fft] += w ** 2
    out = out / np.maximum(norm, 1e-6)
    return out[:len(x)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, required=True,
                    help="long recording of the voice to match (the narrator)")
    ap.add_argument("--source", type=Path, required=True, help="take to convert")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sr", type=int, default=22050)
    ap.add_argument("--cepstra", type=int, default=40)
    ap.add_argument("--strength", type=float, default=1.0,
                    help="how much of the measured difference to apply")
    ap.add_argument("--no-pitch", action="store_true")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args()

    import numpy as np
    import soundfile as sf

    tmp = Path(tempfile.mkdtemp(prefix="vconv-"))
    tgt_wav = tmp / "target.wav"
    src_wav = tmp / "source.wav"
    # Two minutes of the narrator is plenty for a stable long-term spectrum and
    # costs a few seconds; his whole twenty-nine would only make the average smoother.
    decode(a.target, tgt_wav, t=120.0, sr=a.sr)
    decode(a.source, src_wav, sr=a.sr)
    tgt, _ = sf.read(str(tgt_wav))
    src, _ = sf.read(str(src_wav))
    tgt = np.asarray(tgt, float)
    src = np.asarray(src, float)

    f_tgt = median_f0(tgt, a.sr)
    f_src = median_f0(src, a.sr)
    print(f"  النبرة: الهدف {f_tgt:.1f} هرتز · المصدر {f_src:.1f} هرتز "
          f"({12*np.log2(f_src/f_tgt):+.2f} نصف نغمة)")

    env_t, used_t = long_term_envelope(tgt, a.sr, cepstra=a.cepstra)
    env_s, used_s = long_term_envelope(src, a.sr, cepstra=a.cepstra)
    print(f"  الطابع: حُسب من {used_t} إطارًا للهدف و{used_s} للمصدر")

    # Pitch first, so the envelope is measured against the voice that will be heard.
    pitch_note = "بلا تعديل"
    work_src = src_wav
    if not a.no_pitch and f_src > 0 and f_tgt > 0:
        semis = 12 * np.log2(f_tgt / f_src)
        if abs(semis) > 0.05:
            ratio = f_tgt / f_src
            shifted = tmp / "shifted.wav"
            subprocess.run([ffmpeg(), "-v", "error", "-i", str(src_wav),
                            "-af", f"rubberband=pitch={ratio:.6f}:formant=preserved"
                                   f":pitchq=quality",
                            "-ar", str(a.sr), "-ac", "1", "-c:a", "pcm_s16le",
                            "-y", str(shifted)], check=True)
            work_src = shifted
            src2, _ = sf.read(str(shifted))
            src = np.asarray(src2, float)
            pitch_note = f"{semis:+.2f} نصف نغمة (formant=preserved)"
    print(f"  الطبقة: {pitch_note}")

    ratio_env = env_t - env_s
    done = apply_envelope(src, a.sr, ratio_env, strength=a.strength)
    sf.write(str(a.out), done.astype(np.float32), a.sr)

    # Measure the result on the same terms it was built from.
    env_d, _ = long_term_envelope(done, a.sr, cepstra=a.cepstra)
    before = float(np.mean(np.abs(ratio_env)))
    after = float(np.mean(np.abs(env_t - env_d)))
    f_new = median_f0(done, a.sr)
    print(f"  فارق الطابع قبل: {before:.3f} dB/بن · بعد: {after:.3f} dB/بن "
          f"({100*(1-after/before):.0f}% أقرب)")
    print(f"  النبرة بعد: {f_new:.1f} هرتز (الهدف {f_tgt:.1f})")

    if a.report:
        a.report.write_text(json.dumps({
            "target": str(a.target), "source": str(a.source), "out": str(a.out),
            "f0_target_hz": round(f_tgt, 1), "f0_source_hz": round(f_src, 1),
            "f0_out_hz": round(f_new, 1), "pitch_correction": pitch_note,
            "cepstra": a.cepstra, "strength": a.strength,
            "envelope_distance_before_db": round(before, 3),
            "envelope_distance_after_db": round(after, 3),
            "frames_target": used_t, "frames_source": used_s,
            "method": "cepstral long-term spectral envelope transfer"
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
