"""Test-only engine that tries to reach the network from inside its worker.

Loaded by real engine workers (tests/ is under the backend root). ``load`` makes
one TCP attempt, and so does importing this module inside a worker, to the
port in ``RASVA_TEST_PROBE_PORT``; ``run`` tries each
socket entry point against ``inputs["port"]`` and writes what happened to
``result.json``.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Callable
from typing import Any

from rasva.engines.builtin.testing import FaultEngine
from rasva.engines.contract import (
    EngineDescriptor,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)


def _attempt(fn: Callable[[], object]) -> str:
    try:
        result = fn()
    except OSError as exc:
        return f"blocked:{type(exc).__name__}" if "Rasva" in str(exc) else f"error:{exc!r}"
    if isinstance(result, socket.socket):
        result.close()
    return "connected" if result in (None, 0) or isinstance(result, socket.socket) else str(result)


def _connect(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(("127.0.0.1", port))


def _connect_ex(port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port))


def _in_worker() -> bool:
    spec = getattr(sys.modules["__main__"], "__spec__", None)
    return getattr(spec, "name", None) == "rasva.engines.worker"


_PORT = int(os.environ.get("RASVA_TEST_PROBE_PORT", "0"))
# The API process imports this module too (registry); only probe in the worker.
IMPORT_OUTCOME = _attempt(lambda: _connect(_PORT)) if _PORT and _in_worker() else "skipped"


class NetProbe(FaultEngine):
    descriptor = EngineDescriptor(**{**FaultEngine.descriptor.model_dump(), "id": "test-net-probe"})

    def load(self, ctx: RuntimeContext) -> None:
        self.load_outcome = _attempt(lambda: _connect(_PORT)) if _PORT else "skipped"

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"port": int(inputs["port"])}

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        port = inputs["port"]
        outcomes = {
            "import": IMPORT_OUTCOME,
            "load": self.load_outcome,
            "connect": _attempt(lambda: _connect(port)),
            "connect_ex": _attempt(lambda: _connect_ex(port)),
            "create_connection": _attempt(
                lambda: socket.create_connection(("localhost", port), timeout=2)
            ),
            "getaddrinfo": _attempt(lambda: socket.getaddrinfo("example.com", 443) and None),
        }
        a, b = socket.socketpair()  # local IPC stays allowed
        with a, b:
            a.sendall(b"ok")
            outcomes["socketpair"] = b.recv(2).decode()
        (ctx.output_dir / "result.json").write_text(json.dumps(outcomes), encoding="utf-8")
        return RunResult(
            outputs=[OutputFile(filename="result.json", media_type="application/json")]
        )
