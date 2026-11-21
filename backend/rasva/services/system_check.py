"""Hardware and environment readiness checks (PRD §8 step 2, P0-004).

Cheap by design, with no heavy imports. AI frameworks live in each engine's own
environment (ADR-0003), never in the app's Python, so acceleration is reported from
the installed engines' environments (a directory check). Whether MPS/MLX actually
work is verified per engine at install time, inside a worker process.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import psutil

from rasva.config import GB
from rasva.engines.contract import SystemFacts

Status = Literal["pass", "warn", "fail"]

# Framework packages an engine environment may hold, and how the check names them.
FRAMEWORKS = (("mlx", "MLX"), ("torch", "PyTorch (Metal)"), ("onnxruntime", "ONNX Runtime"))


@dataclass
class Check:
    id: str
    label: str
    status: Status
    message: str


def engine_frameworks(envs_dir: Path | None) -> dict[str, list[str]]:
    """Framework label -> ids of engines whose environment contains it."""
    found: dict[str, list[str]] = {}
    if envs_dir is None or not envs_dir.is_dir():
        return found
    envs = sorted(p for p in envs_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    for env in envs:
        for site in sorted(env.glob("lib/python3*/site-packages")):
            for module, label in FRAMEWORKS:
                if (site / module).is_dir():
                    found.setdefault(label, []).append(env.name)
            break
    order = [label for _, label in FRAMEWORKS]
    return dict(sorted(found.items(), key=lambda kv: order.index(kv[0])))


def facts(envs_dir: Path | None = None) -> SystemFacts:
    """Host facts. With ``envs_dir``, MLX/PyTorch availability comes from the
    installed engines' environments; without it, from the app's own Python."""
    is_mac = sys.platform == "darwin"
    arch = platform.machine().lower()
    if envs_dir is not None:
        in_envs = engine_frameworks(envs_dir)
        mlx, torch = "MLX" in in_envs, "PyTorch (Metal)" in in_envs
    else:
        mlx = importlib.util.find_spec("mlx") is not None
        torch = importlib.util.find_spec("torch") is not None
    return SystemFacts(
        os="macOS" if is_mac else platform.system(),
        arch=arch,
        total_memory_bytes=psutil.virtual_memory().total,
        apple_silicon=is_mac and arch == "arm64",
        mlx_available=mlx,
        mps_available=is_mac and arch == "arm64" and torch,
    )


def run_checks(
    free_disk_bytes: int,
    needed_free_bytes: int,
    envs_dir: Path | None = None,
    names: Mapping[str, str] | None = None,
) -> dict[str, object]:
    f = facts(envs_dir)
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

    frameworks = engine_frameworks(envs_dir)
    names = names or {}

    def who(ids: list[str]) -> str:
        return ", ".join(names.get(i, i) for i in ids)

    checks.append(
        Check(
            "accelerators",
            "AI acceleration",
            "pass" if frameworks else "warn",
            "Installed with your engines: "
            + "; ".join(f"{label} ({who(ids)})" for label, ids in frameworks.items())
            + "."
            if frameworks
            else "No engine installed yet. Each engine brings its own AI framework "
            "(MLX or PyTorch) when you install it.",
        )
    )

    return {
        "ready": all(c.status != "fail" for c in checks),
        "facts": asdict(f),
        "checks": [asdict(c) for c in checks],
    }
