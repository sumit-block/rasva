from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rasva.api.deps import DB, State
from rasva.engines.registry import load_class
from rasva.services import system_check

router = APIRouter(tags=["models"])


@router.get("/models")
def list_models(state: State, conn: DB) -> list[dict[str, Any]]:
    facts = system_check.facts()
    installed = {
        r["engine_id"]: dict(r)
        for r in conn.execute("SELECT engine_id, install_state, installed_bytes FROM models")
    }
    out = []
    for engine in state.registry.all():
        d = engine.descriptor
        if d.experimental:
            continue
        compat = load_class(engine.import_path)().check_compatibility(facts)
        if not d.requires_install:
            install_state = "built_in"
        else:
            install_state = installed.get(d.id, {}).get("install_state", "not_installed")
        out.append(
            {
                **d.model_dump(),
                "install_state": install_state,
                "compatibility": compat.model_dump(),
            }
        )
    return out
