#!/usr/bin/env bash
# End-to-end smoke test with a synthetic 6-second video.
# Verifies extract → (ducked) background → mux run without errors.
# Uses --no-separate and skips ML stages by testing media/mix directly.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Building a synthetic test video (6 s, tone + color)"
ffmpeg -y -v error \
  -f lavfi -i "sine=frequency=440:duration=6" \
  -f lavfi -i "color=c=blue:size=320x240:duration=6" \
  -pix_fmt yuv420p /tmp/dub_smoke_src.mp4

echo "==> Running media + mix stages through the package"
python - <<'PY'
import sys; sys.path.insert(0, '.')
from dub import media, mix
from dub.models import Segment

media.extract_audio('/tmp/dub_smoke_src.mp4', '/tmp/dub_smoke_audio.wav')

seg = Segment(id=0, start=1.0, end=3.0, text_src='hello', text_tgt='مرحبا')
seg.audio_path = '/tmp/dub_smoke_audio.wav'      # reuse tone as fake speech
seg.speech_dur = 1.0

out = mix.render('/tmp/dub_smoke_src.mp4', [seg], '/tmp/dub_smoke_audio.wav',
                 '/tmp/dub_smoke_out.mp4', '/tmp')
dur = media.probe_duration(out)
assert abs(dur - 6.0) < 0.2, f'unexpected duration {dur}'
print(f'OK — rendered {out} ({dur:.2f} s)')
PY

echo "==> Smoke test passed"
