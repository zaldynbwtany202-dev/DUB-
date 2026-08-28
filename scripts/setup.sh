#!/usr/bin/env bash
# One-command setup for DUB-: system ffmpeg + python package + local engines.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Installing ffmpeg"
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
elif command -v brew >/dev/null; then
    brew install ffmpeg
elif command -v dnf >/dev/null; then
    sudo dnf install -y ffmpeg
else
    echo "!! Install ffmpeg manually: https://ffmpeg.org/download.html"
fi

echo "==> Installing dub-forge with local engine (whisper + demucs + xtts + google)"
pip install -e '.[whisper,demucs,xtts,google,dev]'

echo "==> Smoke check"
dub --version
dub engines
python -m pytest tests/ -q

echo ""
echo "Ready. Try:"
echo "  dub run input.mp4 --src en --tgt ar --engine xtts --out dubbed_ar.mp4"
