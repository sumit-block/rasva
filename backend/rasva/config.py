"""Runtime configuration and the managed directory layout.

Every byte Rasva writes lives under a single root (``RASVA_HOME``) so the 50 GB
budget can be measured from disk rather than estimated. Third-party caches
(Hugging Face, PyTorch, pip, temp files) are redirected into this root for
engine worker processes; see docs/adr/0002-managed-storage.md.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3

DEFAULT_BUDGET_BYTES = 50 * GB
# Free space that must remain on the volume for macOS itself, independent of the
# application budget (PRD §23). The PRD does not fix a number; 15 GB is an
# assumption that leaves room for swap and OS updates. Configurable in settings.
DEFAULT_OS_RESERVE_BYTES = 15 * GB
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_BYTES = 1 * GB


def default_home() -> Path:
    env = os.environ.get("RASVA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Rasva Studio"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "rasva-studio"


@dataclass(frozen=True)
class Paths:
    """Managed layout. Directory names map 1:1 onto PRD §23 storage categories."""

    home: Path

    @property
    def db_dir(self) -> Path:
        return self.home / "db"

    @property
    def db_file(self) -> Path:
        return self.db_dir / "rasva.sqlite3"

    @property
    def db_backups(self) -> Path:
        return self.db_dir / "backups"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def models_audio(self) -> Path:
        return self.models / "audio"

    @property
    def models_image(self) -> Path:
        return self.models / "image"

    @property
    def envs(self) -> Path:
        return self.home / "envs"

    @property
    def projects(self) -> Path:
        return self.home / "projects"

    @property
    def cache(self) -> Path:
        return self.home / "cache"

    @property
    def tmp(self) -> Path:
        return self.home / "tmp"

    @property
    def staging(self) -> Path:
        """Per-job scratch output; promoted into a project on success."""
        return self.tmp / "jobs"

    @property
    def uploads_tmp(self) -> Path:
        return self.tmp / "uploads"

    def project_dir(self, project_id: str) -> Path:
        return self.projects / project_id

    def all_dirs(self) -> list[Path]:
        return [
            self.home,
            self.db_dir,
            self.db_backups,
            self.logs,
            self.models_audio,
            self.models_image,
            self.envs,
            self.projects,
            self.cache / "huggingface",
            self.cache / "torch",
            self.cache / "pip",
            self.cache / "xdg",
            self.tmp,
            self.staging,
            self.uploads_tmp,
        ]

    def ensure(self) -> None:
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
        # Owner-only access: the tree holds voice references and photographs.
        with contextlib.suppress(OSError):
            self.home.chmod(0o700)

    def worker_env(self) -> dict[str, str]:
        """Environment overrides that keep engine caches inside the managed root."""
        return {
            "HF_HOME": str(self.cache / "huggingface"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            # Inference must never trigger a download; installs are explicit (PRD §8, §21.5).
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TORCH_HOME": str(self.cache / "torch"),
            "PIP_CACHE_DIR": str(self.cache / "pip"),
            "XDG_CACHE_HOME": str(self.cache / "xdg"),
            "TMPDIR": str(self.tmp),
            "DO_NOT_TRACK": "1",
        }


@dataclass(frozen=True)
class Settings:
    paths: Paths
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    # Seconds a loaded engine may sit idle before its worker is shut down to
    # return unified memory to macOS.
    engine_idle_unload_s: float = 300.0
    cancel_grace_s: float = 10.0
    enable_test_engines: bool = False
    frontend_dist: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        home = default_home()
        dist_env = os.environ.get("RASVA_FRONTEND_DIST")
        default_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        dist = Path(dist_env) if dist_env else default_dist
        return cls(
            paths=Paths(home),
            host=os.environ.get("RASVA_HOST", "127.0.0.1"),
            port=int(os.environ.get("RASVA_PORT", DEFAULT_PORT)),
            enable_test_engines=os.environ.get("RASVA_ENABLE_TEST_ENGINES") == "1",
            frontend_dist=dist if dist.is_dir() else None,
        )
