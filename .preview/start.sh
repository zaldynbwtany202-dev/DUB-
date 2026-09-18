#!/usr/bin/env bash
# Bring the preview back after a sandbox reset, in one command.
#
# The sandbox is wiped between turns often enough that it has cost real work:
# a server left running is gone, .venv and .cache with it, and the checkout
# reverts to the branch base. Everything that matters is on origin, so recovery
# is a fetch and a hard reset -- but only when the tree is clean, because
# throwing away uncommitted work is the one thing this script must never do.
#
#   .preview/start.sh [port]      sync, rebuild the venv if needed, then serve
#
# The ASR engine is deliberately not built here: it downloads 487 MB and
# compiles, which competes with an upload in progress. Run
# scripts/bootstrap_local_whisper.py separately, and only when nothing is
# travelling over the preview proxy.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

BRANCH="${BRANCH:-arena/01a09fa1-dub}"
PORT="${1:-8000}"

if [ -n "$(git status --porcelain)" ]; then
    echo "تحذير: تغييرات غير ملتزمة — لن أُعيد الضبط (التزام أو تخزين أولاً)" >&2
    git status --porcelain | head -5 >&2
else
    git fetch -q origin "$BRANCH" || { echo "فشل الجلب من origin" >&2; exit 1; }
    before=$(git rev-parse --short HEAD)
    git reset --hard -q FETCH_HEAD
    echo "المزامنة: $before → $(git rev-parse --short HEAD) ($(git log -1 --format=%s | cut -c1-44))"
fi

if [ ! -x .venv/bin/python ]; then
    echo "بناء .venv …"
    bash scripts/bootstrap_das_env.sh >/dev/null 2>&1 || echo "تحذير: فشل بناء .venv" >&2
fi
[ -x .venv/bin/cmake ] || .venv/bin/pip install -q cmake 2>/dev/null

echo "الخادم على المنفذ $PORT · الرفع إلى library/<slug>/ · الدفع إلى $BRANCH"
exec python3 .preview/serve.py . "$PORT"
