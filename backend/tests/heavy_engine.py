"""Test-only engine with a declared memory peak, for the memory guard tests.

Behaves like the fault injector; importable by engine workers (tests/ is under
the backend root).
"""

from __future__ import annotations

from rasva.engines.builtin.testing import FaultEngine

GB = 1024**3


class HeavyEngine(FaultEngine):
    descriptor = FaultEngine.descriptor.model_copy(
        update={
            "id": "test-heavy",
            "display_name": "Test heavy engine",
            "approx_peak_memory_bytes": 4 * GB,
        }
    )
