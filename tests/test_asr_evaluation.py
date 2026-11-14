import pytest

from youtube_auto_dub.asr_evaluation import evaluate_asr_records


def row(ident, dialect, reference, hypothesis, *, verified=True, hash="a" * 64, reviewer="listener"):
    return {
        "recording_id": ident,
        "dialect": dialect,
        "audio_sha256": hash,
        "reference": reference,
        "reference_verified": verified,
        "reference_reviewer": reviewer,
        "hypothesis": hypothesis,
    }


def test_asr_metrics_are_aggregated_by_dialect_and_explicitly_not_pronunciation():
    report = evaluate_asr_records([
        row("eg-1", "ar-EG", "هذا كتاب جديد", "هذا كتاب جديد"),
        row("eg-2", "ar-EG", "هذا كتاب جديد", "هذا كتاب قديم"),
        row("dz-1", "ar-DZ", "طَعَامِهِم", "طعامهم"),
    ])
    assert report["overall"]["recordings"] == 3
    assert report["overall"]["word_errors"] == 1
    assert report["by_dialect"]["ar-EG"]["recordings"] == 2
    assert report["by_dialect"]["ar-EG"]["wer"] == pytest.approx(1 / 6, abs=1e-6)
    assert report["by_dialect"]["ar-DZ"]["wer"] == 0
    assert report["pronunciation_verified"] is False
    assert report["quality_gate"] is False


def test_text_metrics_cannot_distinguish_arabic_homograph_vowels():
    report = evaluate_asr_records([
        row("homograph", "ar", "عَلَم كبير", "عِلْم كبير"),
    ])
    assert report["overall"]["wer"] == 0
    assert report["overall"]["cer"] == 0
    assert report["pronunciation_verified"] is False


@pytest.mark.parametrize("bad", [
    row("a", "ar-EG", "مرحباً", "مرحباً", verified=False),
    row("a", "ar-EG", "مرحباً", "مرحباً", hash="not-a-sha"),
    {"recording_id": "a", "dialect": "ar-EG", "reference": "مرحباً", "hypothesis": "مرحباً"},
])
def test_evaluation_requires_verified_reference_and_exact_audio_identity(bad):
    with pytest.raises(ValueError):
        evaluate_asr_records([bad])


def test_empty_reference_set_is_not_reported_as_zero_error():
    with pytest.raises(ValueError, match="at least one"):
        evaluate_asr_records([])
