"""Quality presets: resolve a preset id into the engine's real settings."""

from __future__ import annotations

from typing import Any

from rasva.engines.contract import EngineDescriptor


class PresetError(ValueError):
    pass


def apply_preset(descriptor: EngineDescriptor, inputs: dict[str, Any]) -> dict[str, Any]:
    """Merge the chosen preset's settings into ``inputs``.

    A preset owns the settings it defines (they override what the request
    carries); choose no preset to set those values by hand. A preset for an
    engine that has none is an error, not silently ignored.
    """
    preset_id = inputs.get("preset")
    if not preset_id:
        return {k: v for k, v in inputs.items() if k != "preset"}
    preset = next((p for p in descriptor.presets if p.id == preset_id), None)
    if preset is None:
        if not descriptor.presets:
            raise PresetError(f"{descriptor.display_name} has no quality presets.")
        raise PresetError(f"{descriptor.display_name} has no preset called {preset_id!r}.")
    rest = {k: v for k, v in inputs.items() if k != "preset"}
    return {**rest, **preset.settings}
