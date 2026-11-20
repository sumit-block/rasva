#!/usr/bin/env bash
# Generate a hash-pinned lockfile for an engine whose code is installed from GitHub.
#   scripts/lock_engine.sh indicf5 <full commit sha from github.com/AI4Bharat/IndicF5>
# Run on a machine that can reach github.com and pypi.org (e.g. the Mac). Review the
# diff of requirements.lock before committing it.
set -euo pipefail
ENGINE="${1:?engine id, e.g. indicf5}"
COMMIT="${2:?full 40-character commit sha to pin}"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "Use a full 40-character commit sha." >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/backend/rasva/engines/$ENGINE"
case "$ENGINE" in
  indicf5) PY=3.10 ;;
  *) echo "No GitHub-sourced lock recipe for $ENGINE" >&2; exit 1 ;;
esac
TMP="$(mktemp)"
sed "s/@COMMIT/@$COMMIT/" "$DIR/requirements.in" > "$TMP"
uv pip compile "$TMP" --generate-hashes --python-version "$PY" \
  --python-platform aarch64-apple-darwin --no-header --no-annotate -o "$DIR/requirements.lock"
rm -f "$TMP"
echo "Wrote $DIR/requirements.lock. Check that every line has --hash and the git URL pins $COMMIT."
