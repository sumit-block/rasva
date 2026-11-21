# Starting Rasva without the terminal (macOS)

This covers the PRD release checklist item "App launches without normal terminal
interaction".

## What you get

`scripts/make_mac_app.sh [--dest DIR]` makes two apps, by default in
`~/Applications`:

| App | What it does |
| --- | --- |
| **Rasva Studio** | See the steps below. |
| **Stop Rasva** | Stops the server on Rasva's port, however it was started (app, `start.sh`, `dev.sh`). |

What **Rasva Studio** does when you open it:

1. If Rasva already answers on its port, it only opens the browser. It never
   starts a second copy.
2. Otherwise it builds the UI, but only if `frontend/dist` is missing or older
   than a source file.
3. It runs `uv sync`, starts the server in the background and waits for it to
   answer.
4. It opens http://127.0.0.1:8765.
5. It stays in the Dock while Rasva runs. Quitting it (Dock menu or Cmd-Q)
   stops Rasva, and it leaves the Dock by itself if Rasva stops some other way.

All the logic lives in `scripts/mac_launcher.sh` (`start`, `stop`, `status`,
`open`). The apps only remember where the repository is. So after `git pull`
the apps pick up changes without being remade. If you move the repository, run
`make_mac_app.sh` again.

### How it finds its tools

Apps opened from Finder get a bare `PATH` and no shell profile, so nvm is not
loaded. The launcher looks for each tool itself:

- **uv:** `RASVA_UV`, then Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`),
  then `~/.local/bin`.
- **Node:** the newest nvm install that passes the same version test as
  `start.sh` (20.19+ or 22.12+), then Homebrew `node@22` and `node`. Node is only
  needed when the UI has to be rebuilt.
- **The server's `PATH`:** python.org's Python (as its installer adds to shell
  profiles), then Homebrew, then the system. A new backend environment gets
  the same Python as from the terminal, and the server finds `ffmpeg` and
  `uv`. An existing `backend/.venv` is kept as it is.

### Single instance and stopping

- **Health check.** "Running" means `/api/v1/system/health` answers with
  Rasva's reply. If something else holds the port, you get a dialog and
  nothing is started.
- **Double clicks.** A lock in the data folder (`.launcher.lock`) stops two
  starts from racing. A second launcher waits for the first and then opens the
  browser.
- **Stopping.** The launcher finds the process listening on the port, checks
  that its command line is `… -m rasva`, sends SIGTERM and waits up to 25
  seconds. The server shuts down gracefully, with workers and the database
  closed properly. It never kills a process by pid file alone and never sends
  SIGKILL.

### Failures

- Each failure shows a macOS alert that says what went wrong and where the log
  is. The cases covered: Node, uv or the repository not found; `npm ci`, the
  build or `uv sync` failing; the port taken; no answer within 120 s.
- The log is `logs/launcher.log` in the data folder. It is replaced once it
  passes 5 MB. It also receives the server's console output: the same log
  records as the server's own log, which follow the privacy rules. Engine
  workers write to their own logs, not here.
- Progress messages ("Building the interface…") use notifications. macOS may
  hide those, so nothing important relies on them.

## Settings baked into the apps

`RASVA_HOME`, `RASVA_PORT`, `RASVA_LAN_PORT` and `RASVA_LAN_SETUP_PORT` are
written into the apps if they are set when you run `make_mac_app.sh`. They go
in `Contents/Resources/launcher.env`, because apps don't see shell variables.

Apps made for another port get the port in their name and bundle id, for
example "Rasva Studio (8781)", so a test copy can't be mistaken for the real
one.

## Privacy prompt: repository in Downloads, Desktop or Documents

macOS protects these folders. The first time each app (Rasva Studio, then Stop
Rasva) runs from a repository there, macOS should ask whether it may access the
folder. Choose **Allow**.

- Remaking the apps changes their ad-hoc signature, so macOS may ask again.
- If you chose Don't Allow, the app explains how to fix it in System Settings
  › Privacy & Security › Files & Folders.
- To avoid the prompt entirely, move the repository to a folder such as
  `~/Developer/rasva` and run `make_mac_app.sh` again.

This is why the apps are AppleScript applets rather than shell scripts in a
bundle. macOS attributes a shell-script app's file access to `/bin/bash`, and
refuses access to Downloads without asking. That was tested on the M2 on
2026-11-20: the launch failed with "Operation not permitted". An applet has
its own identity, so it can be asked and granted.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| **AppleScript applet made by a script (chosen)** | Built-in tools only (`osacompile`, `codesign`); ad-hoc signed on this Mac with no developer account; a real app identity for privacy prompts; Dock quit gives a natural Stop | Plain generic icon; Gatekeeper and privacy prompts need checking by hand |
| Shell script inside a `.app` bundle | Simplest to read | macOS sees `/bin/bash`, so it can't access a repository in Downloads, Desktop or Documents, and can't be granted access without granting it to every bash script |
| Per-user LaunchAgent at login | Always running, no clicks | Keeps 8765 open and engines loadable all day; a login item to manage; if it fails there's no feedback; it still needs a way to open the browser. Can be added later on top of `mac_launcher.sh start` |
| Shortcuts / Automator action | No files in the repository | Can't be made reproducibly from a script; must be clicked together by hand |

## Uninstall

1. Quit Rasva Studio.
2. Move **Rasva Studio** and **Stop Rasva** to the Trash.

Nothing else is installed: no login items, launch agents or background
services. Projects, models and settings stay in the data folder. The launcher
log is `logs/launcher.log` inside it.

## What was tested, and what needs a person

Tested on the M2 on 2026-11-20 with `RASVA_HOME` and ports 8781–8783:

- **Bare environment.** The launcher was run with an empty environment
  (`env -i`, system `PATH` only). It found nvm's Node 22 (and skipped v20.15),
  built the UI, found Homebrew uv and started the server. The server saw
  Homebrew's `PATH` and found FFmpeg.
- **Applet mechanics, from a copy of the repository outside Downloads:**
  - `open` started the server, which stayed running as its own session after
    the launcher exited.
  - Opening the app again started no second server.
  - Quitting the app stopped the server.
  - Stop Rasva stopped the server, and the main app then left the Dock by
    itself.
- **Single instance:** two simultaneous cold starts gave exactly one server.
- **Slow health check.** A Rasva process listening on the port counts as
  running even if the health check times out (a heavy job can cause this). It
  is never reported as "another program". The Dock app also needs two stopped
  readings in a row before it leaves.
- **Port taken by another program:** a refusal for both start and stop.
- **Failure alert:** a real alert was shown and dismissed itself.

Needs the owner, by hand:

- **Finder double-click** on the apps made into `~/Applications`, with the
  repository where it is now (`~/Downloads/...`). Expect one prompt to allow
  access to Downloads. Rasva should then open in the browser.
- **Quit while starting.** Quitting Rasva Studio during a first build leaves
  the start running. Use Stop Rasva afterwards if you meant to cancel.
- **Gatekeeper.** Apps made locally carry no quarantine flag, so none is
  expected. If macOS still refuses, right-click the app › Open once.
- **First launch after a `git pull` that changes the UI.** The interface
  rebuilds; expect a minute's wait before the browser opens.
- **The "access denied" alert.** It was not shown for real, because that needs
  the privacy prompt answered with Don't Allow.
