#!/usr/bin/env bash
# Build the UI once and serve everything from the backend on http://127.0.0.1:8765
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Vite 8 needs Node 20.19+ or 22.12+; older versions silently skip its native parts.
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)' || {
  echo "Rasva needs Node 22.12 or newer (found $(node -v)). With nvm: nvm install 22 && nvm use 22" >&2
  echo "Then remove frontend/node_modules and run this again." >&2
  exit 1
}
(cd "$ROOT/frontend" && npm ci --silent && npm run build)
cd "$ROOT/backend" && uv sync --quiet --no-dev && exec uv run --no-dev python -m rasva
