import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from youtube_auto_dub import agent_tts as agent


def cue(audio, start=0, end=1, **extra):
    return {"index": 0, "start": start, "end": end, "text": "اختبار", "audio": str(audio), **extra}


def test_resolve_video_and_audio_from_cue_directory_without_mutation(tmp_path):
    sheet = {"video": "../../library/clip.mp4", "segments": [cue("agent_voice/seg.mp3")],
             "background_audio": "music.wav"}
    root = tmp_path / "dubs/clip"
    result = agent.resolve_cue_paths(sheet, root)
    assert result["video"] == str(tmp_path / "library/clip.mp4")
    assert result["segments"][0]["audio"] == str(root / "agent_voice/seg.mp3")
    assert result["background_audio"] == str(root / "music.wav")
    assert sheet["segments"][0]["audio"] == "agent_voice/seg.mp3"


def test_duration_uses_bundled_ffmpeg_if_ffprobe_missing(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")
    def unavailable():
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(agent, "ffprobe_exe", unavailable)
    monkeypatch.setattr(agent, "ffmpeg_exe", lambda: "/bundled/ffmpeg")
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "Duration: 00:01:48.90, start: 0.0"))
    assert agent.probe_duration(source) == 108.9


@pytest.mark.parametrize("start,end", [(-1, 1), (1, 1), (2, 1), (0, float("nan")), (0, 5)])
def test_invalid_timeline_is_not_silently_rendered(start, end):
    with pytest.raises(ValueError, match="Invalid cue"):
        agent.validate_timeline([cue("take.mp3", start, end)], 3)


def test_overlapping_takes_are_rejected():
    with pytest.raises(ValueError, match="Overlapping"):
        agent.validate_timeline([cue("a", 0, 2), cue("b", 1, 3)], 3)


def test_intentional_silences_are_allowed():
    assert len(agent.validate_timeline([cue("a", 0, 1), cue("b", 5, 6)], 7)) == 2


def test_edge_trim_keeps_the_complete_active_signal():
    sr = 24000
    tone = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 0.4).astype(np.float32)
    samples = np.concatenate([np.zeros(sr // 2), tone, np.zeros(sr // 2)]).astype(np.float32)
    result, report = agent.trim_silence(samples, sr)
    assert sr <= len(result) < len(samples)
    assert report["leading_silence_removed"] <= 0.5
    assert report["trailing_silence_removed"] <= 0.5
    assert np.allclose(result[int(0.08 * sr):int(0.08 * sr) + sr], tone)


def test_silent_take_is_not_successful_speech():
    with pytest.raises(ValueError, match="silent"):
        agent.trim_silence(np.zeros(24000, dtype=np.float32), 24000)


def test_long_take_must_not_be_truncated_to_fit(tmp_path):
    sr = 24000
    audio = tmp_path / "long.wav"
    samples = np.sin(2 * np.pi * 440 * np.arange(sr * 3) / sr).astype(np.float32) * 0.3
    sf.write(audio, samples, sr)
    with pytest.raises(ValueError, match="shorten/re-record"):
        agent.fit_take(cue(audio, 0, 0.5), tmp_path, 0)


def test_short_take_keeps_natural_rate_instead_of_slowing_to_fill(tmp_path):
    sr = 24000
    audio = tmp_path / "short.wav"
    samples = np.sin(2 * np.pi * 440 * np.arange(sr) / sr).astype(np.float32) * 0.3
    sf.write(audio, samples, sr)
    result, report = agent.fit_take(cue(audio, 0, 4), tmp_path, 0)
    assert report["tempo"] == 1.0
    assert abs(len(result) / sr - 1.0) < 0.12
    assert report["speech_truncated"] is False


def test_missing_take_fails_before_output_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "probe_duration", lambda p: 3.0)
    sheet = {"video": str(tmp_path / "source.mp4"), "voice_backend": "agent",
             "segments": [cue(tmp_path / "missing.mp3", 0, 2)]}
    out = tmp_path / "final.mp4"
    with pytest.raises(FileNotFoundError, match="agent takes missing"):
        agent.assemble(sheet, work_dir=tmp_path / "work", output=out, mix_background=False)
    assert not out.exists()
    assert not (tmp_path / "work").exists()


def test_preview_cannot_end_in_the_middle_of_a_cue(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "probe_duration", lambda p: 3.0)
    sheet = {"video": str(tmp_path / "source.mp4"), "voice_backend": "agent", "segments": [cue("x", 0, 2)]}
    with pytest.raises(ValueError, match="cue boundary"):
        agent.assemble(sheet, work_dir=tmp_path, output=tmp_path / "preview.mp4", end_time=1)


def test_source_must_not_be_overwritten(tmp_path):
    source = tmp_path / "source.mp4"
    with pytest.raises(ValueError, match="Never overwrite"):
        agent.assemble({"video": str(source), "voice_backend": "agent"}, work_dir=tmp_path, output=source)
