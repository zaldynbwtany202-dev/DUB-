#!/bin/bash
# Rebuild the two delivered films from what is committed.
#
# The finished films are 138 MB each, and GitHub refuses any single file over
# 100 MB, so they cannot live in the repository as themselves. What lives there
# instead is everything they are made of, which is all of it:
#
#   the picture    library/into-the-wild/parts/part-000, part-001 (131 MB, tracked)
#   the sound      work/into-the-wild/deliver/into-the-wild-{narration,mastered}.m4a
#                  (40 MB each, the exact AAC stream the films carry)
#
# Muxing those back together is not a re-encode -- the video is copied and the
# audio is copied -- so what this produces is byte-for-byte the film that was
# delivered, in about half a minute instead of the twenty it took to master.
#
#     bash scripts/rebuild_deliverables.sh
set -u
cd /home/user/DUB-
W=work/into-the-wild
FF="$(.venv/bin/python -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())' 2>/dev/null)"
[ -x "$FF" ] || { echo "  ffmpeg غير موجود — شغّل scripts/rebuild_env.sh"; exit 1; }

# The source film, reassembled from its parts and checked, if it is not there.
SRC=library/into-the-wild/source.local.mp4
if [ ! -s "$SRC" ]; then
  echo "  جمع الفيلم المصدر"
  cat library/into-the-wild/parts/part-000 library/into-the-wild/parts/part-001 > "$SRC"
fi

for v in narration mastered; do
  a="$W/deliver/into-the-wild-$v.m4a"
  out="previews/into-the-wild-$v.mp4"
  if [ ! -s "$a" ]; then
    echo "  ✗ لا صوت محفوظ: $a"
    continue
  fi
  if [ -s "$out" ] && [ "$out" -nt "$a" ]; then
    echo "  ✓ موجود: $out"
    continue
  fi
  echo "  بناء: $out"
  "$FF" -y -v error -i "$SRC" -i "$a" -map 0:v:0 -map 1:a:0 \
        -c:v copy -c:a copy -shortest "$out" || exit 1
  printf '    %s · %.1f م.بايت\n' "$out" "$(stat -c%s "$out" | awk '{print $1/1048576}')"
done

echo "  ✓ الأفلام جاهزة في previews/"
