# YouTube auto captions — Das recap `NB2YcTh_L6k`

Source: https://www.youtube.com/watch?v=NB2YcTh_L6k  
Channel: ملخص انمى كامل · duration 52:22 (matches `library/das-full/source.mp4`).

- `unique.txt` — unique Arabic auto transcript (watch-page order, not Whisper).
- `anchors.tsv` — YouTube caption-window start times (~30s).
- `NB2YcTh_L6k.srt` — 8-word cues for dubbing.
- `NB2YcTh_L6k.words.srt` — one cue per word.
- `NB2YcTh_L6k.words.json` — word list with interpolated times.

YouTube `api/timedtext` json3/srv3/vtt returned HTTP 500 from this environment. Word times are interpolated inside each caption window, not json3 `tOffsetMs`.

Rebuild: `python3 scripts/build_das_youtube_srt.py`
