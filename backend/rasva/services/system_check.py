"""Hardware and environment readiness checks (PRD §8 step 2, P0-004).

Cheap by design: framework availability is detected with ``find_spec`` (no heavy
imports). Whether MPS/MLX actually work is verified per engine at install time,
inside a worker process.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Literal

import psutil

from rasva.config import GB
from rasva.engines.contract import SystemFacts

Status = Literal["pass", "warn", "fail"]


@dataclass
class Check:
    id: str
    label: str
    status: Status
    message: str


def facts() -> SystemFacts:
    is_mac = sys.platform == "darwin"
    arch = platform.machine().lower()
    return SystemFacts(
        os="macOS" if is_mac else platform.system(),
        arch=arch,
        total_memory_bytes=psutil.virtual_memory().total,
        apple_silicon=is_mac and arch == "arm64",
        mlx_available=importlib.util.find_spec("mlx") is not None,
        mps_available=is_mac and arch == "arm64" and importlib.util.find_spec("torch") is not None,
    )


def run_checks(free_disk_bytes: int, needed_free_bytes: int) -> dict[str, object]:
    f = facts()
    checks: list[Check] = []

    if f.apple_silicon:
        checks.append(
            Check(
                "platform",
                "Apple Silicon Mac",
                "pass",
                f"macOS {platform.mac_ver()[0]} on Apple Silicon.",
            )
        )
    else:
        checks.append(
            Check(
                "platform",
                "Apple Silicon Mac",
                "warn",
                f"Running on {f.os} ({f.arch}). Rasva is built for Apple Silicon "
                "Macs; AI engines may not run here.",
            )
        )

    mem_gb = f.total_memory_bytes / GB
    if mem_gb >= 15.5:
        checks.append(Check("memory", "Memory", "pass", f"{mem_gb:.0f} GB unified memory."))
    elif mem_gb >= 7.5:
        checks.append(
            Check(
                "memory",
                "Memory",
                "warn",
                f"{mem_gb:.0f} GB memory. Some engines and image sizes will be unavailable.",
            )
        )
    else:
        checks.append(
            Check(
                "memory",
                "Memory",
                "fail",
                f"{mem_gb:.0f} GB memory is below what local AI engines need.",
            )
        )

    if free_disk_bytes >= needed_free_bytes:
        checks.append(
            Check(
                "disk",
                "Free disk space",
                "pass",
                f"{free_disk_bytes / GB:.0f} GB free on this disk.",
            )
        )
    else:
        checks.append(
            Check(
                "disk",
                "Free disk space",
                "warn",
                f"{free_disk_bytes / GB:.0f} GB free. Rasva's full budget plus space for "
                f"macOS needs about {needed_free_bytes / GB:.0f} GB. You can lower the "
                "budget in Settings.",
            )
        )

    py = sys.version_info
    checks.append(
        Check(
            "python",
            "Python runtime",
            "pass" if py >= (3, 11) else "fail",
            f"Python {py.major}.{py.minor}.{py.micro}.",
        )
    )

    checks.append(
        Check(
            "ffmpeg",
            "Audio tools",
            "pass" if shutil.which("ffmpeg") else "warn",
            "FFmpeg found."
            if shutil.which("ffmpeg")
            else "FFmpeg not found. It will be needed for MP3 export and mixing.",
        )
    )

    accel = []
    if f.mlx_available:
        accel.append("MLX")
    if f.mps_available:
        accel.append("PyTorch (Metal)")
    checks.append(
        Check(
            "accelerators",
            "AI acceleration",
            "pass" if accel else "warn",
            ("Available: " + ", ".join(accel) + ".")
            if accel
            else "No AI framework installed yet. It is installed with your first engine.",
        )
    )

    return {
        "ready": all(c.status != "fail" for c in checks),
        "facts": asdict(f),
        "checks": [asdict(c) for c in checks],
    }
