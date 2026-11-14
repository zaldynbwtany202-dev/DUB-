"""Tests for voice lookup and fail-closed Arabic synthesis provenance."""
from pathlib import Path
import json

import pytest

from youtube_auto_dub.voice import pick_voice


class TestPickVoice:
    def test_returns_string(self):
        voice = pick_voice("en", gender="female")
        assert isinstance(voice, str)
        assert len(voice) > 0

    def test_male(self):
        voice = pick_voice("en", gender="male")
        assert isinstance(voice, str)

    def test_fallback_gender(self):
        voice = pick_voice("am", gender="male")
        assert isinstance(voice, str)

    def test_unknown_language(self):
        with pytest.raises(ValueError, match="not found|not in map"):
            pick_voice("zz", gender="female")


def test_egyptian_edge_voice_receives_original_dialect_text(tmp_path, monkeypatch):
    import asyncio
    from youtube_auto_dub import arabic_text, voice as voice_mod

    monkeypatch.setattr(arabic_text, "_diacritiser", lambda: pytest.fail("MSA diacritizer must not run"))
    monkeypatch.setattr(voice_mod, "speak_studio", lambda *args, **kwargs: False)
    monkeypatch.setattr(voice_mod, "lookup_studio_entry", lambda *args, **kwargs: None)
    spoken = []

    class Communicate:
        def __init__(self, text, selected, rate):
            spoken.append((text, selected, rate))
        async def save(self, dest):
            Path(dest).write_bytes(b"sample" * 2000)

    monkeypatch.setattr(voice_mod.edge_tts, "Communicate", Communicate)
    text = "بقول لك، مش هينفع كده."
    engine = asyncio.run(voice_mod.speak_edge(
        text, "ar-EG-SalmaNeural", tmp_path / "take.mp3", lang="ar",
    ))
    assert engine == "edge-tts"
    assert spoken[0][0] == text
    assert spoken[0][1] == "ar-EG-SalmaNeural"
    manifest = json.loads((tmp_path / "take.mp3.voice.json").read_text())
    assert manifest["engine"] == "edge-tts"
    assert manifest["requested_voice_id"] == "ar-EG-SalmaNeural"
    assert manifest["actual_voice_id"] == "ar-EG-SalmaNeural"
    assert manifest["local_fallback_used"] is False
    assert manifest["audio_bytes"] == (tmp_path / "take.mp3").stat().st_size


def test_studio_take_must_match_the_pinned_voice(tmp_path, monkeypatch):
    import asyncio
    from youtube_auto_dub import voice as voice_mod

    text = "هذه أخذة استوديو معتمدة."
    calls = []
    monkeypatch.setattr(voice_mod, "lookup_studio_entry", lambda t, voice: {
        "text": t, "voice": voice, "file": "take.mp3",
    })
    def render(t, dest, voice=None):
        calls.append((t, voice))
        Path(dest).write_bytes(b"studio take")
        return True
    monkeypatch.setattr(voice_mod, "speak_studio", render)
    monkeypatch.setattr(voice_mod.edge_tts, "Communicate", lambda *args, **kwargs: pytest.fail("Edge must not run"))
    engine = asyncio.run(voice_mod.speak_edge(text, "voice-pinned", tmp_path / "take.wav", lang="en"))
    assert engine == "studio-bank"
    assert calls == [(text, "voice-pinned")]
    manifest = json.loads((tmp_path / "take.wav.voice.json").read_text())
    assert manifest["engine"] == "studio-bank"
    assert manifest["actual_voice_id"] == "voice-pinned"


def test_local_espeak_fallback_is_disabled_unless_explicit(tmp_path, monkeypatch):
    import asyncio
    from youtube_auto_dub import voice as voice_mod
    from youtube_auto_dub import local_tts

    monkeypatch.setattr(voice_mod, "speak_studio", lambda *args, **kwargs: False)
    monkeypatch.setattr(voice_mod, "lookup_studio_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(voice_mod.edge_tts, "Communicate", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(local_tts, "speak_local", lambda *args, **kwargs: pytest.fail("fallback must be off"))
    with pytest.raises(RuntimeError, match="fallback is disabled"):
        asyncio.run(voice_mod.speak_edge(
            "اختبار", "ar-SA-HamedNeural", tmp_path / "failed.mp3",
            lang="ar", retries=0,
        ))
    assert not (tmp_path / "failed.mp3").exists()


def test_explicit_local_fallback_is_marked_as_a_different_engine(tmp_path, monkeypatch):
    import asyncio
    from youtube_auto_dub import voice as voice_mod
    from youtube_auto_dub import local_tts

    monkeypatch.setattr(voice_mod, "speak_studio", lambda *args, **kwargs: False)
    monkeypatch.setattr(voice_mod, "lookup_studio_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(voice_mod.edge_tts, "Communicate", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(local_tts, "speak_local", lambda text, dest, **kwargs: Path(dest).write_bytes(b"local espeak"))
    engine = asyncio.run(voice_mod.speak_edge(
        "اختبار", "ar-SA-HamedNeural", tmp_path / "fallback.wav",
        lang="ar", retries=0, allow_local_fallback=True,
    ))
    assert engine == "espeak-local"
    manifest = json.loads((tmp_path / "fallback.wav.voice.json").read_text())
    assert manifest["engine"] == "espeak-local"
    assert manifest["actual_voice_id"] == "espeak:ar:male"
    assert manifest["local_fallback_used"] is True
