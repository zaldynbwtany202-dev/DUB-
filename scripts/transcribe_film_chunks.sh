#!/bin/bash
# Transcribe a long film in chunks, committing each one as it lands.
#
# One whisper run over thirty minutes is a sixty-minute bet against a sandbox
# that has wiped the working tree nine times. Each chunk here is about eleven
# minutes, and its JSON is committed before the next one starts, so a wipe costs
# eleven minutes instead of an hour.
set -u
cd /home/user/DUB-
W="work/into-the-wild/asr"
mkdir -p "$W"
WCLI=.cache/tools/whisper.cpp/build/bin/whisper-cli
MODEL=.cache/models/whisper-ggml/ggml-small.bin

for i in 0 1 2 3 4 5; do
  out="$W/chunk-$i"
  if [ -s "$out.json" ]; then
    echo "  [جزء $i] منجز سابقًا — تخطّي"
    continue
  fi
  echo "  [جزء $i] يبدأ $(date +%H:%M:%S)"
  "$WCLI" -m "$MODEL" -f ".cache/itw/chunks/chunk-$i.wav" -l ar \
      -bs 5 -bo 5 -ojf -dtw small -of "$out" -t 2 >/dev/null 2>&1
  if [ -s "$out.json" ]; then
    n=$(python3 -c "
import json,sys
d=json.loads(open('$out.json','rb').read().decode('utf-8','surrogateescape'))
print(sum(1 for s in d.get('transcription',[]) for t in s.get('tokens',[]) if t.get('text','').strip('[]')))
" 2>/dev/null || echo 0)
    echo "  [جزء $i] انتهى $(date +%H:%M:%S) · $n رمز"
    git add -A "$W/chunk-$i.json" 2>/dev/null
    git commit -q -m "Transcribe Into the Wild, chunk $i" 2>/dev/null
    git push -q origin arena/01a09fa1-dub 2>/dev/null && echo "  [جزء $i] ✓ محفوظ ومرفوع" || echo "  [جزء $i] محفوظ محليًا"
  else
    echo "  [جزء $i] فشل — لا مخرج"
  fi
done
echo "انتهى التفريغ كله"
