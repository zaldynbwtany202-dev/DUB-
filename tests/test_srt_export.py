from youtube_auto_dub.srt_export import cues_to_entries, render_srt, split_phrases, srt_stamp


def test_stamp_and_phrase_split():
    assert srt_stamp(0.9) == "00:00:00,900"
    assert srt_stamp(32.25) == "00:00:32,250"
    parts = split_phrases("فضل يضحك ويبتسم. بعد سنين طويلة، أخيرا لقى مية.")
    assert parts[0] == "فضل يضحك ويبتسم."
    assert "بعد سنين طويلة،" in parts[1]


def test_entries_follow_islands_not_padded_windows():
    cues = {
        "segments": [{"index": 0, "start": 0.9, "end": 9.86, "text": "فضل يضحك ويبتسم. بعد سنين طويلة."}],
        "conversions": [{"index": 0, "placements": [{"start": 0.0, "end": 1.81}, {"start": 1.81, "end": 4.61}]}],
    }
    entries = cues_to_entries(cues)
    assert len(entries) == 2
    assert entries[0]["start"] == 0.9
    assert entries[0]["end"] == 2.71
    assert entries[1]["start"] == 2.71
    body = render_srt(entries)
    assert "00:00:00,900 --> 00:00:02,710" in body
    assert "فضل يضحك ويبتسم." in body
