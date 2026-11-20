#!/usr/bin/env bash
# Development: backend on :8765, Vite (hot reload) on :5173 proxying /api.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export RASVA_HOME="${RASVA_HOME:-$ROOT/.rasva-dev}"

(cd "$ROOT/backend" && uv sync --quiet)
(cd "$ROOT/frontend" && npm install --silent)

(cd "$ROOT/backend" && uv run python -m rasva) &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT

cd "$ROOT/frontend" && npm run dev
