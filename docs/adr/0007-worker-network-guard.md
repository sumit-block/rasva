# ADR-0007: Engine workers cannot open network connections

- Status: Accepted
- Date: 2026-11-20
- PRD refs: §8, §21.5, §27

## Context

ADR-0002 keeps generation offline by setting `HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE` and similar flags in the worker environment. Those flags
only reach the libraries that read them. On 2026-11-20 the first Chatterbox
load on the M2 downloaded a 34 MB spacy_pkuseg model from GitHub into
`~/.pkuseg` anyway. That case was fixed in the Chatterbox adapter, but any other
dependency could do the same thing silently. PRD §21.5 requires no network
during normal use after setup.

## Decision

- Before the worker imports the engine module, `rasva/engines/netguard.py`
  installs a `sys.addaudithook` hook. CPython raises `socket.*` audit events
  inside the C socket module, so one hook covers `socket.connect` (and with it
  `connect_ex` and `create_connection`), `sendto`/`sendmsg`, and all name
  lookups (`getaddrinfo`, `gethostbyname`, ...), including calls that use
  `_socket` directly. Audit hooks cannot be removed, so a library cannot undo
  the guard.
- A blocked attempt raises an `OSError` subclass (`NetworkBlocked`, or
  `LookupBlocked` for lookups), which is what libraries see with Wi-Fi off. A
  library that probes the network and falls back to its local cache keeps
  working. One line goes to the engine log
  (`logs/engines/<engine>.log`) naming the host and port and the calling file
  and line, never data. Each distinct target is logged once. A job's metrics
  include `network_blocked`, the attempts blocked since the worker's previous
  result (so a cold start includes import and load), when that is non-zero.
- AF_UNIX sockets stay allowed because they are local IPC. Loopback TCP and UDP
  are blocked too, because the worker talks to its parent over stdin/stdout
  only.
- Installs and environment builds run in the API process through
  `services/installer.py`, never in a worker, so they are unaffected.
- `RASVA_WORKER_ALLOW_NETWORK=1` in the app's environment skips the guard. It
  is meant only for debugging a dependency and is off by default. When it is
  set, the worker logs that the guard is off.

## Consequences

- An engine dependency that needs the network now fails during load or
  generation, with the host named in its log, instead of downloading silently.
  Fix such cases in the adapter or installer (pin and install the file), not by
  turning the guard off.
- Limits: the guard works at the Python level only. Native code that opens
  sockets without going through Python's `socket` module, and child processes
  started by an engine (for example `ffmpeg` from the mixer), are not covered.
  An OS-level sandbox, such as macOS `sandbox-exec` with `deny network*`, would
  close this gap. It is not done yet.
- Tests (`tests/test_worker_network_guard.py`) run a probe engine through the
  real subprocess path. The probe cannot reach a local TCP listener at import,
  load or run, its lookups fail, and the log names the attempt. With the escape
  hatch set, the same probe does connect.
