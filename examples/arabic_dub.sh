#!/usr/bin/env bash
# Full example: dub an English clip into Arabic with local voice cloning.
set -euo pipefail

# 1) system dependency: ffmpeg
#    Ubuntu/Debian: sudo apt-get install -y ffmpeg
#    macOS:         brew install ffmpeg

# 2) install the tool with the local engine + local separation
pip install -e '.[whisper,demucs,xtts,google]'

# 3) dub
dub run input.mp4 \
    --src en --tgt ar \
    --engine xtts \
    --out dubbed_ar.mp4 \
    --workdir work_ar

# Cloud variant (Minimax voice cloning):
#   pip install fal-client
#   export FAL_KEY=...
#   dub run input.mp4 --src en --tgt ar --engine minimax --out dubbed_ar.mp4
