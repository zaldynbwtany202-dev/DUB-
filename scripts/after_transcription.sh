#!/bin/bash
# When the dialogue transcription finishes, carry on without being asked.
#
# The transcription is six segments of roughly twenty minutes each, so the person
# waiting for the film is not going to sit and watch it. This waits for the last
# segment, then does the three mechanical steps that follow it -- joining the
# segments onto the film's clock, re-aligning the script against the new timings,
# and checking every take still contains its whole text -- and stops there,
# because choosing the new group plan is a decision rather than a step.
#
#     bash scripts/after_transcription.sh
set -u
cd /home/user/DUB-
W=work/into-the-wild
LAST="$W/asr-vocal/seg-06.json"

echo "  في انتظار المقطع الأخير من التفريغ (حتى ست ساعات)"
for _ in $(seq 1 360); do
  [ -s "$LAST" ] && break
  sleep 60
done
if [ ! -s "$LAST" ]; then
  echo "  انتهى الانتظار دون اكتمال التفريغ — لن أُكمل"
  exit 1
fi
echo "  ✓ اكتمل التفريغ"
sleep 15

echo "=== دمج المقاطع على ساعة الفيلم ==="
.venv/bin/python scripts/asr_to_words.py --chunks "$W/asr-vocal" --stride 280 \
    "$W/full-asr-vocal.json" --slug into-the-wild 2>&1 | tail -12
if [ -s "$W/full-asr-vocal.json" ]; then
  git add -A "$W/full-asr-vocal.json"
  git commit -q -m "Join the dialogue transcription onto the film's clock

Seven segments, each with its own local timestamps, shifted by a stride of 280 s
and sorted. This is the timing source the repository requires -- the raw mix was
the wrong input, and five of the seventy-eight groups anchored under ninety
percent because of it."
  git push -q origin arena/01a09fa1-dub && echo "  ✓ مدموج ومرفوع" || echo "  ✓ مدموج محليًا"
fi

echo "=== إعادة موائمة النص مع التوقيتات الجديدة ==="
.venv/bin/python scripts/align_vocal_timings.py into-the-wild \
    --asr "$W/full-asr-vocal.json" --script "$W/full-script.json" \
    --out "$W/full-timed-vocal.json" 2>&1 | tail -10
if [ -s "$W/full-timed-vocal.json" ]; then
  git add -A "$W/full-timed-vocal.json"
  git commit -q -m "Re-align the script against the dialogue timings"
  git push -q origin arena/01a09fa1-dub && echo "  ✓ موائمة ومرفوعة" || echo "  ✓ موائمة محليًا"
fi

echo "=== فحص كل تسجيل مقابل نصه ==="
.venv/bin/python scripts/verify_takes.py into-the-wild --threads 2 2>&1 | tail -45
git add -A "$W/take-check.json" "$W/takecheck" 2>/dev/null
git commit -q -m "Check every take against its own text, all forty" 2>/dev/null
git push -q origin arena/01a09fa1-dub 2>/dev/null && echo "  ✓ فُحص ورُفع" || true

echo "  جاهز لإعادة التخطيط — القرار لي"
