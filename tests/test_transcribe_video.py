import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.transcribe_video import main, serialize_segment, srt_stamp, transcribe_file, write_results


def segment(**overrides):
    values = dict(
        start=0.4, end=2.7, text=" أهلاً بيك ", avg_logprob=-0.12, no_speech_prob=0.01,
        words=[SimpleNamespace(word=" أهلاً", start=0.4, end=1.2, probability=0.95)],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("value,expected", [
    (0, "00:00:00,000"), (59.9996, "00:01:00,000"),
    (3599.9996, "01:00:00,000"), (108.9, "00:01:48,900"),
])
def test_srt_rounding_carries(value, expected):
    assert srt_stamp(value) == expected


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_srt_rejects_invalid_time(value):
    with pytest.raises(ValueError):
        srt_stamp(value)


def test_word_timing_and_confidence_are_retained():
    row = serialize_segment(segment())
    assert row["text"] == "أهلاً بيك"
    assert row["words"][0] == {"word": " أهلاً", "start": 0.4, "end": 1.2, "probability": 0.95}
    assert row["no_speech_prob"] == 0.01


@pytest.mark.parametrize("times", [(-1, 2), (1, 1), (3, 2), (0, float("nan"))])
def test_segment_rejects_bad_intervals(times):
    with pytest.raises(ValueError):
        serialize_segment(segment(start=times[0], end=times[1]))


def test_transcribe_uses_actual_input_and_auto_detection(tmp_path, monkeypatch):
    source = tmp_path / "real.mp4"
    source.write_bytes(b"test media fixture")
    calls = {}

    class Model:
        def __init__(self, model, **kwargs):
            calls["model"] = (model, kwargs)

        def transcribe(self, path, **kwargs):
            calls["transcribe"] = (path, kwargs)
            return iter([segment()]), SimpleNamespace(language="ar", language_probability=0.99, duration=3.0)

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    result = transcribe_file(source, model_factory=Model)
    assert calls["transcribe"][0] == str(source)
    assert calls["transcribe"][1]["language"] is None
    assert calls["transcribe"][1]["task"] == "transcribe"
    assert calls["transcribe"][1]["word_timestamps"] is True
    assert calls["model"][1]["compute_type"] == "int8"
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["review_required"] is True
    assert result["asr"]["runner"] == "local"
    paths = write_results(result, tmp_path / "asr")
    assert json.loads(paths["source.segments.json"].read_text())["segments"][0]["text"] == "أهلاً بيك"
    assert paths["source.srt"].read_text() == "1\n00:00:00,400 --> 00:00:02,700\nأهلاً بيك\n"
    assert not list((tmp_path / "asr").glob("*.tmp"))


def test_missing_media_does_not_load_model(tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("Model must not load for missing input")
    with pytest.raises(FileNotFoundError):
        transcribe_file(tmp_path / "missing.mp4", model_factory=unexpected)


def test_empty_transcription_is_failure_not_demo(tmp_path):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"fixture")

    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            return iter([]), SimpleNamespace(language="ar", language_probability=0.01, duration=3.0)

    with pytest.raises(RuntimeError, match="no speech"):
        transcribe_file(source, model_factory=Model)
    with pytest.raises(ValueError, match="No speech"):
        write_results({"segments": []}, tmp_path / "empty")
    assert not (tmp_path / "empty").exists()


def test_cli_returns_failure_for_missing_media(tmp_path, capsys):
    assert main(["--source", str(tmp_path / "missing.mp4")]) == 1
    assert "ASR FAILED (FileNotFoundError)" in capsys.readouterr().err
