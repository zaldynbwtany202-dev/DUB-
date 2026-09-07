import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

from youtube_auto_dub.neural_background import (
    apply_masks, band_weights, encode_bands, fetch_model, spectrum, waveform,
)


@pytest.fixture
def config():
    return json.loads((Path(__file__).resolve().parents[1] / "config/uvr-background.json").read_text())


def test_stft_roundtrip_keeps_length_phase_and_stereo():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.05, size=(2, 17329)).astype(np.float32)
    result = waveform(spectrum(x, 960, 480), 960, 480, x.shape[-1])
    assert result.shape == x.shape
    assert np.max(np.abs(result - x)) < 1e-6


def test_encoder_has_the_model_frequency_layout(config):
    x = np.zeros((2, 44100), dtype=np.float32)
    mag = encode_bands(x, config)
    assert mag.shape[:2] == (2, 673)
    assert mag.shape[-1] >= 91
    assert np.all(mag == 0)


def test_zero_neural_masks_cannot_restore_original_speech(config):
    t = np.arange(44100, dtype=np.float32) / 44100
    x = np.stack([np.sin(2 * np.pi * 1000 * t)] * 2) * 0.25
    masks = np.zeros_like(encode_bands(x, config))
    result = apply_masks(x, masks, config)
    assert result.shape == x.shape
    assert np.max(np.abs(result)) == 0


def test_unity_mask_preserves_an_in_band_tone_and_phase(config):
    t = np.arange(44100, dtype=np.float32) / 44100
    x = np.stack([np.sin(2 * np.pi * 1000 * t)] * 2) * 0.25
    masks = np.ones_like(encode_bands(x, config))
    result = apply_masks(x, masks, config)
    assert np.corrcoef(result[0], x[0])[0, 1] > 0.999
    assert np.sqrt(np.mean(result**2)) == pytest.approx(np.sqrt(np.mean(x**2)), rel=0.01)


def test_more_aggressive_mask_does_not_add_or_invent_audio(config):
    t = np.arange(44100, dtype=np.float32) / 44100
    x = np.stack([np.sin(2 * np.pi * 700 * t)] * 2) * 0.25
    masks = np.full_like(encode_bands(x, config), 0.5)
    soft = apply_masks(x, masks, config, power=1)
    strong = apply_masks(x, masks, config, power=2)
    assert np.allclose(strong, soft * 0.5, atol=1e-6)


def test_mask_power_cannot_amplify_or_be_nonfinite(config):
    with pytest.raises(ValueError):
        apply_masks(np.zeros((2, 100)), np.zeros((2, 673, 1)), config, power=float("nan"))
    with pytest.raises(ValueError):
        apply_masks(np.zeros((2, 100)), np.zeros((2, 673, 1)), config, power=0.5)


def test_band_weights_are_bounded_and_zero_outside_trained_band(config):
    freq = np.linspace(0, 22050, 1000)
    for band in config["bands"]:
        w = band_weights(freq, band)
        assert np.all((w >= 0) & (w <= 1))
        assert w[-1] == 0


def test_known_model_cache_does_not_require_network(tmp_path, monkeypatch):
    data = b"model fixture"
    path = tmp_path / "uvr-fp16-sim.onnx"
    path.write_bytes(data)
    cfg = {"model_sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr("youtube_auto_dub.neural_background.subprocess.run", lambda *a, **kw: pytest.fail("Unexpected network call"))
    assert fetch_model(cfg, tmp_path) == path


def test_lfs_pointer_is_rejected_and_not_installed(tmp_path, monkeypatch):
    cfg = {"model_sha256": "0" * 64, "model_bytes": 63846557, "model_repo": "owner/repo", "model_path": "model.onnx", "model_revision": "abc"}
    def fake_run(*args, **kwargs):
        kwargs["stdout"].write(b"version https://git-lfs.github.com/spec/v1\n")
    monkeypatch.setattr("youtube_auto_dub.neural_background.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="Git LFS pointer"):
        fetch_model(cfg, tmp_path)
    assert not (tmp_path / "uvr-fp16-sim.onnx").exists()
    assert not (tmp_path / "uvr-fp16-sim.download").exists()
