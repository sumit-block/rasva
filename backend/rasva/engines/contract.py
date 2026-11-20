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

from pydantic import BaseModel, Field, field_validator

LicenseClass = Literal["permissive", "open-weight", "restricted", "unknown"]
Modality = Literal["audio", "image", "diagnostic", "tool"]


class ModelArtifact(BaseModel):
    """One file the installer downloads into the engine's model directory.

    ``url`` must be pinned to an immutable revision (e.g. a Hugging Face commit
    hash, never ``main``). Installation refuses artifacts without a sha256.
    """

    path: str = Field(pattern=r"^[A-Za-z0-9._\-]+(/[A-Za-z0-9._\-]+)*$")  # relative to model_dir
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def _no_dot_segments(cls, v: str) -> str:
        if any(part in (".", "..") for part in v.split("/")):
            raise ValueError("artifact path may not contain '.' or '..' segments")
        return v


class Voice(BaseModel):
    """A preset voice an audio engine offers (PRD §9.3 voice cards)."""

    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    display_name: str
    language: Literal["en", "hi"]
    accent: str | None = None  # e.g. "American", "British"
    gender: Literal["female", "male"] | None = None


class Preset(BaseModel):
    """A quality preset: plain-language name for real engine settings (PRD §5.1).

    Only engines with genuinely different quality settings declare presets; the
    UI shows no preset choice for engines without them (capability honesty).
    """

    id: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    label: str
    description: str
    settings: dict[str, Any]


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
    # Weights to download (P0-013). Empty for built-in engines.
    artifacts: list[ModelArtifact] = []
    # Packaged requirements file (relative to the engine's Python package) with
    # pinned versions and --hash lines. If set, the installer creates an
    # isolated venv at envs/<id>/ from it (ADR-0003).
    requirements_lock: str | None = None
    # Python version for the isolated environment, e.g. "3.12" (uv fetches it if
    # missing). None = same version as the Rasva app.
    python_version: str | None = None
    voices: list[Voice] = []
    # Language -> voice used when nothing better is known (no ratings yet).
    default_voices: dict[str, str] = {}
    presets: list[Preset] = []
    # Third-party components with licences users should know about (PRD §28).
    license_notes: list[str] = []


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

    def prepare(self, ctx: InstallContext) -> None:  # noqa: B027 - optional hook
        """One-time conversion of downloaded weights (e.g. quantising), run by the
        installer after the download and environment build, before ``verify``.

        Runs in the engine's own environment, in a separate process, with network
        access blocked (``rasva.engines.prepare``). Must be idempotent and write its
        results atomically (temp folder, then rename), so a cancelled or failed run
        leaves nothing half-written and a reinstall can resume.
        """

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


def prepare_owner(cls: type[Engine]) -> type[Engine] | None:
    """The class that defines ``prepare`` for ``cls``, or None if it has no prepare step."""
    for klass in cls.__mro__:
        if "prepare" in vars(klass):
            return None if klass is Engine else klass
    return None
