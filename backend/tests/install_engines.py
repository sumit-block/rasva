"""Engine classes for installer tests, created at runtime with a local server URL."""

from __future__ import annotations

import sys
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    InstallContext,
    ModelArtifact,
    OutputFile,
    RunContext,
    RunResult,
)


class _Base(Engine):
    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return inputs

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        (ctx.output_dir / "x.txt").write_text("x")
        return RunResult(outputs=[OutputFile(filename="x.txt", media_type="text/plain")])


class Preparing(_Base):
    """prepare() writes a marker atomically and counts its runs (installer tests)."""

    def prepare(self, ctx: InstallContext) -> None:
        runs = ctx.model_dir / "prepare-runs.txt"
        n = int(runs.read_text()) + 1 if runs.exists() else 1
        runs.write_text(str(n))
        tmp = ctx.model_dir / ".prepared.tmp"
        tmp.write_text((ctx.model_dir / "weights" / "model.bin").read_bytes()[:4].hex())
        tmp.rename(ctx.model_dir / "prepared.txt")

    def verify(self, ctx: InstallContext) -> bool:
        return (ctx.model_dir / "prepared.txt").is_file()


class FailingPrepare(_Base):
    def prepare(self, ctx: InstallContext) -> None:
        raise RuntimeError("conversion ran out of memory (test)")


class NetworkPrepare(_Base):
    def prepare(self, ctx: InstallContext) -> None:
        import socket

        socket.create_connection(("127.0.0.1", 9))  # must be blocked


def make_engine(
    name: str,
    artifacts: list[ModelArtifact],
    *,
    license_class: str = "permissive",
    requirements_lock: str | None = None,
    verify_ok: bool = True,
    base: type[Engine] = _Base,
) -> str:
    """Define ``tests.install_engines.<name>`` and return its import path."""
    descriptor = EngineDescriptor(
        id=f"test-{name.lower()}",
        display_name=f"Test {name}",
        summary="Installer test engine.",
        version="1.0.0",
        modality="audio",
        tasks=["test.installed"],
        license="Test licence",
        license_class=license_class,  # type: ignore[arg-type]
        artifacts=artifacts,
        requirements_lock=requirements_lock,
    )
    body: dict[str, Any] = {"descriptor": descriptor}
    if base is _Base:
        body["verify"] = lambda self, ctx: verify_ok
    cls = type(name, (base,), body)
    setattr(sys.modules[__name__], name, cls)
    return f"{__name__}:{name}"
