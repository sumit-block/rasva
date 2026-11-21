"""Block network access inside engine worker processes (PRD §21.5, ADR-0002).

Engines must work with no network once installed. Offline environment flags
(``HF_HUB_OFFLINE`` and friends) only cover the libraries that read them; on
2026-11-20 a Chatterbox dependency fetched a model from GitHub on first load
regardless. This guard makes any such attempt fail and leaves one line in the
engine log naming the host.

Mechanism: a ``sys.addaudithook`` hook. CPython raises the ``socket.*`` audit
events inside the C socket module, so the hook sees ``socket.connect`` (which
also covers ``connect_ex`` and ``create_connection``), ``sendto``/``sendmsg``
and every name lookup, including calls made through ``_socket`` directly.
Audit hooks cannot be removed once added, so no library can undo the guard.

Allowed: AF_UNIX sockets (local IPC, never the network). Loopback TCP/UDP is
blocked too; the worker talks to its parent over stdin/stdout only.

Not covered: native code that opens sockets without going through Python's
``socket`` module, and child processes the engine starts (they get a fresh
interpreter). Install jobs run in the API process, not here, so they are
unaffected.

Errors raised are ``OSError`` subclasses, so libraries that probe the network
and fall back to a local cache keep working; a library that needs the network
fails as it would with Wi-Fi off.

Escape hatch for debugging only: ``RASVA_WORKER_ALLOW_NETWORK=1`` in the app's
environment skips the guard and logs that it did.

This module must stay stdlib-only and Python 3.10 compatible: it runs in each
engine's own interpreter before the engine is imported.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import sysconfig
import threading
import traceback

ALLOW_ENV = "RASVA_WORKER_ALLOW_NETWORK"
LOG_PREFIX = "rasva-netguard:"

_CONNECT_EVENTS = {"socket.connect", "socket.sendto", "socket.sendmsg"}
_LOOKUP_EVENTS = {
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
    "socket.getnameinfo",
}
_MAX_HOST_CHARS = 100
_MAX_LOGGED = 50  # distinct targets; later attempts are still blocked, just not logged

_lock = threading.Lock()
_installed = False
_blocked = 0
_logged: set[str] = set()
_in_hook = threading.local()
_STDLIB = os.path.normcase(sysconfig.get_paths()["stdlib"])


class NetworkBlocked(PermissionError):
    """An engine worker tried to open a network connection."""


class LookupBlocked(socket.gaierror):
    """An engine worker tried to resolve a host name."""


def blocked_attempts() -> int:
    """Number of network attempts blocked in this process so far."""
    return _blocked


def install() -> bool:
    """Install the guard once. Returns False if the escape hatch disabled it."""
    global _installed
    if os.environ.get(ALLOW_ENV) == "1":
        _write(f"{LOG_PREFIX} {ALLOW_ENV}=1 is set; network access is NOT blocked in this worker")
        return False
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True
    return True


def _hook(event: str, args: tuple) -> None:
    if not event.startswith("socket."):
        return
    if event in _CONNECT_EVENTS:
        address = args[-1]
        if _is_unix(args[0], address):
            return
        target = _describe_address(address)
        _record("connection", target)
        raise NetworkBlocked(
            f"Network access is disabled in Rasva engine workers (blocked connection to {target})"
        )
    if event in _LOOKUP_EVENTS:
        target = _describe_host(args[0] if args else None)
        _record("lookup", target)
        raise LookupBlocked(
            socket.EAI_NONAME,
            f"Network access is disabled in Rasva engine workers (blocked lookup of {target})",
        )


def _is_unix(sock: object, address: object) -> bool:
    family = getattr(sock, "family", None)
    unix = getattr(socket, "AF_UNIX", None)
    if unix is not None and family == unix:
        return True
    # AF_UNIX addresses are paths; INET addresses are tuples.
    return isinstance(address, (str, bytes, bytearray)) and family is None


def _clean(value: object) -> str:
    text = value.decode("ascii", "replace") if isinstance(value, (bytes, bytearray)) else str(value)
    text = "".join(ch if ch.isprintable() and not ch.isspace() else "?" for ch in text)
    return text[:_MAX_HOST_CHARS] or "?"


def _describe_host(host: object) -> str:
    return "?" if host is None else _clean(host)


def _describe_address(address: object) -> str:
    if isinstance(address, tuple) and len(address) >= 2:
        port = address[1] if isinstance(address[1], int) else "?"
        return f"{_clean(address[0])}:{port}"
    return "?"


def _caller() -> str:
    """Innermost non-stdlib frame, as file:line. Names code, never data."""
    for frame in reversed(traceback.extract_stack()[:-3]):
        path = os.path.normcase(frame.filename)
        if path.startswith(_STDLIB) and "site-packages" not in path:
            continue
        if path == os.path.normcase(__file__):
            continue
        parts = frame.filename.replace("\\", "/").split("/")
        return f"{'/'.join(parts[-2:])}:{frame.lineno}"
    return "?"


def _record(kind: str, target: str) -> None:
    global _blocked
    with _lock:
        _blocked += 1
        key = f"{kind} {target}"
        if key in _logged or len(_logged) >= _MAX_LOGGED:
            return
        _logged.add(key)
    if getattr(_in_hook, "active", False):
        return
    _in_hook.active = True
    try:
        prep = "of" if kind == "lookup" else "to"
        _write(f"{LOG_PREFIX} blocked network {kind} {prep} {target} from {_caller()}")
    finally:
        _in_hook.active = False


def _write(line: str) -> None:
    # Logging must never mask the block itself.
    with contextlib.suppress(Exception):
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
