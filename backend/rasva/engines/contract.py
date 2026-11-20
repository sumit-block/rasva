"""The engine adapter contract (PRD §21.2).

An engine has two halves:

- **Control-plane methods** (``describe``, ``check_compatibility``, ``install``,
  ``verify``, ``remove``) run in the API process and must not import heavy ML
  libraries at module import time.
- **Runtime methods** (``load``, ``validate_input``, ``run``, ``unload``) run only
  inside an isolated worker subprocess (docs/adr/0001-inference-worker-process.md),
  so a crash or memory exhaustion never takes down the API, and unloading is
  guaranteed by process exit.

Adapters import ML frameworks lazily inside ``load``.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

LicenseClass = Literal["permissive", "open-weight", "restricted", "unknown"]
Modality = Literal["audio", "image", "diagnostic"]


class EngineDescriptor(BaseModel):
    """Static capability metadata. Shown to users in plain language."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.\-]{1,63}$")
    display_name: str
    summary: str  # one plain-language sentence, no jargon
    version: str
    modality: Modality
    tasks: list[str]
    languages: list[str] = []
    features: list[str] = []  # only features the engine genuinely supports
    license: str
    license_class: LicenseClass
    source_url: str | None = None
    approx_download_bytes: int = 0
    approx_installed_bytes: int = 0
    approx_peak_memory_bytes: int = 0
    requires_install: bool = True
    experimental: bool = False


class CompatibilityReport(BaseModel):
    compatible: bool
    reasons: list[str] = []


class OutputFile(BaseModel):
    """A file the engine wrote into its run's output directory."""

    filename: str  # relative to RunContext.output_dir, no path separators
    media_type: str
    metadata: dict[str, Any] = {}


class RunResult(BaseModel):
    outputs: list[OutputFile]
    metrics: dict[str, Any] = {}


class EngineError(Exception):
    """An expected failure with a message safe to show users."""

    def __init__(self, code: str, user_message: str, detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.code = code
        self.user_message = user_message
        self.detail = detail


class EngineInputError(EngineError):
    def __init__(self, user_message: str) -> None:
        super().__init__("invalid_input", user_message)


class Canceled(Exception):
    """Raised at a safe point after cancellation was requested."""


@dataclass
class RuntimeContext:
    model_dir: Path
    cache_dir: Path


@dataclass
class RunContext:
    job_id: str
    output_dir: Path
    _progress: Callable[[float | None, str | None], None]
    _cancel: threading.Event = field(default_factory=threading.Event)

    def report_progress(self, fraction: float | None, detail: str | None = None) -> None:
        self._progress(fraction, detail)

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def raise_if_canceled(self) -> None:
        if self._cancel.is_set():
            raise Canceled


@dataclass
class InstallContext:
    model_dir: Path
    cache_dir: Path


@dataclass
class SystemFacts:
    os: str
    arch: str
    total_memory_bytes: int
    apple_silicon: bool
    mlx_available: bool
    mps_available: bool


class Engine(ABC):
    descriptor: ClassVar[EngineDescriptor]

    # ---- control plane (API process) ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        return CompatibilityReport(compatible=True)

    def install(self, ctx: InstallContext) -> None:  # noqa: B027 - optional hook
        """Download and convert weights. Must be resumable and checksum-verified."""

    def verify(self, ctx: InstallContext) -> bool:
        return True

    def remove(self, ctx: InstallContext) -> None:  # noqa: B027 - optional hook
        """Delete installed weights only; never project files."""

    # ---- runtime (worker process only) ----

    def load(self, ctx: RuntimeContext) -> None:  # noqa: B027 - optional hook
        """Load weights into memory. Import heavy frameworks here, not at module top."""

    @abstractmethod
    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return normalized inputs or raise EngineInputError."""

    @abstractmethod
    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        """Do the work. Call ctx.raise_if_canceled() at safe points."""

    def unload(self) -> None:  # noqa: B027 - optional hook
        """Release memory. The worker process exits afterwards regardless."""
