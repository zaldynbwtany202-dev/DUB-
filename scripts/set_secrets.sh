#!/usr/bin/env bash
# Push your local .env keys into the repo's GitHub Secrets (used by the
# "Dub a video" workflow). Requires: gh CLI authenticated (gh auth login).
#
#   cp .env.example .env   # fill in your keys
#   bash scripts/set_secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null; then
    echo "Install GitHub CLI first: https://cli.github.com" >&2
    exit 1
fi

[ -f .env ] || { echo ".env not found — copy .env.example and fill it in" >&2; exit 1; }

set -a; source .env; set +a

for KEY in ELEVENLABS_API_KEY FAL_KEY; do
    VAL="${!KEY:-}"
    if [ -n "$VAL" ]; then
        printf '%s' "$VAL" | gh secret set "$KEY"
        echo "✓ $KEY → GitHub Secrets"
    else
        echo "– $KEY empty in .env, skipped"
    fi
done

echo "Done. The workflow now sees these as secrets.* — the keys never touch the repo."
