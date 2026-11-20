#!/bin/bash
# Make two small macOS apps that start and stop Rasva without the terminal:
#
#   Rasva Studio.app   builds the UI if needed, starts the server once and opens
#                      the browser. It stays in the Dock while Rasva runs;
#                      quitting it (Dock menu or Cmd-Q) stops Rasva.
#   Stop Rasva.app     stops the server, however it was started.
#
# Usage: scripts/make_mac_app.sh [--dest DIR]      (default: ~/Applications)
#
# The apps are AppleScript applets made with the built-in osacompile and signed
# ad hoc on this Mac, so nothing is downloaded and no developer account is needed.
# They remember where this repository is and run scripts/mac_launcher.sh, so
# `git pull` updates their behaviour without remaking them. Run this again if
# you move the repository.
#
# RASVA_HOME, RASVA_PORT, RASVA_LAN_PORT and RASVA_LAN_SETUP_PORT, if set now,
# are written into the apps (apps opened from Finder don't see shell variables).
# Apps made for a non-default port get the port in their name, so a test copy
# can't be mistaken for the real one.
#
# Uninstall: quit Rasva Studio, then move both apps to the Trash. Your projects,
# models and settings are untouched; they live in RASVA_HOME.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Applications"

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:?--dest needs a folder}"; shift 2 ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 64 ;;
  esac
done

[ "$(uname -s)" = Darwin ] || { echo "This makes macOS apps; run it on the Mac." >&2; exit 1; }
[ -f "$ROOT/scripts/mac_launcher.sh" ] || { echo "scripts/mac_launcher.sh is missing in $ROOT" >&2; exit 1; }
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

PORT="${RASVA_PORT:-8765}"
SUFFIX=""; ID_SUFFIX=""
if [ "$PORT" != 8765 ]; then SUFFIX=" ($PORT)"; ID_SUFFIX=".port$PORT"; fi

case "$ROOT/" in
  "$HOME/Downloads/"*|"$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Library/Mobile Documents/"*)
    cat >&2 <<EOF
Note: the Rasva folder is inside a folder macOS protects ($ROOT).
The first time you open Rasva Studio, macOS asks whether it may access that
folder: choose Allow. Making the apps again asks again. To avoid this, move the
Rasva folder to e.g. ~/Developer and run this script from there.

EOF
    ;;
esac

# Settings baked into each app, as shell assignments the applet sources.
env_file() {
  printf 'RASVA_REPO=%q\n' "$ROOT"
  local v
  for v in RASVA_HOME RASVA_PORT RASVA_LAN_PORT RASVA_LAN_SETUP_PORT; do
    if [ -n "${!v:-}" ]; then printf 'export %s=%q\n' "$v" "${!v}"; fi
  done
}

# Handlers shared by both applets. launcher(cmd) runs scripts/mac_launcher.sh with
# the app's baked settings; it errors with the exit status (126 when macOS blocks
# access to the repository folder, 4 when the folder is gone).
COMMON='
on launcher(cmd)
	set envFile to POSIX path of (path to me) & "Contents/Resources/launcher.env"
	set script_ to ". \"$0\" || exit 5; [ -f \"$RASVA_REPO/scripts/mac_launcher.sh\" ] || exit 4; exec /bin/bash \"$RASVA_REPO/scripts/mac_launcher.sh\" \"$@\""
	return do shell script "/bin/bash -c " & quoted form of script_ & " " & quoted form of envFile & " " & cmd
end launcher

on explain(n, msg)
	if n is 126 or msg contains "Operation not permitted" then
		display alert "macOS blocked access to the Rasva folder" message "Open System Settings > Privacy & Security > Files & Folders and allow this app to use the folder that holds Rasva (for example Downloads). Or move the Rasva folder out of Downloads, Desktop and Documents and run scripts/make_mac_app.sh again." as critical
	else if n is 4 or n is 5 then
		display alert "The Rasva folder was not found" message "It has moved or been deleted. Run scripts/make_mac_app.sh again from the Rasva folder to recreate this app." as critical
	end if
end explain

-- 0 running, 3 starting, 2 port used by another program, 1 stopped; other values are errors.
on state()
	try
		launcher("status")
		return 0
	on error msg number n
		if n is in {1, 2, 3} then return n
		explain(n, msg)
		return -1
	end try
end state
'

MAIN_APPLET="$COMMON"'
property startedAt : missing value

on run
	set startedAt to current date
	set s to state()
	if s is -1 then
		quit
	else if s is 0 then
		launcher("open")
	else
		-- In the background, so the app stays responsive during a first build.
		launcher("start >/dev/null 2>&1 &")
	end if
end run

on reopen
	if state() is 0 then launcher("open")
end reopen

-- Leave the Dock once Rasva is no longer running (stopped elsewhere, failed to start).
on idle
	if startedAt is not missing value and ((current date) - startedAt) > 15 then
		set s to state()
		if s is not 0 and s is not 3 then quit
	end if
	return 5
end idle

on quit
	set s to state()
	if s is 3 then
		display alert "Rasva Studio is still starting" message "Quit again once it has opened in the browser." giving up after 15
		return
	end if
	if s is 0 then
		try
			launcher("stop")
		end try
	end if
	continue quit
end quit
'

STOP_APPLET="$COMMON"'
on run
	try
		launcher("stop")
	on error msg number n
		explain(n, msg)
	end try
end run
'

make_app() {  # make_app "Display Name" bundle-id-tail applet-source [stay-open]
  local name="$1$SUFFIX" id="org.rasva.studio.$2$ID_SUFFIX" src="$3" stay="${4:-}"
  local app="$DEST/$name.app"
  local work; work="$(mktemp -d "${TMPDIR:-/tmp}/rasva-app.XXXXXX")"
  printf '%s\n' "$src" >"$work/applet.applescript"
  if [ -n "$stay" ]; then
    /usr/bin/osacompile -s -o "$work/$name.app" "$work/applet.applescript"
  else
    /usr/bin/osacompile -o "$work/$name.app" "$work/applet.applescript"
  fi
  local plist="$work/$name.app/Contents/Info.plist"
  /usr/bin/plutil -replace CFBundleIdentifier -string "$id" "$plist"
  /usr/bin/plutil -replace CFBundleName -string "$name" "$plist"
  env_file >"$work/$name.app/Contents/Resources/launcher.env"
  # Sign last: osacompile's signature no longer matches after the edits above.
  /usr/bin/codesign --force --sign - "$work/$name.app" >/dev/null 2>&1
  /usr/bin/codesign --verify "$work/$name.app"

  rm -rf "$app"
  mv "$work/$name.app" "$app"
  rm -rf "$work"
  echo "made $app"
}

make_app "Rasva Studio" launcher "$MAIN_APPLET" stay-open
make_app "Stop Rasva" stop "$STOP_APPLET"

cat <<EOF

Open "Rasva Studio$SUFFIX" from $DEST (Finder, Launchpad or Spotlight) to start
Rasva. Quit it from the Dock to stop Rasva, or open "Stop Rasva$SUFFIX".
Launcher log: ${RASVA_HOME:-~/Library/Application Support/Rasva Studio}/logs/launcher.log
Uninstall: quit Rasva Studio, then move both apps to the Trash; your data is not affected.
EOF
