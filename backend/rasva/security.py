"""Access guard for the loopback and home Wi-Fi listeners (ADR-0004).

The main port serves the Mac itself only; the Wi-Fi port (when enabled) serves
paired devices on the private network. Even on loopback, two browser-borne
attacks apply:

- DNS rebinding: a hostile site resolves its own name to 127.0.0.1. Blocked by
  requiring the Host header to be a loopback name.
- Cross-site requests: a hostile page POSTs to localhost. Blocked by rejecting
  state-changing requests whose Origin is not a loopback origin.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
from collections.abc import Iterable
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (
        b"content-security-policy",
        b"default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
        b"style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
        b"font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    ),
]


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _is_loopback_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


# Paths a not-yet-paired device may load on the Wi-Fi listener: the app shell
# (no data in it) and the pairing endpoint. Every other API call needs a device.
_LAN_PUBLIC_API = {"/api/v1/devices/pair", "/api/v1/devices/me", "/api/v1/system/health"}


def _lan_public(path: str) -> bool:
    return path in _LAN_PUBLIC_API or not path.startswith("/api/")


class LocalOnlyGuard:
    """Access policy for both listeners.

    Loopback listener (the Mac itself): loopback clients, loopback Host names,
    same-site Origin for writes. Wi-Fi listener (only while enabled): private-
    network clients, this Mac's LAN names, same-site Origin, and a paired-device
    cookie for every API call. The client kind is recorded in ``scope["state"]``
    for endpoints that are restricted to the Mac.
    """

    def __init__(
        self, app: ASGIApp, extra_hosts: Iterable[str] = (), trust_test_client: bool = False
    ):
        self.app = app
        self.allowed_hosts = LOOPBACK_NAMES | {h.lower() for h in extra_hosts}
        self.trust_test_client = trust_test_client

    async def _deny(
        self, send: Send, message: str, status: int = 403, code: str = "forbidden"
    ) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), *SECURITY_HEADERS],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else ""
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        app_state = getattr(getattr(scope.get("app"), "state", None), "rasva", None)
        lan = getattr(app_state, "lan", None)

        if lan is not None and lan.is_lan_scope(scope):
            client_info = await self._check_lan(scope, send, lan, app_state, client_host, headers)
            if client_info is None:
                return
        else:
            if not (
                _is_loopback_ip(client_host)
                or (self.trust_test_client and client_host == "testclient")
            ):
                await self._deny(send, "Rasva Studio only accepts connections from this Mac.")
                return
            if _hostname(headers.get("host", "")) not in self.allowed_hosts:
                await self._deny(send, "Unrecognised host name.")
                return
            if not self._origin_ok(scope, headers, self.allowed_hosts):
                await self._deny(send, "Cross-site request blocked.")
                return
            client_info = {"kind": "local"}

        scope.setdefault("state", {})["rasva_client"] = client_info

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    h for h in SECURITY_HEADERS if h[0] not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _origin_ok(
        scope: Scope, headers: dict[str, str], allowed: set[str] | frozenset[str]
    ) -> bool:
        origin = headers.get("origin")
        if origin and scope.get("method", "GET") not in SAFE_METHODS:
            return (urlsplit(origin).hostname or "").lower() in allowed
        return True

    async def _check_lan(
        self, scope: Scope, send: Send, lan: Any, app_state: Any, client_host: str,
        headers: dict[str, str],
    ) -> dict[str, Any] | None:  # fmt: skip
        from rasva.db import connect
        from rasva.lan.devices import COOKIE_NAME
        from rasva.lan.server import client_allowed

        if not client_allowed(client_host, lan.settings.lan_test_mode):
            await self._deny(send, "Only devices on your home network can connect.")
            return None
        allowed = lan.allowed_hosts()
        if _hostname(headers.get("host", "")) not in allowed:
            await self._deny(send, "Unrecognised host name.")
            return None
        if not self._origin_ok(scope, headers, allowed):
            await self._deny(send, "Cross-site request blocked.")
            return None
        cookies = SimpleCookie()
        with contextlib.suppress(CookieError):
            cookies.load(headers.get("cookie", ""))
        token = cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else None
        conn = connect(app_state.settings.paths.db_file)
        try:
            device = lan.devices.authenticate(conn, token, client_host)
        finally:
            conn.close()
        if device is None:
            if _lan_public(scope.get("path", "/")):
                return {"kind": "unpaired", "ip": client_host}
            await self._deny(
                send, "This device isn't paired with your Mac yet.", status=401, code="not_paired"
            )
            return None
        return {"kind": "device", "device": device, "ip": client_host}
