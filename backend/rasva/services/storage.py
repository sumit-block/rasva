"""Managed-storage accounting and preflight (PRD §23, NFR-02).

Usage is measured from disk (allocated blocks), not estimated from metadata, so
caches written by third-party libraries are counted too. Results are cached for
a few seconds because walking large model directories is not free.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rasva.config import Paths

WARN_RATIO = 0.80
BLOCK_DOWNLOAD_RATIO = 0.90

Purpose = Literal["download", "upload", "generation", "conversion"]

# Planning bands from PRD §23. Informational, not hard partitions.
PLANNING_BANDS_GB = {
    "audio_models": 6,
    "image_models": 16,
    "environments": 7,
    "references": 7,
    "outputs": 7,
    "caches": 4,
    "database_logs": 1,
    "contingency": 2,
}


def disk_usage_bytes(path: Path) -> int:
    """Allocated size of a tree, not following symlinks."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        blocks = getattr(st, "st_blocks", None)
                        total += blocks * 512 if blocks is not None else st.st_size
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
    return total


@dataclass
class StorageUsage:
    budget_bytes: int
    os_reserve_bytes: int
    used_bytes: int
    free_disk_bytes: int
    total_disk_bytes: int
    categories: dict[str, int]
    level: Literal["ok", "warning", "critical", "full"]
    measured_at: float

    @property
    def ratio(self) -> float:
        return self.used_bytes / self.budget_bytes if self.budget_bytes else 1.0


@dataclass
class Preflight:
    allowed: bool
    required_bytes: int
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


class StorageService:
    def __init__(self, paths: Paths, ttl_s: float = 10.0) -> None:
        self._paths = paths
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._cached: StorageUsage | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def _categories(self) -> dict[str, int]:
        p = self._paths
        refs = outputs = 0
        if p.projects.exists():
            for project in p.projects.iterdir():
                if project.is_dir():
                    refs += disk_usage_bytes(project / "references")
                    refs += disk_usage_bytes(project / "imports")
                    outputs += disk_usage_bytes(project / "outputs")
        return {
            "audio_models": disk_usage_bytes(p.models_audio),
            "image_models": disk_usage_bytes(p.models_image),
            "environments": disk_usage_bytes(p.envs),
            "references": refs,
            "outputs": outputs,
            "caches": disk_usage_bytes(p.cache) + disk_usage_bytes(p.tmp),
            "database_logs": disk_usage_bytes(p.db_dir) + disk_usage_bytes(p.logs),
        }

    def usage(
        self, budget_bytes: int, os_reserve_bytes: int, *, force: bool = False
    ) -> StorageUsage:
        with self._lock:
            cached = self._cached
            if (
                cached
                and not force
                and time.monotonic() - cached.measured_at < self._ttl
                and cached.budget_bytes == budget_bytes
                and cached.os_reserve_bytes == os_reserve_bytes
            ):
                return cached
        categories = self._categories()
        used = sum(categories.values())
        du = shutil.disk_usage(self._paths.home)
        ratio = used / budget_bytes if budget_bytes else 1.0
        if ratio >= 1.0:
            level = "full"
        elif ratio >= BLOCK_DOWNLOAD_RATIO:
            level = "critical"
        elif ratio >= WARN_RATIO:
            level = "warning"
        else:
            level = "ok"
        result = StorageUsage(
            budget_bytes=budget_bytes,
            os_reserve_bytes=os_reserve_bytes,
            used_bytes=used,
            free_disk_bytes=du.free,
            total_disk_bytes=du.total,
            categories=categories,
            level=level,
            measured_at=time.monotonic(),
        )
        with self._lock:
            self._cached = result
        return result

    def preflight(
        self,
        required_bytes: int,
        purpose: Purpose,
        *,
        budget_bytes: int,
        os_reserve_bytes: int,
    ) -> Preflight:
        """Decide whether a write of ``required_bytes`` may proceed.

        ``required_bytes`` must be the *peak* need, including temporary copies
        made during download or conversion.
        """
        if required_bytes < 0:
            raise ValueError("required_bytes must be non-negative")
        u = self.usage(budget_bytes, os_reserve_bytes, force=True)
        result = Preflight(allowed=True, required_bytes=required_bytes)
        after = u.used_bytes + required_bytes

        if after > u.budget_bytes:
            result.allowed = False
            result.reasons.append(
                f"This needs {_gb(required_bytes)} but only "
                f"{_gb(max(u.budget_bytes - u.used_bytes, 0))} of your Rasva storage is left."
            )
        elif purpose == "download" and u.ratio >= BLOCK_DOWNLOAD_RATIO:
            result.allowed = False
            result.reasons.append(
                "Rasva storage is over 90% full. Remove unused models or old previews "
                "before downloading more."
            )
        if u.free_disk_bytes - required_bytes < u.os_reserve_bytes:
            result.allowed = False
            result.reasons.append(
                f"Your Mac needs at least {_gb(u.os_reserve_bytes)} free for macOS itself; "
                f"only {_gb(u.free_disk_bytes)} is free on the disk."
            )
        if result.allowed and after / u.budget_bytes >= WARN_RATIO:
            result.warnings.append(
                f"After this, Rasva storage will be {after / u.budget_bytes:.0%} full."
            )
        return result
