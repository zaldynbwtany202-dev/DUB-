#!/usr/bin/env bash
# أمر واحد بين الجولة والمستودع: أدلّة + تعقّب + تقسيم + كوميت.
#
# وُلد من عيبين متكرّرين: (1) أدلّة البناء في build/ تُفقد مع كل مسحة،
# (2) نواتج (أخذات/معاينات) تبقى بلا تعقّب فتُفقد نهائيًا (حصل مع 0→25 وبديلَي g026).
#
#   scripts/safe_commit.sh "رسالة الكوميت"              # يجهّز ويلتزم (بلا دفع)
#   PUSH=1 scripts/safe_commit.sh "رسالة"               # ويضيف الدفع أيضًا
#   SPLIT_MB=80 scripts/safe_commit.sh "رسالة"          # حدّ التقسيم (افتراضيًا 80)
set -euo pipefail
cd "$(dirname "$0")/.."

MSG="${1:?الاستعمال: scripts/safe_commit.sh \"رسالة الكوميت\"}"
SPLIT_MB="${SPLIT_MB:-80}"

echo "== 1) نسخ أدلّة البناء إلى qa/ (build/ مستثنى من git) =="
for d in work/*/; do
  [ -d "$d/build" ] || continue
  python3 scripts/archive_build_evidence.py "$d" || true
done

echo "== 2) تقسيم كل معاينة أكبر من ${SPLIT_MB} م.ب (بصمة SHA-256) =="
for f in previews/*.mp4; do
  [ -e "$f" ] || continue
  mb=$(( $(stat -c %s "$f") / 1048576 ))
  if [ "$mb" -gt "$SPLIT_MB" ] && [ ! -d "$f.parts" ]; then
    echo "  تقسيم $f ($mb م.ب)"
    python3 scripts/split_preview.py "$f" || true
  fi
done

echo "== 3) تعقّب النواتج المهمة (تأخذات · معاينات · أدلّة · نصوص) =="
git add -A
git add -f \
  work/*/takes/*/*.mp3 work/*/takes-*/*/*.mp3 work/*/takes/*/*.json \
  work/*/qa/*.json work/*/qa/asr-takes/*.json work/*/qa/*/*.json \
  work/*/*.json work/*/NEXT.md 2>/dev/null || true
git add -f previews/*.mp3 2>/dev/null || true
# المعاينات الصغيرة تُعقَّب كاملة؛ الكبيرة تُعقَّب أجزاؤها (وهي مُضافة بـgit add -A)
for f in previews/*.mp4; do
  [ -e "$f" ] || continue
  mb=$(( $(stat -c %s "$f") / 1048576 ))
  if [ "$mb" -le "$SPLIT_MB" ]; then git add -f "$f" 2>/dev/null || true; fi
done

echo "== 3ب) تحقّق: لا ملف مهم بلا تعقّب =="
missing=0
for f in work/*/takes/*/*.mp3 work/*/groups.json work/*/qa/verify-*.json; do
  [ -e "$f" ] || continue
  git ls-files --error-unmatch "$f" >/dev/null 2>&1 || { echo "  ✗ غير متعقَّب: $f"; missing=1; }
done
[ "$missing" -eq 0 ] && echo "  ✓ كل الأخذات والنصوص والأدلة متعقَّبة"

echo "== 4) كوميت =="
if git diff --cached --quiet; then
  echo "  لا تغييرات مجهّزة"
else
  git commit -q -m "$MSG"
  git log --oneline -1
fi

if [ "${PUSH:-0}" = "1" ]; then
  echo "== 5) دفع =="
  git push -q origin arena/01a09fa1-dub && echo "  ✓ تمّ الدفع"
fi
