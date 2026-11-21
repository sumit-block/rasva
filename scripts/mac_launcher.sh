#!/bin/bash
# Start or stop Rasva without a terminal. Called by the apps that
# scripts/make_mac_app.sh generates; also usable by hand:
#
#   scripts/mac_launcher.sh start    # build the UI if needed, start once, open the browser
#   scripts/mac_launcher.sh stop     # stop the server listening on RASVA_PORT
#   scripts/mac_launcher.sh status   # 0 running, 3 starting, 2 port taken by another program, 1 stopped
#   scripts/mac_launcher.sh open     # open the browser if Rasva is running
#
# Apps started from Finder get a bare PATH and no shell profile (so no nvm), so
# everything here finds its tools itself. Written for macOS /bin/bash 3.2.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${RASVA_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
HEALTH_URL="$URL/api/v1/system/health"
HOME_DIR="${RASVA_HOME:-$HOME/Library/Application Support/Rasva Studio}"
LOG_DIR="$HOME_DIR/logs"
LOG="$LOG_DIR/launcher.log"
LOCK="$HOME_DIR/.launcher.lock"
START_TIMEOUT="${RASVA_START_TIMEOUT:-120}"   # seconds to wait for the server to answer

# What a login shell on this Mac would have: python.org's Python first (its
# installer adds this to shell profiles, so uv picks the same interpreter for a
# new environment as in the terminal), then Homebrew (Apple silicon and Intel),
# then the system. The server needs this PATH too: it looks up ffmpeg and uv
# with shutil.which.
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$LOG_DIR"
# Keep the launcher log small: start over once it passes 5 MB.
if [ -f "$LOG" ] && [ "$(stat -f %z "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv -f "$LOG" "$LOG.1"
fi

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# AppleScript string literal: escape backslashes and double quotes.
as_quote() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "$s"; }

alert() {   # alert "title" "message"
  log "ALERT: $1: $2"
  if [ -n "${RASVA_LAUNCHER_NO_UI:-}" ]; then
    printf '%s: %s\n' "$1" "$2" >&2
    return
  fi
  /usr/bin/osascript -e "display alert $(as_quote "$1") message $(as_quote "$2") as critical" >/dev/null 2>&1 || true
}

notify() {  # best effort only: macOS may hide notifications from osascript
  [ -n "${RASVA_LAUNCHER_NO_UI:-}" ] && return
  /usr/bin/osascript -e "display notification $(as_quote "$2") with title $(as_quote "$1")" >/dev/null 2>&1 || true
}

fail() {    # fail "what went wrong"
  alert "Rasva Studio could not start" "$1

Details are in the log:
$LOG"
  exit 1
}

# PIDs of Rasva servers listening on PORT (verified by command line, never blind).
rasva_pids() {
  local pid cmd
  for pid in $(/usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | sort -u); do
    cmd="$(/bin/ps -o command= -p "$pid" 2>/dev/null)"
    case "$cmd" in
      *" -m rasva"|*" -m rasva "*) printf '%s\n' "$pid" ;;
    esac
  done
}

# 0 = Rasva runs on PORT, 1 = nothing listens, 2 = something else holds the port.
# A Rasva process listening on the port counts as running even when the health
# check is slow (a heavy job can starve it); uvicorn only listens once started up.
probe() {
  local body
  body="$(/usr/bin/curl -s --max-time 2 "$HEALTH_URL" 2>/dev/null)"
  case "$body" in
    *'"status":"ok"'*'"schema_version"'*) return 0 ;;
  esac
  [ -n "$(rasva_pids)" ] && return 0
  if /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    return 2
  fi
  return 1
}

open_browser() {
  if [ -n "${RASVA_LAUNCHER_NO_UI:-}" ]; then echo "Rasva is running at $URL/"; return; fi
  /usr/bin/open "$URL/"
}

# --- tools ------------------------------------------------------------------

find_uv() {
  local c
  for c in "${RASVA_UV:-}" /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

node_ok() {  # same rule as start.sh: Node 20.19+, 22.12+ or newer than 22
  "$1" -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)' >/dev/null 2>&1
}

find_node() {
  local c best=""
  # nvm installs, newest first (sort -V is not on macOS; sort numerically per field).
  if [ -d "${NVM_DIR:-$HOME/.nvm}/versions/node" ]; then
    for c in $(ls -1 "${NVM_DIR:-$HOME/.nvm}/versions/node" 2>/dev/null | sed 's/^v//' | sort -t. -k1,1nr -k2,2nr -k3,3nr); do
      if [ -x "${NVM_DIR:-$HOME/.nvm}/versions/node/v$c/bin/node" ] && node_ok "${NVM_DIR:-$HOME/.nvm}/versions/node/v$c/bin/node"; then
        best="${NVM_DIR:-$HOME/.nvm}/versions/node/v$c/bin/node"; break
      fi
    done
  fi
  if [ -z "$best" ]; then
    for c in /opt/homebrew/opt/node@22/bin/node /usr/local/opt/node@22/bin/node /opt/homebrew/bin/node /usr/local/bin/node; do
      if [ -x "$c" ] && node_ok "$c"; then best="$c"; break; fi
    done
  fi
  [ -n "$best" ] && printf '%s\n' "$best"
}

# The UI needs rebuilding when dist is missing or any source file is newer than it.
ui_needs_build() {
  local stamp="$ROOT/frontend/dist/index.html"
  [ -f "$stamp" ] || return 0
  [ -n "$(find "$ROOT/frontend" \( -path "$ROOT/frontend/node_modules" -o -path "$ROOT/frontend/dist" \) -prune \
          -o -type f -newer "$stamp" -print -quit 2>/dev/null)" ]
}

build_ui() {
  local node npm_cli
  node="$(find_node)" || true
  [ -n "$node" ] || fail "The interface needs building, but Node 22.12 or newer was not found (looked in ~/.nvm and Homebrew). Install it with: nvm install 22"
  export PATH="$(dirname "$node"):$PATH"
  log "building the UI with node $("$node" -v) at $node"
  notify "Rasva Studio" "Building the interface, this can take a minute…"
  local fe="$ROOT/frontend"
  if [ ! -f "$fe/node_modules/.package-lock.json" ] || [ "$fe/package-lock.json" -nt "$fe/node_modules/.package-lock.json" ]; then
    (cd "$fe" && npm ci --no-audit --no-fund) >>"$LOG" 2>&1 || fail "Installing the interface's packages (npm ci) failed."
  fi
  (cd "$fe" && npm run build) >>"$LOG" 2>&1 || fail "Building the interface (npm run build) failed."
}

# --- commands ---------------------------------------------------------------

take_lock() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if mkdir "$LOCK" 2>/dev/null; then
      echo $$ >"$LOCK/pid"
      trap 'rm -rf "$LOCK"' EXIT
      return 0
    fi
    # A lock left by a launcher that no longer runs is stale.
    local holder; holder="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -z "$holder" ] || ! kill -0 "$holder" 2>/dev/null; then
      rm -rf "$LOCK"; continue
    fi
    return 1
  done
  return 1
}

cmd_start() {
  log "start requested (port $PORT, home $HOME_DIR, repo $ROOT)"
  probe; case $? in
    0) log "already running; opening the browser"; open_browser; exit 0 ;;
    2) fail "Port $PORT is in use by another program, not Rasva. Quit that program, or set a different RASVA_PORT and rebuild the app." ;;
  esac

  if ! take_lock; then
    # Another launcher is starting Rasva right now (double click): wait for it.
    log "another launcher is starting Rasva; waiting"
    local i=0
    while [ $i -lt "$START_TIMEOUT" ]; do
      probe && { open_browser; exit 0; }
      sleep 1; i=$((i + 1))
    done
    fail "Another copy of the launcher is still starting Rasva."
  fi

  [ -d "$ROOT/backend" ] && [ -d "$ROOT/frontend" ] || fail "The Rasva folder was not found at $ROOT. If you moved it, run scripts/make_mac_app.sh again from its new place."

  local uv; uv="$(find_uv)" || fail "uv was not found (looked in /opt/homebrew/bin, /usr/local/bin and ~/.local/bin). Install it with: brew install uv"
  export RASVA_UV="$uv"

  if ui_needs_build; then build_ui; else log "UI is up to date"; fi

  log "syncing the backend environment with $uv"
  (cd "$ROOT/backend" && "$uv" sync --quiet --no-dev) >>"$LOG" 2>&1 || fail "Preparing the Python environment (uv sync) failed."
  local py="$ROOT/backend/.venv/bin/python"
  [ -x "$py" ] || fail "The Python environment is missing at $py."

  # Start in its own session so it outlives this launcher and the app that ran it.
  log "starting the server"
  # exec, so no shell lingers as the server's parent.
  (cd "$ROOT/backend" || exit 1
   exec nohup "$py" -c 'import os, sys
os.setsid()
os.execv(sys.executable, [sys.executable, "-m", "rasva"])') </dev/null >>"$LOG" 2>&1 &
  disown 2>/dev/null || true

  local i=0 rc
  while [ $i -lt "$START_TIMEOUT" ]; do
    probe; rc=$?
    if [ $rc -eq 0 ]; then
      log "server is up; opening the browser"
      open_browser
      exit 0
    fi
    sleep 1; i=$((i + 1))
  done
  fail "The server did not answer on port $PORT within $START_TIMEOUT seconds."
}

cmd_stop() {
  log "stop requested (port $PORT)"
  local pids; pids="$(rasva_pids)"
  if [ -z "$pids" ]; then
    probe; [ $? -eq 2 ] && { alert "Rasva Studio" "Port $PORT is used by another program, not Rasva. Nothing was stopped."; exit 1; }
    log "nothing to stop"
    notify "Rasva Studio" "Rasva is not running."
    [ -n "${RASVA_LAUNCHER_NO_UI:-}" ] && echo "Rasva is not running."
    exit 0
  fi
  log "sending SIGTERM to $pids"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null
  local i=0
  while [ $i -lt 25 ]; do
    [ -z "$(rasva_pids)" ] && { log "stopped"; notify "Rasva Studio" "Rasva has stopped."; [ -n "${RASVA_LAUNCHER_NO_UI:-}" ] && echo "Rasva stopped."; exit 0; }
    sleep 1; i=$((i + 1))
  done
  alert "Rasva Studio did not stop" "The server (process $pids) is still running after 25 seconds; a job may be finishing. Try again, or quit it from Activity Monitor (search for python).

Log: $LOG"
  exit 1
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status)
    # 0 running, 3 starting (a launcher holds the lock), 2 port used by another program, 1 stopped
    probe; rc=$?
    if [ $rc -eq 0 ]; then echo "running on $URL"; exit 0; fi
    holder="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then echo "starting"; exit 3; fi
    if [ $rc -eq 2 ]; then echo "port $PORT is used by another program"; exit 2; fi
    echo "not running"; exit 1 ;;
  open) probe && open_browser ;;
  *) echo "usage: $0 [start|stop|status|open]" >&2; exit 64 ;;
esac
