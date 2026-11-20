"""Local-only access guard.

Until LAN pairing ships (phase A5, docs/adr/0004-lan-access-security.md) the API
serves the Mac itself only. Even on loopback, two browser-borne attacks apply:

- DNS rebinding: a hostile site resolves its own name to 127.0.0.1. Blocked by
  requiring the Host header to be a loopback name.
- Cross-site requests: a hostile page POSTs to localhost. Blocked by rejecting
  state-changing requests whose Origin is not a loopback origin.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Iterable
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


class LocalOnlyGuard:
    def __init__(
        self, app: ASGIApp, extra_hosts: Iterable[str] = (), trust_test_client: bool = False
    ):
        self.app = app
        self.allowed_hosts = LOOPBACK_NAMES | {h.lower() for h in extra_hosts}
        self.trust_test_client = trust_test_client

    async def _deny(self, send: Send, message: str) -> None:
        body = json.dumps({"error": {"code": "forbidden", "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
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
        if not (
            _is_loopback_ip(client_host) or (self.trust_test_client and client_host == "testclient")
        ):
            await self._deny(send, "Rasva Studio only accepts connections from this Mac.")
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if _hostname(headers.get("host", "")) not in self.allowed_hosts:
            await self._deny(send, "Unrecognised host name.")
            return

        method = scope.get("method", "GET")
        origin = headers.get("origin")
        if origin and method not in SAFE_METHODS:
            parsed = urlsplit(origin)
            if (parsed.hostname or "").lower() not in self.allowed_hosts:
                await self._deny(send, "Cross-site request blocked.")
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    h for h in SECURITY_HEADERS if h[0] not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
