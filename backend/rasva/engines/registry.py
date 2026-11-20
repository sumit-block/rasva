"""Engine registry. Maps engine ids to import paths and resolves worker interpreters."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

from rasva.engines.contract import Engine, EngineDescriptor

BUILTIN: dict[str, str] = {
    "diagnostic-tone": "rasva.engines.builtin.tone:ToneEngine",
    "kokoro-82m": "rasva.engines.kokoro:KokoroEngine",
    "chatterbox-multilingual": "rasva.engines.chatterbox:ChatterboxEngine",
    "indicf5": "rasva.engines.indicf5:IndicF5Engine",
    "audio-mixer": "rasva.engines.builtin.mixer:MixerEngine",
}
TEST_ONLY: dict[str, str] = {
    "test-fault": "rasva.engines.builtin.testing:FaultEngine",
}


def load_class(import_path: str) -> type[Engine]:
    module_name, _, attr = import_path.partition(":")
    cls = getattr(importlib.import_module(module_name), attr)
    if not (isinstance(cls, type) and issubclass(cls, Engine)):
        raise TypeError(f"{import_path} is not an Engine")
    return cls


@dataclass(frozen=True)
class RegisteredEngine:
    import_path: str
    descriptor: EngineDescriptor

    def python_executable(self, envs_dir: Path) -> str:
        """Interpreter for this engine's worker.

        Engines with conflicting dependencies get an isolated venv at
        ``envs/<engine-id>/`` (docs/adr/0003-per-engine-environments.md).
        Built-in engines run on the app interpreter.
        """
        candidate = envs_dir / self.descriptor.id / "bin" / "python"
        return str(candidate) if candidate.exists() else sys.executable


class EngineRegistry:
    def __init__(self, include_test_engines: bool = False, extra: tuple[str, ...] = ()) -> None:
        paths = dict(BUILTIN)
        if include_test_engines:
            paths.update(TEST_ONLY)
        for import_path in extra:
            paths[load_class(import_path).descriptor.id] = import_path
        self._engines: dict[str, RegisteredEngine] = {}
        for engine_id, path in paths.items():
            cls = load_class(path)
            if cls.descriptor.id != engine_id:
                raise ValueError(f"Engine id mismatch for {path}")
            self._engines[engine_id] = RegisteredEngine(path, cls.descriptor)

    def get(self, engine_id: str) -> RegisteredEngine | None:
        return self._engines.get(engine_id)

    def all(self) -> list[RegisteredEngine]:
        return list(self._engines.values())

    def for_task(self, task: str) -> list[RegisteredEngine]:
        return [e for e in self._engines.values() if task in e.descriptor.tasks]
