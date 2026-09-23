#!/bin/bash
# Transcribe the separated dialogue stems of Into the Wild, committing as it goes.
#
# The first pass transcribed the raw mix and it was the wrong input: this film
# runs a music bed under the whole runtime, and whisper loses words where the
# music wins. Five of the seventy-eight groups came back under ninety percent
# anchored because of it, one of them at forty-seven.
#
# The separated dialogue exists now, so the timing source is what the repository
# always said it should be. Each segment is its own whisper run with its own
# local clock -- no join between them, so no join artefact -- and is shifted onto
# the film's clock with a stride of 280 s.
set -u
cd /home/user/DUB-
W="work/into-the-wild/asr-vocal"
mkdir -p "$W"
WCLI=.cache/tools/whisper.cpp/build/bin/whisper-cli
MODEL=.cache/models/whisper-ggml/ggml-small.bin

for i in 0 1 2 3 4 5 6; do
  out="$W/seg-$(printf '%02d' "$i")"
  src="work/into-the-wild/stems/vocal-$(printf '%02d' "$i").mp3"
  if [ -s "$out.json" ]; then
    echo "  [مقطع $i] منجز سابقًا — تخطّي"
    continue
  fi
  if [ ! -s "$src" ]; then
    echo "  [مقطع $i] لا ملف صوت — تخطّي"
    continue
  fi
  echo "  [مقطع $i] يبدأ $(date +%H:%M:%S)"
  "$WCLI" -m "$MODEL" -f "$src" -l ar -bs 5 -bo 5 -ojf -dtw small \
      -of "$out" -t 2 >/dev/null 2>&1
  if [ -s "$out.json" ]; then
    n=$(python3 -c "
import json
d=json.loads(open('$out.json','rb').read().decode('utf-8','surrogateescape'))
print(sum(1 for s in d.get('transcription',[]) for t in s.get('tokens',[])
          if t.get('text','').strip('[]') and not t['text'].startswith('[')))
" 2>/dev/null || echo 0)
    echo "  [مقطع $i] انتهى $(date +%H:%M:%S) · $n رمز"
    git add -A "$W/seg-$(printf '%02d' "$i").json" 2>/dev/null
    git commit -q -m "Transcribe Into the Wild dialogue stem, segment $i" 2>/dev/null
    git push -q origin arena/01a09fa1-dub 2>/dev/null \
      && echo "  [مقطع $i] ✓ محفوظ ومرفوع" || echo "  [مقطع $i] محفوظ محليًا"
  else
    echo "  [مقطع $i] فشل — لا مخرج"
  fi
done
echo "انتهى تفريغ الصوت المعزول"
