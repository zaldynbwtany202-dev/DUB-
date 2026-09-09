"""Measure how a video *feels*, segment by segment, from signals - not from guesses.

A dub that is correctly placed and tonally flat still sounds wrong: an exciting moment read in
a calm voice is the complaint "الإلقاء مش على الفيديو". Text is the only lever this pipeline has
over the agent voice, so the register has to be decided from something measurable first, and the
result has to be measurable afterwards.

Per stretch of speech the signals are:

* ``level_db``        - loudness of the original's voice (it rises with agitation);
* ``f0_mean``/``f0_spread`` - pitch centre and its semitone movement (pitch range is the strongest
  single tell of emphasis in Arabic narration);
* ``rate``            - words per second of speech;
* ``motion``/``cuts`` - picture energy and shot-cut density in the same stretch (an action beat
  shows up in the picture before it shows up in the words);
* ``crowd``           - how much of the stretch is speech (a wall of talk reads as pressure).

``classify`` turns z-scores of those into a named register with its evidence, and keeps the
suggestion separate from the final register so a human/agent reading the story can override a
row without destroying what the numbers said.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

FLOOR_HZ = 65.0
CEIL_HZ = 400.0


# --------------------------------------------------------------------------- audio signals

def frame_level(audio: np.ndarray, sample_rate: int, *, frame_s: float = 0.05) -> np.ndarray:
    """RMS per frame in dBFS, for envelope statistics."""
    size = max(1, int(round(sample_rate * frame_s)))
    usable = (len(audio) // size) * size
    frames = np.asarray(audio[:usable], dtype=np.float64).reshape(-1, size)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return 20.0 * np.log10(rms + 1e-9)


def f0_track(audio: np.ndarray, sample_rate: int, *, frame_s: float = 0.04,
             hop: float = 0.5, floor: float = FLOOR_HZ, ceil: float = CEIL_HZ) -> np.ndarray:
    """Pitch per frame by autocorrelation; NaN where the frame is not voiced.

    Deliberately model-free: this runs anywhere numpy runs, and the comparison it feeds is a
    relative one (how much the dub moves versus how much the original moves), so an estimator
    with a small bias is enough while a biased learned model would not be.
    """
    size = max(8, int(round(sample_rate * frame_s)))
    step = max(1, int(round(size * hop)))
    data = np.asarray(audio, dtype=np.float64)
    count = (len(data) - size) // step + 1
    if count < 1:
        return np.array([], dtype=float)
    view = np.lib.stride_tricks.as_strided(data, shape=(count, size),
                                           strides=(data.strides[0] * step, data.strides[0]))
    frames = view * np.hanning(size)
    energy = np.mean(frames ** 2, axis=1)
    nfft = 1 << int(math.ceil(math.log2(2 * size)))
    spectrum = np.fft.rfft(frames, n=nfft, axis=1)
    autocorr = np.fft.irfft(np.abs(spectrum) ** 2, n=nfft, axis=1)[:, :size]
    max_lag = min(size - 1, int(sample_rate / floor))
    min_lag = max(2, int(sample_rate / ceil))
    window = autocorr[:, min_lag:max_lag + 1]
    if window.shape[1] < 2:
        return np.array([], dtype=float)
    peak = window.argmax(axis=1) + min_lag
    height = window[np.arange(len(peak)), peak - min_lag]
    total = autocorr[:, 1:min_lag].max(axis=1) + 1e-12
    periodicity = height / total
    voiced = (periodicity > 0.32) & (energy > 1e-6)
    f0 = np.full(len(frames), np.nan)
    f0[voiced] = sample_rate / peak[voiced]
    return f0


def semitone_spread(f0: np.ndarray) -> float:
    """Standard deviation of pitch in semitones - the movement, not the level."""
    live = f0[np.isfinite(f0)]
    if live.size < 6:
        return 0.0
    notes = 12.0 * np.log2(live / float(np.median(live)))
    return float(np.std(notes))


def pitch_stats(audio: np.ndarray, sample_rate: int) -> dict[str, float]:
    f0 = f0_track(audio, sample_rate)
    live = f0[np.isfinite(f0)]
    if live.size == 0:
        return {"f0_mean": 0.0, "f0_spread": 0.0, "voiced_ratio": 0.0}
    return {"f0_mean": float(np.median(live)), "f0_spread": round(semitone_spread(f0), 3),
            "voiced_ratio": round(float(live.size / max(1, f0.size)), 3)}


# --------------------------------------------------------------------------- picture signals

def picture_energy(source, *, fps: float = 4.0, width: int = 64, height: int = 36) -> tuple[np.ndarray, np.ndarray]:
    """Frame difference (motion) at ``fps`` and the time of every hard cut.

    A cut is a motion spike far above the local median, which is what a recap editor uses to
    mark a beat change; the magnitude between cuts is how much is happening on screen.
    """
    import subprocess  # local import: only the CLI path needs ffmpeg

    from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe

    command = [ffmpeg_exe(), "-v", "error", "-i", str(source), "-vf",
               f"fps={fps},scale={width}:{height},format=gray", "-f", "rawvideo", "-pix_fmt",
               "gray", "-"]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    frame_bytes = width * height
    count = len(raw) // frame_bytes
    if count < 3:
        raise ValueError("picture decode produced too few frames")
    frames = np.frombuffer(raw[:count * frame_bytes], dtype=np.uint8).reshape(count, frame_bytes)
    frames = frames.astype(np.float64)
    motion = np.abs(np.diff(frames, axis=0)).mean(axis=1)
    times = (np.arange(motion.size) + 1) / fps
    floor = float(np.median(motion)) or 1.0
    cuts = [round(float(t), 3) for t, value in zip(times, motion) if value > max(6.0 * floor, 8.0)]
    return np.concatenate([[motion[0]], motion]), np.array(cuts, dtype=float)


def motion_stats(motion: np.ndarray, times: np.ndarray, start: float, end: float,
                 cuts: np.ndarray) -> dict[str, float]:
    inside = (times >= start) & (times < end)
    values = motion[inside] if inside.any() else np.array([0.0])
    in_cuts = int(np.sum((cuts >= start) & (cuts < end))) if len(cuts) else 0
    return {"motion": round(float(np.percentile(values, 90)), 3),
            "cuts": in_cuts,
            "cut_rate_per_min": round(in_cuts / max(0.5, (end - start) / 60.0), 2)}


# --------------------------------------------------------------------------- registers

REGISTERS = ("neutral", "warm", "excited", "tense", "grief", "astonished", "solemn", "playful")

WRITING_RULES: dict[str, dict[str, object]] = {
    "neutral": {"rate": 1.0, "punch": 0, "sentences": "medium",
                "note": "عادي: جُمل متوسطة، بدون تعجّب ولا نفي متتالي، علامة استفهام واحدة فقط لو السؤال في الأصل."},
    "warm": {"rate": 0.94, "punch": 1, "sentences": "long",
             "note": "دافي/حنون: بطء خفيف، «يا» و«توب» في النداء، جُمل طويلة متصلة بلا نقاط كثيرة."},
    "excited": {"rate": 1.12, "punch": 3, "sentences": "short",
                "note": "حماس: جُمل قصيرة، فعل أمر أو استفهام استنكاري، «النار»، تكرار كلمة مفتاحية، علامات تعجّب متتالية."},
    "tense": {"rate": 1.08, "punch": 2, "sentences": "short",
              "note": "توتر/خوف: جُمل مقطوعة، «وفجأة»، «مفيش جدال»، وقفات قصيرة تُكتب كنقاط، ونفيان متتاليان."},
    "grief": {"rate": 0.86, "punch": 0, "sentences": "medium",
              "note": "حُزن: بطء، «المسكين»، «قلة حيلة»، جُمل تنتهي بحال لا بخبر، ويا للنداء الحزين."},
    "astonished": {"rate": 1.02, "punch": 2, "sentences": "short",
                   "note": "تعجّب: «إيه؟»، «مستحيل»، «الراجل ما صدّق»، استفهام ثم جملة تفسير قصيرة."},
    "solemn": {"rate": 0.88, "punch": 0, "sentences": "long",
               "note": "وقار/ديني: ترديد «لبيك اللهم لبيك» كما هو، جُمل موزونة طويلة، لا تعجّب ولا مفردات عامية خفيفة."},
    "playful": {"rate": 1.10, "punch": 1, "sentences": "short",
                "note": "خفة ظل/ساخر: تشبيه شعبي، «هو عمل إيه يعني؟»، جُمل قصيرة وسؤال بلا جواب."},
}


def zscores(values: Sequence[float]) -> np.ndarray:
    data = np.asarray(list(values), dtype=np.float64)
    if data.size < 3:
        return np.zeros_like(data)
    spread = float(np.std(data)) or 1e-9
    return (data - float(np.mean(data))) / spread


def classify(*, level_z: float, f0_mean_z: float, f0_spread_z: float, rate_z: float,
             motion_z: float, cut_z: float, crowd: float) -> tuple[str, list[str]]:
    """Rule-based register from z-scores, with the reasons kept so they can be audited."""
    reasons: list[str] = []
    if rate_z > 0.6 and level_z > 0.45:
        reasons.append("كلام أسرع وأعلى")
        return "excited", reasons
    if f0_spread_z > 1.1 and motion_z > 0.7:
        reasons.append("ميل في النبرة مع حركة في الصورة")
        return "excited", reasons
    if (level_z > 0.5 and f0_mean_z > 0.55) or (cut_z > 1.0 and motion_z > 0.8):
        reasons.append("ضغط: صوت أعلى ومونتاج أسرع")
        return "tense", reasons
    if f0_spread_z > 1.0 and crowd < 0.5:
        reasons.append("نبرة متقلبة في مساحة كلام قصيرة")
        return "astonished", reasons
    if f0_mean_z < -0.55 and level_z < -0.35 and rate_z < -0.2:
        reasons.append("أخفض وأبطأ وأهدأ")
        return "grief", reasons
    if f0_spread_z < -0.75 and rate_z < -0.55 and motion_z < 0.0:
        reasons.append("نبرة ثابتة وبطيئة على صورة ساكنة")
        return "solemn", reasons
    if rate_z > 0.55 and f0_spread_z > 0.55 and level_z < 0.25:
        reasons.append("سريع بخفة بدون رفع صوت")
        return "playful", reasons
    if level_z > 0.2 and f0_spread_z > 0.25 and rate_z < 0.2:
        reasons.append("صوت أعلى شوية وميل ودود")
        return "warm", reasons
    reasons.append("كل الإشارات في حدود المتوسط")
    return "neutral", reasons
