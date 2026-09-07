from pathlib import Path

import pytest

from scripts import mix_original_background as mixer


def test_music_is_ducked_not_a_second_narrator_overlay():
    graph = mixer.mix_graph(108.9, 2.5)
    assert "asplit=2[voice][side]" in graph
    assert "[bed][side]sidechaincompress=" in graph
    assert "ratio=3" in graph and "detection=rms" in graph
    assert "[voice][ducked]amix=inputs=2:duration=first:normalize=0" in graph
    assert "loudnorm=I=-16:TP=-1.5" in graph
    assert "volume=2.500dB" in graph
    assert "afade=t=out:st=108.550000:d=0.350000" in graph


@pytest.mark.parametrize("duration,gain", [(0, 0), (-1, 0), (float("nan"), 0), (10, float("inf")), (10, 13), (10, -31)])
def test_invalid_mix_parameters_cannot_reach_ffmpeg(duration, gain):
    with pytest.raises(ValueError):
        mixer.mix_graph(duration, gain)


def test_tiny_clip_has_nonnegative_fade_times():
    graph = mixer.mix_graph(0.1, 0)
    assert "afade=t=out:st=0.000000:d=0.100000" in graph


def test_missing_background_is_not_replaced_with_original_speech(tmp_path):
    video = tmp_path / "approved.mp4"
    video.write_bytes(b"fixture")
    with pytest.raises(FileNotFoundError):
        mixer.mix(video, tmp_path / "missing.flac", tmp_path / "out.mp4", tmp_path / "work")
    assert not (tmp_path / "out.mp4").exists()


def test_approved_dub_cannot_be_overwritten(tmp_path):
    video = tmp_path / "approved.mp4"
    video.write_bytes(b"fixture")
    background = tmp_path / "stem.flac"
    background.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="overwrite"):
        mixer.mix(video, background, video, tmp_path / "work")
    assert video.read_bytes() == b"fixture"


def test_misaligned_stem_is_rejected(tmp_path, monkeypatch):
    video = tmp_path / "approved.mp4"
    background = tmp_path / "stem.flac"
    video.write_bytes(b"fixture")
    background.write_bytes(b"fixture")
    monkeypatch.setattr(mixer, "probe_duration", lambda p: 10 if p == video else 8)
    with pytest.raises(ValueError, match="timelines"):
        mixer.mix(video, background, tmp_path / "out.mp4", tmp_path / "work")
    assert not (tmp_path / "work").exists()
