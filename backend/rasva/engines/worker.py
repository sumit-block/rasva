"""Engine worker subprocess entry point.

Protocol: newline-delimited JSON. Parent -> worker on stdin; worker -> parent on
a private copy of the original stdout. File descriptor 1 is then redirected to
stderr, so stray ``print`` calls from ML libraries cannot corrupt the protocol.

Parent -> worker:
  {"op": "load"}
  {"op": "run", "job_id": str, "task": str, "inputs": {...}, "output_dir": str}
  {"op": "cancel"}
  {"op": "shutdown"}

Worker -> parent:
  {"event": "ready", "engine_id": str, "pid": int}
  {"event": "loaded", "load_s": float}
  {"event": "progress", "fraction": float | null, "detail": str | null}
  {"event": "result", "outputs": [...], "metrics": {...}}
  {"event": "canceled"}
  {"event": "error", "code": str, "message": str}
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import resource
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import IO, Any

from rasva.engines import netguard
from rasva.engines.contract import (
    Canceled,
    EngineError,
    RunContext,
    RuntimeContext,
)
from rasva.engines.registry import load_class


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return int(peak if sys.platform == "darwin" else peak * 1024)


class Channel:
    def __init__(self, out: IO[str]) -> None:
        self._out = out
        self._lock = threading.Lock()

    def emit(self, event: str, **payload: Any) -> None:
        line = json.dumps({"event": event, **payload}, ensure_ascii=False)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args(argv)

    protocol_out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    chan = Channel(protocol_out)

    # Before the engine module (and its ML libraries) is imported: no network
    # during load or generation (PRD §21.5). Installs run in the API process.
    netguard.install()
    engine_cls = load_class(args.engine)
    engine = engine_cls()
    runtime = RuntimeContext(model_dir=Path(args.model_dir), cache_dir=Path(args.cache_dir))

    commands: queue.Queue[dict[str, Any] | None] = queue.Queue()
    cancel_event = threading.Event()

    def reader() -> None:
        for raw in sys.stdin:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("op") == "cancel":
                cancel_event.set()
            else:
                commands.put(msg)
        commands.put(None)  # parent closed stdin or died

    threading.Thread(target=reader, daemon=True, name="rasva-worker-stdin").start()
    chan.emit("ready", engine_id=engine_cls.descriptor.id, pid=os.getpid())

    loaded = False
    while True:
        msg = commands.get()
        if msg is None or msg.get("op") == "shutdown":
            break
        op = msg.get("op")
        if op == "load":
            started = time.perf_counter()
            try:
                if not loaded:
                    engine.load(runtime)
                    loaded = True
                chan.emit("loaded", load_s=round(time.perf_counter() - started, 4))
            except EngineError as exc:
                chan.emit("error", code=exc.code, message=exc.user_message)
            except Exception:
                traceback.print_exc()
                chan.emit("error", code="load_failed", message="The engine could not start.")
        elif op == "run":
            cancel_event.clear()
            ctx = RunContext(
                job_id=msg["job_id"],
                output_dir=Path(msg["output_dir"]),
                _progress=lambda f, d: chan.emit("progress", fraction=f, detail=d),
                _cancel=cancel_event,
            )
            started = time.perf_counter()
            try:
                inputs = engine.validate_input(msg["task"], msg.get("inputs") or {})
                result = engine.run(msg["task"], inputs, ctx)
                metrics = {
                    "run_s": round(time.perf_counter() - started, 4),
                    "peak_rss_bytes": _peak_rss_bytes(),
                    **result.metrics,
                }
                if netguard.blocked_attempts():
                    metrics["network_blocked"] = netguard.blocked_attempts()
                chan.emit(
                    "result",
                    outputs=[o.model_dump() for o in result.outputs],
                    metrics=metrics,
                )
            except Canceled:
                chan.emit("canceled")
            except EngineError as exc:
                chan.emit("error", code=exc.code, message=exc.user_message)
            except MemoryError:
                chan.emit(
                    "error",
                    code="out_of_memory",
                    message="Your Mac ran low on memory while generating.",
                )
            except Exception:
                traceback.print_exc()
                chan.emit(
                    "error", code="engine_failed", message="The engine hit an unexpected problem."
                )

    try:
        engine.unload()
    finally:
        protocol_out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
