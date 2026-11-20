"""Home Wi-Fi access and device pairing (ADR-0004, PRD §22)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from rasva.api.deps import DB, Client, LocalOnly, State
from rasva.api.errors import ApiError
from rasva.lan.devices import COOKIE_MAX_AGE, COOKIE_NAME, DeviceStore
from rasva.lan.server import LanError
from rasva.services.settings_store import SettingsStore

router = APIRouter(tags=["devices"])


class PairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(default="Phone", min_length=1, max_length=100)


def _lan(state: State) -> Any:
    if state.lan is None:
        raise ApiError(503, "lan_unavailable", "Home Wi-Fi access is not available.")
    return state.lan


def _status(state: State, conn: DB) -> dict[str, Any]:
    lan = _lan(state)
    pairing = lan.pairing.current()
    return {
        **lan.status(),
        "enabled": bool(SettingsStore(conn).get("lan.enabled")),
        "pairing": ({"code": pairing.code, "expires_at": pairing.expires_at} if pairing else None),
    }


@router.get("/devices/me")
def me(client: Client) -> dict[str, Any]:
    return {"kind": client.get("kind"), "device": client.get("device")}


@router.get("/lan", dependencies=[LocalOnly])
def lan_status(state: State, conn: DB) -> dict[str, Any]:
    return _status(state, conn)


@router.post("/lan/enable", dependencies=[LocalOnly])
async def enable(state: State, conn: DB) -> dict[str, Any]:
    try:
        await _lan(state).start()
    except LanError as exc:
        raise ApiError(409, "lan_failed", str(exc)) from exc
    SettingsStore(conn).set("lan.enabled", True, internal=True)
    return _status(state, conn)


@router.post("/lan/disable", dependencies=[LocalOnly])
async def disable(state: State, conn: DB) -> dict[str, Any]:
    await _lan(state).stop()
    SettingsStore(conn).set("lan.enabled", False, internal=True)
    return _status(state, conn)


@router.post("/devices/pairing", dependencies=[LocalOnly])
def start_pairing(state: State, conn: DB) -> dict[str, Any]:
    lan = _lan(state)
    if not lan.running:
        raise ApiError(409, "lan_off", "Turn on home Wi-Fi access first.")
    lan.pairing.start()
    return _status(state, conn)


@router.delete("/devices/pairing", dependencies=[LocalOnly])
def stop_pairing(state: State, conn: DB) -> dict[str, Any]:
    _lan(state).pairing.stop()
    return _status(state, conn)


@router.get("/devices", dependencies=[LocalOnly])
def list_devices(conn: DB) -> list[dict[str, Any]]:
    return DeviceStore.list(conn)


@router.delete("/devices/{device_id}", status_code=204, dependencies=[LocalOnly])
def revoke(device_id: str, conn: DB) -> None:
    if not DeviceStore.revoke(conn, device_id):
        raise ApiError(404, "device_not_found", "That device is not paired.")


@router.post("/devices/pair", status_code=201)
def pair(body: PairRequest, request: Request, response: Response, state: State, conn: DB,
         client: Client) -> dict[str, Any]:  # fmt: skip
    if client.get("kind") == "local":
        raise ApiError(
            409, "pair_from_device", "Open the pairing link on the device you're adding."
        )
    lan = _lan(state)
    if not lan.pairing.redeem(body.code):
        raise ApiError(
            403, "bad_code", "That code is wrong or has expired. Get a new one on your Mac."
        )
    device, token = DeviceStore.create(
        conn, body.name, request.headers.get("user-agent", ""), client.get("ip", "")
    )
    response.set_cookie(
        COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, secure=True,
        samesite="strict", path="/",
    )  # fmt: skip
    return device
