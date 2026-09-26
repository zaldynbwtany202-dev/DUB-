#!/bin/bash
# Answer one question: is everything that matters saved in the repository?
#
# The sandbox has been wiped thirteen times, and each time the same conversation
# happened -- what is gone, and was any of it work? The answer is knowable
# without asking, so this classifies every file in the workspace into three kinds
# and complains about anything that fits none of them:
#
#   محفوظ      tracked by git, and the remote has it. Nothing to do.
#   يُعاد بناؤه  toolchain: the model, the built binary, the virtual environment,
#              the film reassembled from its parts. A script rebuilds each one,
#              verified by checksum, so a wipe costs minutes rather than work.
#   خطر        work with no copy anywhere. The only category worth reacting to,
#              and the script exits non-zero when it is not empty.
#
#     bash scripts/audit_saved.sh
set -u
cd /home/user/DUB-
FAIL=0
BRANCH=arena/01a09fa1-dub

say() { printf '  %s\n' "$*"; }
size_of() { du -sh "$1" 2>/dev/null | cut -f1; }

echo "=== ١. الشجرة نظيفة ==="
dirty=$(git status --porcelain | wc -l)
if [ "$dirty" -gt 0 ]; then
  say "تحذير: $dirty ملفًا متغيّرًا أو غير متعقَّب"
  git status --porcelain | head -10 | sed 's/^/    /'
  FAIL=1
else
  say "لا شيء معلَّق"
fi

echo "=== ٢. مدفوع إلى GitHub ==="
# The refspec names the destination explicitly: a plain 'git fetch origin BRANCH'
# writes only FETCH_HEAD, so the comparison below would look for a remote ref
# that was never created and call a fully pushed repository unsaved.
git fetch -q origin "$BRANCH:refs/remotes/origin/$BRANCH" 2>/dev/null
local_head=$(git rev-parse HEAD 2>/dev/null)
remote_head=$(git rev-parse "refs/remotes/origin/$BRANCH" 2>/dev/null || echo "")
if [ -z "$remote_head" ]; then
  say "تحذير: لا مرجع للفرع على GitHub — تعذّر التحقق"
  FAIL=1
elif [ "$local_head" = "$remote_head" ]; then
  say "مرفوع بالكامل · ${local_head:0:7}"
else
  n=$(git rev-list --count "$remote_head..$local_head" 2>/dev/null || echo "?")
  say "تحذير: $n التزامًا محليًا غير مرفوع"
  git log --oneline "$remote_head..$local_head" | head -5 | sed 's/^/    /'
  FAIL=1
fi

echo "=== ٣. ما يتجاهله git: هل كل صنف يُعاد بناؤه بسكربت؟ ==="
known=".venv/ .cache/ library/into-the-wild/source.local.mp4 scripts/__pycache__/ "
known="$known previews/into-the-wild-narration.mp4 previews/into-the-wild-mastered.mp4 "
known="$known previews/into-the-wild-cloned.mp4 "
known="$known work/into-the-wild/takes-clone/ work/into-the-wild/build/"
for entry in $(git status --porcelain --ignored 2>/dev/null | awk '$1=="!!" {print $2}'); do
  case "$known" in
    *"$entry "*) ;;
    *"$entry"*) ;;
    *) say "**غير مصنَّف**: $entry ($(size_of "$entry")) — احفظه أو أضفه إلى .gitignore"
       FAIL=1 ;;
  esac
done
[ -d .venv ] && say "$(size_of .venv)  .venv/ — يُعاد بناؤه: bash scripts/rebuild_env.sh"
[ -d .cache/models ] && say "$(size_of .cache/models)  .cache/models/ — يُعاد بناؤه: نموذج مثبَّت بالبصمة في config/whisper-small-ggml.json"
[ -d .cache/tools ] && say "$(size_of .cache/tools)  .cache/tools/ — يُسترجَع من tools/whisper-1.7.6 بلا تصريف"
# The films are too big to track, so each one has to name the script that
# rebuilds it -- an unclassified ignored file is the thing this audit exists to
# catch, and a 139 MB film with no recipe would be exactly that.
for f in previews/into-the-wild-narration.mp4 previews/into-the-wild-mastered.mp4; do
  [ -f "$f" ] && say "$(size_of "$f")  $f — يُعاد بناؤه: bash scripts/rebuild_deliverables.sh"
done
[ -f previews/into-the-wild-cloned.mp4 ] && \
  say "$(size_of previews/into-the-wild-cloned.mp4)  previews/into-the-wild-cloned.mp4 — يُعاد بناؤه: bash scripts/rebuild_deliverables.sh --clone (4 د 30 ث)"
[ -d work/into-the-wild/takes-clone ] && \
  say "$(size_of work/into-the-wild/takes-clone)  takes-clone/ — يُعاد بناؤه: scripts/clone_takes.py من takes/ وstems/ (75 ثانية)"
[ -d work/into-the-wild/build ] && \
  say "$(size_of work/into-the-wild/build)  build/ — سكراتش: المسار الصوتي والمزيج قبل mux"
for sub in .cache/itw .cache/separation .cache/takecheck; do
  [ -e "$sub" ] && say "$(size_of "$sub")  $sub/ — سكراتش"
done
[ -f library/into-the-wild/source.local.mp4 ] && \
  say "$(size_of library/into-the-wild/source.local.mp4)  source.local.mp4 — يُعاد بناؤه: جمع parts/ والتحقق من SOURCE.sha256"

echo "=== ٤. مخرجات عمل في السكراتش؟ ==="
# Anything text-shaped in .cache that is not a model, a tool, or a separator's
# scratch masks is work. The take checks used to live there and a wipe took them;
# they are written into work/ now, and this is what notices if one drifts back.
stray=$(find .cache -type f \( -name '*.json' -o -name '*.srt' -o -name '*.txt' \) 2>/dev/null \
        | grep -vE '/models/|/tools/|/work-[0-9]|manifest' | head -12)
if [ -n "$stray" ]; then
  say "ملفات نصية في .cache (مكانها work/):"
  printf '%s\n' "$stray" | sed 's/^/    /'
  FAIL=1
else
  say "لا شيء — مخرجات العمل في work/"
fi

echo "=== ٥. أكبر عشرين ملفًا متعقَّبًا ==="
git ls-files -z | xargs -0 -r du -h 2>/dev/null | sort -rh | head -20 | sed 's/^/  /'

echo
if [ "$FAIL" = 0 ]; then
  say "✓ كل شيء إما محفوظ أو يُعاد بناؤه بسكربت — لا شيء في خطر"
else
  say "✗ يوجد ما يحتاج حفظًا — راجع البنود أعلاه"
fi
exit "$FAIL"
