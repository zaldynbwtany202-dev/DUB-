#!/usr/bin/env bash
# أرشفة أدلة البناء وتجزئة المعاينات الكبيرة وتعقّب الأصول قبل الحفظ.
# لا يُغيَّر الفرع؛ PUSH=1 يدفع إلى فرع الجلسة فقط.
# الاستعمال: scripts/safe_commit.sh "رسالة"؛ DRY_RUN=1 للتحقق والتجهيز بلا commit.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob
MSG="${1:?أدخل رسالة الكوميت}"
SPLIT_MB="${SPLIT_MB:-80}"
[[ "$SPLIT_MB" =~ ^[1-9][0-9]*$ ]] || { echo "✗ SPLIT_MB يجب أن يكون عددًا موجبًا" >&2; exit 2; }
[[ "$(git branch --show-current)" == 'arena/01a09fa1-dub' ]] || {
  echo '✗ ليس فرع الجلسة؛ لن أعدّل المستودع' >&2; exit 2;
}

for d in work/*/; do
  [[ ! -d "$d/build" ]] || python3 scripts/archive_build_evidence.py "$d"
done

# git add -A يُعيد إدخال الملف المتعقَّب ولو كان له أجزاء؛ نزيله من الفهرس بعده.
git add -A
for f in previews/*.mp4; do
  size=$(stat -c %s -- "$f")
  if (( size > SPLIT_MB * 1048576 )); then
    # --check يفحص بصمة الملف المصدر وبصمة الأجزاء؛ لا يكفي وجود المجلد.
    if ! python3 scripts/split_preview.py "$f" --check >/dev/null 2>&1; then
      python3 scripts/split_preview.py "$f" --part-mb 48
    fi
    python3 scripts/split_preview.py "$f" --check
    git add -f -- "$f.parts/manifest.json" "$f.parts/"*.part*
    git rm --cached -q --ignore-unmatch -- "$f"  # لا يحذف الملف الذي يشغّله الخادم
    echo "✓ الأجزاء محفوظة والملف الكامل خارج الفهرس: $f"
  else
    git add -f -- "$f"
  fi
done

# مهم: الأَخْذَة الجديدة موضوعة في takes-voice25/g*.mp3 وهي مُتجاهلة بـ*.mp3.
# الاستدعاء السابق لمسار takes/*/*.mp3 لم يُضفها وكان يتجاهل فشل git add.
takes=(work/*/takes/g*.mp3 work/*/takes-*/g*.mp3
       work/*/takes-*/candidates/*.mp3 work/*/takes-superseded-*/*.mp3)
(( ${#takes[@]} == 0 )) || git add -f -- "${takes[@]}"
qa=(work/*/qa/*.json work/*/qa/asr-takes/*.json work/*/qa/*/*.json)
(( ${#qa[@]} == 0 )) || git add -f -- "${qa[@]}"
notes=(work/*/groups.json work/*/NEXT.md work/*/full-timed*.json)
(( ${#notes[@]} == 0 )) || git add -f -- "${notes[@]}"
mp3=(previews/*.mp3)
(( ${#mp3[@]} == 0 )) || git add -f -- "${mp3[@]}"

# تحقق من الفهرس، لا من شجرة العمل: الملف غير المحفوظ ممنوع قبل commit.
missing=0
for f in "${takes[@]}" "${qa[@]}" "${notes[@]}"; do
  if ! git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    echo "✗ ناتج ليس في الفهرس: $f" >&2; missing=1
  fi
done
(( missing == 0 )) || exit 3
for f in previews/*.mp4; do
  size=$(stat -c %s -- "$f")
  if (( size > SPLIT_MB * 1048576 )); then
    if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      echo "✗ الملف الكامل الكبير ما زال في الفهرس: $f" >&2; exit 3
    fi
    git ls-files --error-unmatch -- "$f.parts/manifest.json" >/dev/null || exit 3
  else
    git ls-files --error-unmatch -- "$f" >/dev/null || exit 3
  fi
done
if [[ "${DRY_RUN:-0}" == 1 ]]; then
  echo '✓ تحقق وتجهيز فقط (بلا commit)'; exit 0
fi
if ! git diff --cached --quiet; then
  git commit -m "$MSG"
else
  echo 'لا تغييرات جاهزة'
fi
if [[ "${PUSH:-0}" == 1 ]]; then
  git push origin arena/01a09fa1-dub
fi
