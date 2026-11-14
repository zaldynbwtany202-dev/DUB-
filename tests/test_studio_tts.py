"""The studio voice bank must use pronunciation-safe, voice-scoped lookup."""
from pathlib import Path

from youtube_auto_dub import studio_tts


def test_v2_identity_preserves_harakat_letters_and_punctuation():
    assert studio_tts.normalize("عَلَم") != studio_tts.normalize("عِلْم")
    assert studio_tts.key_for("عَلَم") != studio_tts.key_for("عِلْم")
    assert studio_tts.normalize("مدرسة") != studio_tts.normalize("مدرسه")
    assert studio_tts.normalize("هذا، صحيح") != studio_tts.normalize("هذا صحيح")
    assert studio_tts.key_for("مرحباً   بكم") == studio_tts.key_for("مرحباً بكم")


def test_key_is_versioned_and_voice_scoped():
    assert studio_tts.key_for("مرحبا", "voice-a").startswith("v2-")
    assert studio_tts.key_for("مرحبا", "voice-a") != studio_tts.key_for("مرحبا", "voice-b")
    assert len(studio_tts.key_for("ثم نترجم المعنى إلى العربية.")) == 67


def test_bundled_demo_lines_are_covered_without_replacing_assets():
    """Existing v1 assets remain available for their exact stored text."""
    lines = [
        "مرحباً بكم في برو ستوديو.",
        "يعرض هذا الفيلم القصير الدبلجة الآلية للفيديو.",
        "أولاً نُفرّغ الكلام.",
        "ثم نترجم المعنى إلى العربية.",
        "وأخيراً نولّد صوتاً جديداً ونزامنه مع الصورة.",
    ]
    have, total = studio_tts.coverage(lines)
    assert have == total == 5


def test_unknown_line_has_no_take(tmp_path):
    assert studio_tts.lookup("جملة غير مسجلة إطلاقاً في البنك") is None
    assert studio_tts.speak_studio(
        "جملة غير مسجلة إطلاقاً في البنك", tmp_path / "out.wav"
    ) is False


def test_missing_file_is_not_returned(monkeypatch):
    monkeypatch.setattr(
        studio_tts,
        "load_index",
        lambda: {studio_tts.key_for("مفقود"): {"text": "مفقود", "file": "nope.mp3"}},
    )
    assert studio_tts.lookup("مفقود") is None


def test_legacy_lossy_hash_is_only_a_hint_not_a_match(monkeypatch, tmp_path):
    intended = "عَلَم"
    homograph = "عِلْم"
    assert studio_tts._legacy_key_for(intended) == studio_tts._legacy_key_for(homograph)
    take = tmp_path / "old-approved.mp3"
    take.write_bytes(b"old take")
    monkeypatch.setattr(studio_tts, "VOICE_DIR", tmp_path)
    monkeypatch.setattr(studio_tts, "load_index", lambda: {
        studio_tts._legacy_key_for(intended): {
            "text": intended, "file": take.name, "voice": "voice-a",
        }
    })
    assert studio_tts.lookup(intended) == take
    assert studio_tts.lookup(homograph) is None
    assert studio_tts.lookup(intended, voice="voice-b") is None
    assert studio_tts.lookup(intended, voice="voice-a") == take


def test_v2_registration_does_not_overwrite_legacy_or_cross_voice_entries(monkeypatch, tmp_path):
    index_path = tmp_path / "index.json"
    monkeypatch.setattr(studio_tts, "VOICE_DIR", tmp_path)
    monkeypatch.setattr(studio_tts, "INDEX_PATH", index_path)
    legacy = {studio_tts._legacy_key_for("عَلَم"): {
        "text": "عَلَم", "file": "legacy.mp3", "voice": "voice-a",
    }}
    (tmp_path / "legacy.mp3").write_bytes(b"legacy")
    studio_tts.save_index(legacy)
    take_a = tmp_path / "a.mp3"
    take_b = tmp_path / "b.mp3"
    take_a.write_bytes(b"voice a")
    take_b.write_bytes(b"voice b")
    key_a = studio_tts.register("عِلْم", take_a, voice="voice-a")
    key_b = studio_tts.register("عِلْم", take_b, voice="voice-b")
    assert key_a != key_b
    saved = studio_tts.load_index()
    assert studio_tts._legacy_key_for("عَلَم") in saved
    assert studio_tts.lookup("عَلَم", voice="voice-a") == tmp_path / "legacy.mp3"
    assert studio_tts.lookup("عِلْم", voice="voice-a") == take_a
    assert studio_tts.lookup("عِلْم", voice="voice-b") == take_b
    assert studio_tts.lookup("عِلْم", voice="voice-c") is None


def test_index_path_cannot_escape_voice_directory(monkeypatch, tmp_path):
    outside = tmp_path.parent / "outside.mp3"
    outside.write_bytes(b"do not load")
    monkeypatch.setattr(studio_tts, "VOICE_DIR", tmp_path)
    monkeypatch.setattr(studio_tts, "load_index", lambda: {
        studio_tts._legacy_key_for("خطر"): {
            "text": "خطر", "file": "../outside.mp3", "voice": "voice-a",
        }
    })
    assert studio_tts.lookup("خطر") is None


def test_speak_studio_renders_known_legacy_line(tmp_path):
    dest = tmp_path / "line.wav"
    assert studio_tts.speak_studio("مرحباً بكم في برو ستوديو.", dest) is True
    assert dest.exists() and dest.stat().st_size > 1000
