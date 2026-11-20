"""Run an engine's one-time ``prepare`` step inside its own environment.

    python -m rasva.engines.prepare --engine pkg.module:Class --model-dir DIR --cache-dir DIR

Started by the installer with the engine's interpreter, after the download and the
environment build. Network access is blocked first: preparing converts files that are
already on disk and verified, so any connection attempt is a bug and fails loudly.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path


def block_network() -> None:
    def deny(*args: object, **kwargs: object) -> None:
        host = args[0] if args else "?"
        print(f"rasva: blocked a network connection while preparing ({host!r})", file=sys.stderr)
        raise OSError("Network access is blocked while preparing an engine.")

    socket.getaddrinfo = deny  # type: ignore[assignment]
    socket.create_connection = deny  # type: ignore[assignment]
    socket.socket.connect = lambda self, *a, **k: deny(*a)  # type: ignore[method-assign]
    socket.socket.connect_ex = lambda self, *a, **k: deny(*a)  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an installed engine's weights.")
    parser.add_argument("--engine", required=True)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    block_network()

    from rasva.engines.contract import InstallContext
    from rasva.engines.registry import load_class

    engine = load_class(args.engine)()
    started = time.perf_counter()
    engine.prepare(InstallContext(model_dir=args.model_dir, cache_dir=args.cache_dir))
    print(f"rasva: prepared in {time.perf_counter() - started:.1f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
