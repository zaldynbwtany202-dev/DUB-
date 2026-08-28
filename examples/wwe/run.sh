#!/usr/bin/env bash
# Reproduce the professional Arabic dub of the WWE sample
# (Brock Lesnar vs R-Truth segment, en → ar).
#
# The pipeline will:
#   1. extract + separate the audio (demucs)
#   2. transcribe with word timestamps (faster-whisper)
#   3. cut one clean voice reference per speaker automatically
#   4. use the human translations below (no MT)
#   5. clone both voices and synthesize each line (engine of your choice)
#   6. schedule, mix with the original crowd, and mux
set -euo pipefail
cd "$(dirname "$0")/../.."

VIDEO="${1:-examples/wwe/source.mp4}"   # pass the source clip as $1

# --- Cloud engine (closest to the reference dub): Minimax ---
# export FAL_KEY=...
dub run "$VIDEO" \
    --src en --tgt ar \
    --engine minimax \
    --translate-backend file \
    --translations-file examples/wwe/translations_ar.json \
    --speaker-map "0:S1,1:S2,2:S1,3:S2,4:S1,5:S2,6:S1,7:S2" \
    --out dubbed_wwe_ar.mp4

# --- Free local alternative (no keys): XTTS ---
# COQUI_TOS_AGREED=1 dub run "$VIDEO" --src en --tgt ar --engine xtts \
#     --translate-backend file \
#     --translations-file examples/wwe/translations_ar.json \
#     --speaker-map "0:S1,1:S2,2:S1,3:S2,4:S1,5:S2,6:S1,7:S2" \
#     --out dubbed_wwe_ar.mp4
