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
# The cloned-voice cut is in the same position and one step longer: it grows
# group by group, so it passes the 100 MB limit partway through and cannot be
# tracked either. Its ingredients are the takes (work/into-the-wild/takes/*.mp3,
# tracked), the scripts, and the narrator's isolated voice in stems/. Rebuilding
# it is the four-step chain below -- convert, assemble, master, mux -- about
# four minutes, not twenty.
#
#     bash scripts/rebuild_deliverables.sh            # the two 29-minute films
#     bash scripts/rebuild_deliverables.sh --clone    # also the cloned cut
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

# ── القصّة المستنسخة ───────────────────────────────────────────────────────
# تُبنى عند الطلب (--clone) أو إذا كان الفيلم مفقودًا، لأن تحويل الطابع هو
# الخطوة المكلفة (~75 ثانية لأربعين تسجيلًا) ولا معنى لإعادتها بلا سبب.
CLONE=previews/into-the-wild-cloned.mp4
if [ "${1:-}" = "--clone" ] || [ ! -s "$CLONE" ]; then
  END="$(.venv/bin/python - <<'PY'
import re, pathlib
n = [int(m.group(1)) for p in pathlib.Path("work/into-the-wild/takes").glob("g*.mp3")
     if (m := re.match(r"g(\d+)\.mp3$", p.name))]
print(max(n) + 1 if n else 0)
PY
)"
  WANT="$(.venv/bin/python - "$END" <<'PY'
import sys, pathlib
print(len(list(pathlib.Path("work/into-the-wild/takes-clone").glob("g*.wav"))) >= int(sys.argv[1]))
PY
)"
  if [ "$END" = "0" ]; then
    echo "  ✗ لا تسجيلات في work/into-the-wild/takes/"
  else
    if [ "$WANT" != "True" ] || [ ! -d "work/into-the-wild/takes-clone" ]; then
      echo "  تحويل الطابع إلى صوت الراوي: $END تسجيلًا"
      .venv/bin/python scripts/clone_takes.py --slug into-the-wild \
        --in work/into-the-wild/takes --out work/into-the-wild/takes-clone \
        --formant shifted --strength 1.0 \
        --report "work/into-the-wild/clone-report-$END.json" || exit 1
    fi
    mkdir -p work/into-the-wild/build
    echo "  تجميع المسار الصوتي ($END مجموعة)"
    .venv/bin/python scripts/assemble_dub.py work/into-the-wild \
      --out work/into-the-wild/build/clone-voice.wav \
      --start 0 --end "$END" --stretch rubberband --pack \
      --takes work/into-the-wild/takes-clone || exit 1
    SECS="$(.venv/bin/python - <<'PY'
import soundfile as sf
print(f"{sf.info('work/into-the-wild/build/clone-voice.wav').duration:.3f}")
PY
)"
    echo "  المعالجة النهائية (-16 LUFS / سقف -1.5 dBTP) · $SECS ثانية"
    .venv/bin/python scripts/master_dub.py \
      --voice work/into-the-wild/build/clone-voice.wav \
      --music work/into-the-wild/stems/background-80k.mp3 \
      --music-start 0 --music-dur "$SECS" \
      --out work/into-the-wild/build/clone-master.wav \
      --lufs -16 --duck-db 10 --music-db -4 \
      --report work/into-the-wild/clone-master-report.json || exit 1
    echo "  بناء: $CLONE"
    "$FF" -y -v error -i "$SRC" -i work/into-the-wild/build/clone-master.wav \
      -t "$SECS" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k "$CLONE" || exit 1
    printf '    %s · %s\n' "$CLONE" \
      "$("$FF" -i "$CLONE" 2>&1 | sed -n 's/.*Duration: \([0-9:.]*\).*/\1/p' | head -1)"
  fi
fi

echo "  ✓ الأفلام جاهزة في previews/"
