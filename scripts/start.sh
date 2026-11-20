#!/usr/bin/env bash
# Build the UI once and serve everything from the backend on http://127.0.0.1:8765
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/frontend" && npm ci --silent && npm run build)
cd "$ROOT/backend" && uv sync --quiet --no-dev && exec uv run --no-dev python -m rasva
