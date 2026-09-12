import numpy as np
import pytest

from youtube_auto_dub.island_sync import layout_islands, speech_islands


def _tone(seconds, sr=24000, freq=180.0):
    t = np.arange(int(seconds * sr)) / sr
    return (np.sin(2 * np.pi * freq * t) * 0.4).astype(np.float32)


def test_islands_ignore_gaps_and_keep_speech():
    sr = 24000
    audio = np.concatenate([_tone(0.4, sr), np.zeros(int(0.3 * sr), np.float32), _tone(0.5, sr)])
    islands = speech_islands(audio, sr)
    assert len(islands) == 2
    durs = [(b - a) / sr for a, b in islands]
    assert durs[0] == pytest.approx(0.4, abs=0.05)
    assert durs[1] == pytest.approx(0.5, abs=0.05)


def test_layout_starts_with_original_island_and_does_not_slow():
    sr = 24000
    original = np.concatenate([np.zeros(int(0.5 * sr), np.float32), _tone(1.0, sr), np.zeros(int(0.5 * sr), np.float32)])
    dub = np.concatenate([np.zeros(int(0.2 * sr), np.float32), _tone(0.6, sr)])
    out, report = layout_islands(dub, original, sr)
    assert len(out) == len(original)
    assert report["slowed"] is False
    assert report["tempo"] == 1.0
    assert report["speech_truncated"] is False
    # Speech energy should sit near the original island (0.5s), not at 0.
    energy = np.array([np.sqrt(np.mean(out[i:i + sr // 10] ** 2)) for i in range(0, len(out) - sr // 10, sr // 10)])
    peak_t = int(np.argmax(energy)) * 0.1
    assert 0.4 <= peak_t <= 1.2


def test_layout_refuses_slowdown_and_overlong_speech():
    sr = 24000
    original = _tone(0.4, sr)
    dub = _tone(2.0, sr)
    with pytest.raises(ValueError, match="shorten/re-record"):
        layout_islands(dub, original, sr)
    with pytest.raises(ValueError, match="slow"):
        layout_islands(dub, original, sr, min_tempo=0.85)
