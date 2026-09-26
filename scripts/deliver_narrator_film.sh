#!/bin/bash
# Deliver the film with the narrator's own voice.
#
# The verdict after four synthetic rounds was that the film's own narrator is the
# voice that is wanted, and he is: a human professional reading the exact words
# this job was going to re-record. Every word of the script came from the film's
# caption track, which means the narrator has already said all of them, in the
# exact timing the picture needs.
#
# So there is nothing to synthesize and nothing to stretch. The separated dialogue
# gives twenty-nine minutes of his voice, clean of music, and its length comes out
# at 28:59.99 against the film's 29:00.00.
#
# Two versions are built because the question is not settled:
#
#   A  the narration alone, no music -- the film as a narration track, which is
#      what the separated stem actually is
#   B  the narration over the film's own music, music ducked under the voice and
#      the whole thing mastered, which is the film as intended but cleaner
#
# Neither is stretched, equalised or de-essed. A human recording that someone
# already mixed properly is not improved by a chain designed for a synthetic
# voice; it is measured and levelled, and that is all.
#
#     bash scripts/deliver_narrator_film.sh
set -u
cd /home/user/DUB-
W=work/into-the-wild
OUT=$W/deliver
mkdir -p "$OUT"
FF="$(.venv/bin/python -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')"

# 1. One continuous narration track, joined exactly the way the separator joined
#    its own segments, so it lands on the film's clock rather than near it.
NARR=/tmp/narrator-full.wav
if [ ! -s "$NARR" ]; then
  echo "  جمع صوت الراوي من 7 مقاطع"
  inputs=""
  for i in 0 1 2 3 4 5 6; do inputs="$inputs -i $W/stems/vocal-0$i.mp3"; done
  chain=""; prev="0:a"
  for i in 1 2 3 4 5 6; do
    out="[a$i]"; [ "$i" -eq 6 ] && out="[out]"
    chain="$chain[$prev][$i:a]acrossfade=d=0.5:c1=nofade:c2=nofade$out;"
    prev="a$i"
  done
  "$FF" -y -v error $inputs -filter_complex "$chain" -map "[out]" \
        -ar 44100 -ac 2 -c:a pcm_s16le "$NARR" || exit 1
fi
dur=$("$FF" -i "$NARR" 2>&1 | grep Duration | sed 's/.*Duration: //;s/,.*//')
echo "  صوت الراوي: $dur"

# 2. A: the narration alone.
if [ ! -s "$OUT/into-the-wild-narration.wav" ]; then
  echo "  [A] صوت الراوي وحده"
  .venv/bin/python scripts/master_dub.py --voice "$NARR" --plain-voice \
      --lufs -16 --out "$OUT/into-the-wild-narration.wav" 2>&1 | tail -3
fi

# 3. B: the narration over the film's music.
if [ ! -s "$OUT/into-the-wild-mastered.wav" ]; then
  echo "  [B] صوت الراوي فوق موسيقى الفيلم"
  .venv/bin/python scripts/master_dub.py --voice "$NARR" --plain-voice \
      --music "$W/stems/background-80k.mp3" --duck-db 10 --music-db -4 \
      --lufs -16 --out "$OUT/into-the-wild-mastered.wav" \
      --report "$OUT/master-report.json" 2>&1 | tail -4
fi

# 4. Mux onto the picture with the video stream copied. Re-encoding the picture
#    would cost an hour and a generation of quality for no visible gain.
for v in narration mastered; do
  f="previews/into-the-wild-$v.mp4"
  [ -s "$f" ] && continue
  echo "  mux: $f"
  "$FF" -y -v error -i library/into-the-wild/source.local.mp4 \
        -i "$OUT/into-the-wild-$v.wav" -map 0:v:0 -map 1:a:0 \
        -c:v copy -c:a aac -b:a 192k -shortest "$f" || exit 1
  ls -la "$f"
done
echo "انتهى التسليم"
