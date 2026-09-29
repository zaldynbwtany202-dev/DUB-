#!/bin/bash
# استعادة كاملة بعد مسح البيئة (يتكرّر هنا كثيرًا).
# كل ما يهم محفوظ في الريموت؛ هذا السكربت يعيد الفرع ثم يبني الأدوات ثم يشغّل الصفحة.
#
#   bash scripts/restore_after_wipe.sh [المنفذ]
set -u
cd /home/user/DUB-
BR=arena/01a09fa1-dub
say() { printf '  %s\n' "$*"; }

if [ -n "$(git status --porcelain)" ]; then
  say "[تحفّظ] عمل غير محفوظ → stash"
  git stash push -u -m "استعادة تلقائية قبل المسح $(date +%F_%H:%M)" >/dev/null
fi
say "[فرع] جلب $BR"
git fetch origin "$BR" >/dev/null 2>&1
git reset --hard FETCH_HEAD >/dev/null
say "[فرع] الآن عند $(git log --oneline -1)"

[ -x .venv/bin/python ] || bash scripts/rebuild_env.sh >/dev/null 2>&1
say "[بيئة] .venv جاهز"
