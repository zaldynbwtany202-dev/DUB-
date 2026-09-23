#!/bin/bash
# The two long jobs, in order, each checkpointed so a wipe costs one step.
#
# Both were running when the sandbox was wiped, and both lost their output,
# because both wrote into .cache. This time every completed step lands in the
# repository and gets pushed.
#
#   1. Separate the music bed for the whole film (~40 min). Segments are
#      resumable: a segment whose output already exists is skipped, so a wipe
#      costs only the segment that was in flight.
#   2. Encode the bed and commit it. The wav is 307 MB and stays local, but a
#      160k mp3 is 35 MB and is enough to rebuild it, which is what makes the
#      forty minutes survivable.
#   3. Transcribe the separated dialogue, segment by segment (~2 h), committing
#      after each one. This is the timing source the repository requires, and the
#      raw mix was the wrong input: five of the seventy-eight groups anchored
#      under ninety percent because of it.
#
#     bash scripts/run_long_jobs.sh
set -u
cd /home/user/DUB-

KEY=.cache/itw/music-full.wav
SEGS=.cache/separation/itw/segments
BED=work/into-the-wild/stems/background.mp3

if [ ! -s "$KEY" ]; then
  echo "=== [1/3] فصل الموسيقى عن الفيلم كاملاً ==="
  .venv/bin/python scripts/separate_background_segmented.py \
      --source library/into-the-wild/source.local.mp4 \
      --out "$KEY" --segment 280 --work .cache/separation/itw || exit 1
else
  echo "=== [1/3] الموسيقى مفصولة سابقًا ==="
fi

if [ ! -s "$BED" ]; then
  echo "=== [2/3] وسم الموسيقى mp3 وحفظها في المستودع ==="
  FF=$(.venv/bin/python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())")
  "$FF" -y -v error -i "$KEY" -c:a libmp3lame -b:a 160k "$BED" || exit 1
  ls -la "$BED"
  git add -A "$BED"
  git commit -q -m "Keep the separated music bed, so a wipe costs no CPU

The full run -- forty minutes of separation over the whole film, and the bed it
produced -- lives in .cache, which is scratch, which is how the last wipe took
it. Everything except segment zero's mp3 went with it.

So the bed is committed as a 160k mp3: thirty-five megabytes instead of three
hundred, and enough to rebuild the wav the mixer wants. It is a second generation
under a source that was already 127k AAC, and it sits under speech at a few dB of
gain, so it is the right trade for not having to spend the forty minutes again.
The wav stays in .cache because forty minutes is recoverable and 307 MB in git is
not free." && git push origin arena/01a09fa1-dub
fi

echo "=== [3/3] تفريغ الصوت المعزول ==="
bash scripts/transcribe_dialogue_stems.sh
echo "انتهت السلسلة"
