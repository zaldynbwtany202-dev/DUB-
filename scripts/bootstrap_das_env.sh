#!/usr/bin/env bash
# Restore the working environment for the Das dub. Idempotent, safe to re-run.
#
# `.venv/` and `.cache/` are excluded from the workspace snapshot, so both are
# gone at the start of every session turn. This rebuilds them:
#   1. a venv with ffmpeg (bundled static binary), numpy and soundfile
#   2. the 151 MB source video, fetched as nine SHA-verified parts into .cache/
#
#   bash scripts/bootstrap_das_env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet imageio-ffmpeg numpy soundfile pyyaml
.venv/bin/python - <<'PY'
import imageio_ffmpeg, numpy, soundfile, subprocess
print("ffmpeg :", imageio_ffmpeg.get_ffmpeg_exe())
print("numpy  :", numpy.__version__)
PY

if [ "${1:-}" = "--no-source" ]; then
  echo "source video: skipped (--no-source)"
else
  .venv/bin/python scripts/restore_das_source.py
fi
