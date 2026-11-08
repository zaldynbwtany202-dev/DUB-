"""Tests for voice lookup functions (no network calls)."""

from pathlib import Path

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
    monkeypatch.setattr(voice_mod, "speak_studio", lambda *args: False)
    spoken = []

    class Communicate:
        def __init__(self, text, selected, rate):
            spoken.append((text, selected, rate))
        async def save(self, dest):
            Path(dest).write_bytes(b"sample" * 2000)

    monkeypatch.setattr(voice_mod.edge_tts, "Communicate", Communicate)
    text = "بقول لك، مش هينفع كده."
    asyncio.run(voice_mod.speak_edge(text, "ar-EG-SalmaNeural", tmp_path / "take.mp3", lang="ar"))
    assert spoken[0][0] == text
    assert spoken[0][1] == "ar-EG-SalmaNeural"
