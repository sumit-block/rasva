"""Memory readings for the heavy-job guard (PRD §24).

``available`` is psutil's figure. On macOS it is free plus inactive pages: memory
macOS can usually hand to a new process quickly, though reclaiming inactive
pages can still mean compressing or swapping some of them. It does not count
compressed or purgeable memory, and it is not the same signal as the
memory-pressure graph in Activity Monitor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psutil

from rasva.config import GB

Level = Literal["ok", "tight", "low"]

# Default room left over after an engine's measured peak, for macOS, the browser
# and Rasva's own server. An assumption, not a measurement; configurable.
DEFAULT_SAFETY_MARGIN_BYTES = 1 * GB


@dataclass(frozen=True)
class MemoryReading:
    total_bytes: int
    available_bytes: int


def read() -> MemoryReading:
    """Current system memory. Module-level so tests can replace it."""
    v = psutil.virtual_memory()
    return MemoryReading(total_bytes=int(v.total), available_bytes=int(v.available))


def level(reading: MemoryReading) -> Level:
    """Plain three-step summary for the UI, from the share of memory available."""
    share = reading.available_bytes / reading.total_bytes if reading.total_bytes else 0.0
    if share >= 0.25:
        return "ok"
    if share >= 0.10:
        return "tight"
    return "low"


def format_gb(n: int) -> str:
    """``10 GB`` / ``9.5 GB``: one decimal only when it matters."""
    value = round(n / GB, 1)
    return f"{value:.0f} GB" if value == int(value) else f"{value:.1f} GB"
