#!/usr/bin/env bash
# Development: backend on :8765, Vite (hot reload) on :5173 proxying /api.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Vite 8 needs Node 20.19+ or 22.12+; older versions silently skip its native parts.
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)' || {
  echo "Rasva needs Node 22.12 or newer (found $(node -v)). With nvm: nvm install 22 && nvm use 22" >&2
  echo "Then remove frontend/node_modules and run this again." >&2
  exit 1
}
export RASVA_HOME="${RASVA_HOME:-$ROOT/.rasva-dev}"

(cd "$ROOT/backend" && uv sync --quiet)
(cd "$ROOT/frontend" && npm install --silent)

(cd "$ROOT/backend" && uv run python -m rasva) &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT

cd "$ROOT/frontend" && npm run dev
