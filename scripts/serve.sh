#!/bin/bash
# Restore everything and start the preview server -- one command, no thinking.
#
# The sandbox is wiped between sessions. A wipe takes the virtual environment, the
# built films and the running server with it, and leaves HEAD on an old commit.
# Everything that matters is committed and pushed, so recovery is mechanical, and
# this is the mechanical part in the right order:
#
#   1. fetch and reset to what is actually on origin
#   2. start the preview server on 8080
#   3. rebuild the pieces that cannot be tracked (the two 29-minute films)
#      in the background, so the page is usable immediately either way
#
# Nothing is ever deleted: if the tree has uncommitted changes they are put in a
# stash first and the stash name is printed, so a wipe's leftovers can be found
# again (`git stash list`).
#
#     bash scripts/serve.sh            # restore, serve, rebuild the films
#     bash scripts/serve.sh --no-films # restore and serve only
set -u
cd /home/user/DUB-
BRANCH=arena/01a09fa1-dub
say() { printf '  %s\n' "$*"; }

# --- 1. what is on origin is the truth ---------------------------------------
say "[1/3] استعادة من المرفوع"
git fetch -q origin "$BRANCH" || { say "✗ تعذّر الوصول إلى GitHub"; exit 1; }
if ! git diff --quiet HEAD FETCH_HEAD 2>/dev/null; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    keep="serve.sh auto-stash $(date -u +%Y-%m-%dT%H:%MZ)"
    git stash push -u -q -m "$keep" && say "حُفظت التغييرات غير المرتكبة في: $keep"
  fi
  git reset --hard -q FETCH_HEAD && say "HEAD → $(git log --oneline -1)"
else
  say "HEAD محدَّث أصلًا: $(git log --oneline -1)"
fi

# --- 2. serve -----------------------------------------------------------------
say "[2/3] تشغيل الخدمة على المنفذ 8080"
if ss -ltn 2>/dev/null | grep -q ':8080'; then
  old=$(ss -ltnp 2>/dev/null | sed -n 's/.*:8080 .*pid=\([0-9]*\).*/\1/p' | head -1)
  [ -n "${old:-}" ] && kill "$old" 2>/dev/null && say "أُوقف المستمع القديم ($old)"
  sleep 1
fi
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
nohup "$PY" scripts/upload_server.py 8080 >/tmp/serve-preview.log 2>&1 &
sleep 2
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:8080/; then
  say "✓ الصفحة تجيب على http://127.0.0.1:8080 (إصدار $(grep -o 'BUILD = "[0-9]*"' scripts/upload_server.py | grep -o '[0-9]*'))"
else
  say "✗ الخدمة لم تُجب — راجع /tmp/serve-preview.log"; exit 1
fi

# --- 3. the untracked pieces ------------------------------------------------
if [ "${1:-}" = "--no-films" ]; then
  say "[3/3] تُخطّي بناء الفيلمين (--no-films)"
  exit 0
fi
missing=0
for f in previews/into-the-wild-narration.mp4 previews/into-the-wild-mastered.mp4; do
  [ -s "$f" ] || missing=1
done
if [ "$missing" = 0 ]; then
  say "[3/3] الفيلمان الكبيران موجودان"
else
  say "[3/3] بناء الفيلمين الكبيرين في الخلفية (~دقيقة)"
  nohup bash scripts/rebuild_deliverables.sh >/tmp/serve-films.log 2>&1 &
  say "  يتبع في /tmp/serve-films.log — الصفحة تبقى صالحة للاستعمال فورًا"
fi
