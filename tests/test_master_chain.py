"""The room-tone gate must accept air and refuse hums, ducked music and shifted speech."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_source_sync_render import chain_air_evidence, master_chain_gate  # noqa: E402

SR = 8000
AIR_DBFS = -32.0
SPEECH = [(2.0, 6.0), (10.0, 14.0), (18.0, 22.0), (26.0, 30.0)]
PLAN = {"timeline": [{"written_begin": a, "take_piece": [0.0, b - a]} for a, b in SPEECH]}


def _bursts(rng: np.random.Generator, gain: float = 0.5) -> np.ndarray:
    track = np.zeros(SR * 32)
    for lo, hi in SPEECH:
        track[int(lo * SR):int(hi * SR)] = rng.normal(0, gain, int((hi - lo) * SR))
    return track


def _air(rng: np.random.Generator, level_dbfs: float = AIR_DBFS) -> np.ndarray:
    return rng.normal(0, 10 ** (level_dbfs / 20), SR * 32)


@pytest.fixture
def base():
    rng = np.random.default_rng(7)
    return rng, _bursts(rng)


def test_air_passes(base):
    rng, shaped = base
    mc = chain_air_evidence(shaped + _air(rng), shaped, PLAN, SR, AIR_DBFS)
    assert mc["usable"]
    assert abs(mc["air_level_error_db"]) <= 1.0
    assert mc["spectral_flatness_db"] > -3.0
    assert master_chain_gate(mc) is True


def test_hum_is_not_air(base):
    rng, shaped = base
    t = np.arange(SR * 32) / SR
    # A tonal layer at -23 dBFS under the voice: quiet enough to hide in a waveform view,
    # loud enough that a listener would call it "music", which is exactly what is forbidden.
    hum = 0.02 * np.sin(2 * np.pi * 220 * t) + 0.012 * np.sin(2 * np.pi * 440 * t)
    mc = chain_air_evidence(shaped + _air(rng) + hum, shaped, PLAN, SR, AIR_DBFS)
    assert master_chain_gate(mc) is False
    assert mc["spectral_peakiness_db"] > 12.0
    assert mc["air_level_dbfs"] < -28.0  # the level alone looks fine; the spectrum catches it


def test_ducked_music_pumps_with_the_voice(base):
    """A bed that disappears under speech (side-chain ducking) is a mix, not room tone."""
    rng, shaped = base
    air = _air(rng)
    mask = np.zeros_like(air)
    for lo, hi in SPEECH:
        mask[int(lo * SR):int(hi * SR)] = 1.0
    mc = chain_air_evidence(shaped + air * (1.0 - 0.95 * mask), shaped, PLAN, SR, AIR_DBFS)
    assert master_chain_gate(mc) is False
    assert mc["level_under_speech_minus_in_gaps_db"] < -4.0


def test_shifted_copy_is_rejected(base):
    rng, shaped = base
    delayed = np.concatenate([np.zeros(SR // 100), shaped])[:-SR // 100] + _air(rng)
    mc = chain_air_evidence(delayed, shaped, PLAN, SR, AIR_DBFS)
    assert master_chain_gate(mc) is False
    assert mc["max_time_lag_ms"] > 0.5


def test_wrong_level_is_rejected(base):
    rng, shaped = base
    mc = chain_air_evidence(shaped + _air(rng, AIR_DBFS + 9.0), shaped, PLAN, SR, AIR_DBFS)
    assert master_chain_gate(mc) is False
    assert abs(mc["air_level_error_db"]) > 4.0


def test_unusable_without_enough_silence(base):
    rng, shaped = base
    crowded = {"timeline": [{"written_begin": 0.0, "take_piece": [0.0, 31.5]}]}
    mc = chain_air_evidence(shaped + _air(rng), shaped, crowded, SR, AIR_DBFS)
    assert mc["usable"] is False
    assert master_chain_gate(mc) is False
