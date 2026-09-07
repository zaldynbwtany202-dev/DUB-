from pathlib import Path

from youtube_auto_dub.agent_tts import cues_from_segments, write_cues, load_cues


def test_cues_from_segments_skips_empty(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    sheet = cues_from_segments(
        video,
        [
            {"start": 0.2, "end": 2.0, "text": "مرحبا"},
            {"start": 2.0, "end": 2.1, "text": "   "},
            {"start": 3.0, "end": 2.0, "text": "bad"},
        ],
        voice_id="voice-00",
        audio_dir=tmp_path / "takes",
    )
    assert sheet["voice_backend"] == "agent"
    assert sheet["voice_id"] == "voice-00"
    assert len(sheet["segments"]) == 1
    assert sheet["segments"][0]["audio"].endswith("seg-0000.mp3")
    path = write_cues(tmp_path / "cues.json", sheet)
    loaded = load_cues(path)
    assert loaded["segments"][0]["text"] == "مرحبا"
