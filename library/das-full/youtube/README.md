# YouTube auto captions — Das recap `NB2YcTh_L6k`

Source: https://www.youtube.com/watch?v=NB2YcTh_L6k  
Channel: ملخص انمى كامل · duration 52:22 (matches `library/das-full/source.mp4`).

- `unique.txt` — unique Arabic auto transcript (watch-page order, not Whisper).
- `anchors.tsv` — YouTube caption-window start times (~30s).
- `NB2YcTh_L6k.srt` — 8-word phrase cues (window starts).
- `NB2YcTh_L6k.words.srt` — **rejected**: linear interpolate inside ~30s caption windows. Not pronunciation time.
- `NB2YcTh_L6k.spoken.srt` — **word SRT in use**: one cue per YouTube gold word; start/end = when that word is spoken on `full.wav`.
- `NB2YcTh_L6k.spoken.json` — same list with `how=spoken|replace|insert`.

Times come from whisper.cpp Small (`-ml 1 -sow`) on 45s slices of `library/das-full/full.wav`. Whisper text is discarded. Words stay `unique.txt`. Rebuild: `python3 scripts/spoken_word_srt.py` (or `--align-only` if `library/das-full/align/spoken.json` already exists).
