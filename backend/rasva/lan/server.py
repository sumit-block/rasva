"""Home Wi-Fi listeners (ADR-0004).

When the owner turns on Wi-Fi access, two extra servers run inside the app's
event loop, sharing its state:

- HTTPS (default port 8766): the full app, for paired devices only.
- Setup HTTP (default 8767): one instruction page plus the *public* CA
  certificate, so a phone can trust the HTTPS server before using it. Nothing
  else is served over plain HTTP.

Both reject clients that are not on a private network, and requests whose Host
header is not this Mac's local name or address (DNS-rebinding defence).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import ipaddress
import logging
from collections.abc import Iterator
from typing import Any

import uvicorn

from rasva.config import Settings
from rasva.lan.certs import LocalCA, SecretStore, lan_addresses, local_hostname
from rasva.lan.devices import DeviceStore, Pairing

log = logging.getLogger(__name__)

START_TIMEOUT_S = 10.0
STOP_GRACE_S = 2.0


class LanError(Exception):
    """Wi-Fi access could not start; the message is safe to show users."""


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that leaves signal handling to the main server."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def client_allowed(ip: str, test_mode: bool) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return test_mode
    return addr.is_private and not addr.is_multicast


def hostname_of(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LanManager:
    def __init__(self, settings: Settings, app: Any) -> None:
        self.settings = settings
        self.app = app
        store = SecretStore(settings.paths.secrets, settings.use_keychain)
        self.ca = LocalCA(settings.paths.secrets, store)
        self.pairing = Pairing()
        self.devices = DeviceStore()
        self.hostnames: list[str] = []
        self.addresses: list[str] = []
        self.error: str | None = None
        self._servers: list[_EmbeddedServer] = []
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def port(self) -> int:
        return self.settings.lan_port

    @property
    def running(self) -> bool:
        return bool(self._servers) and all(s.started for s in self._servers)

    def allowed_hosts(self) -> set[str]:
        return {h.lower() for h in self.hostnames} | set(self.addresses)

    def is_lan_scope(self, scope: dict[str, Any]) -> bool:
        server = scope.get("server")
        return bool(self._servers) and bool(server) and server[1] == self.settings.lan_port

    # ---- lifecycle

    async def start(self) -> None:
        if self._servers:
            return
        self.error = None
        hostnames = [local_hostname()]
        addresses = lan_addresses()
        if self.settings.lan_test_mode:
            hostnames.append("localhost")
            addresses.append("127.0.0.1")
        if not addresses:
            self.error = "This Mac isn't connected to a Wi-Fi or wired network."
            raise LanError(self.error)
        self.hostnames, self.addresses = hostnames, addresses
        await asyncio.to_thread(self.ca.ensure_server, hostnames, addresses)

        common = {"lifespan": "off", "log_config": None, "access_log": False,
                  "proxy_headers": False, "server_header": False,
                  "host": self.settings.lan_bind_host}  # fmt: skip
        https = _EmbeddedServer(
            uvicorn.Config(
                self.app,
                port=self.settings.lan_port,
                ssl_certfile=str(self.ca.paths.server_cert),
                ssl_keyfile=str(self.ca.paths.server_key),
                ssl_keyfile_password=self.ca.server_passphrase.decode(),
                **common,
            )
        )
        setup = _EmbeddedServer(
            uvicorn.Config(SetupApp(self), port=self.settings.lan_setup_port, **common)
        )
        self._servers = [https, setup]
        self._tasks = [asyncio.create_task(s.serve(), name="rasva-lan") for s in self._servers]
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT_S
        while not all(s.started for s in self._servers):
            if any(t.done() for t in self._tasks) or asyncio.get_running_loop().time() > deadline:
                await self.stop()
                self.error = (
                    f"Couldn't open ports {self.settings.lan_port} and "
                    f"{self.settings.lan_setup_port}. Another app may be using them."
                )
                raise LanError(self.error)
            await asyncio.sleep(0.05)
        log.info("home Wi-Fi access on", extra={"addresses": self.addresses})

    async def stop(self) -> None:
        """Stop listening at once; give open connections a short grace period.

        Listening sockets close first, so no new device can connect. Idle
        keep-alive TLS connections would otherwise hold shutdown for up to 30 s
        while the TLS close handshake waits on a phone that is not reading.
        """
        for s in self._servers:
            s.should_exit = True
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=STOP_GRACE_S)
            if pending:
                for s in self._servers:
                    s.force_exit = True
                await asyncio.wait(pending, timeout=STOP_GRACE_S)
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        self._servers, self._tasks = [], []
        self.pairing.stop()

    def status(self) -> dict[str, Any]:
        ca_ready = self.ca.paths.ca_cert.exists()
        names = self.hostnames or [local_hostname()]
        addresses = self.addresses or lan_addresses()
        return {
            "running": self.running,
            "error": self.error,
            "port": self.settings.lan_port,
            "setup_port": self.settings.lan_setup_port,
            "hostname": names[0],
            "addresses": addresses,
            "urls": [f"https://{a}:{self.settings.lan_port}" for a in [names[0], *addresses]],
            "ca_fingerprint": self.ca.fingerprint() if ca_ready else None,
            "ca_name": self.ca.ca_certificate().subject.rfc4514_string() if ca_ready else None,
        }


# ------------------------------------------------------------------ setup page


_SETUP_SCRIPT = """
(function () {
  var m = /code=(\\d{6})/.exec(location.hash);
  var link = document.getElementById("open");
  var url = "https://" + location.hostname + ":" + link.dataset.port + "/pair";
  if (m) url += "#code=" + m[1];
  link.href = url;
  var ua = navigator.userAgent;
  var ios = /iPhone|iPad|iPod/.test(ua) || (ua.indexOf("Mac") > -1 && "ontouchend" in document);
  document.getElementById(ios ? "android" : "ios").hidden = true;
})();
"""
_SETUP_HASH = base64.b64encode(hashlib.sha256(_SETUP_SCRIPT.encode()).digest()).decode()

_SETUP_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to Rasva Studio</title>
<style>
body{{font:17px/1.5 -apple-system,system-ui,sans-serif;background:#16151b;color:#f3f2f7;margin:0;padding:24px}}
main{{max-width:34rem;margin:auto}} h1{{font-size:1.5rem}} li{{margin:.75rem 0}}
a.btn{{display:inline-block;background:#b9a6ff;color:#1b1530;padding:.75rem 1rem;border-radius:.6rem;
text-decoration:none;font-weight:600;margin:.25rem 0}} code{{font-size:.8rem;word-break:break-all;color:#c9c6d6}}
.muted{{color:#b4b1c2;font-size:.9rem}}
</style></head><body><main>
<h1>Connect this device to Rasva Studio</h1>
<p class="muted">Your Mac does the work. This device just needs to trust it once.</p>
<ol>
<li><b>Install the Rasva certificate.</b><br>
<span id="ios"><a class="btn" href="/rasva-ca.mobileconfig">Download for iPhone/iPad</a><br>
<span class="muted">Then open Settings &rarr; Profile Downloaded &rarr; Install. After that:
Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings &rarr; turn on
&ldquo;{ca_name}&rdquo;.</span></span>
<span id="android"><a class="btn" href="/rasva-ca.crt">Download certificate</a><br>
<span class="muted">Open it and install as a &ldquo;CA certificate&rdquo; (Settings &rarr; Security
&rarr; Encryption &amp; credentials &rarr; Install a certificate).</span></span></li>
<li><b>Check it&rsquo;s really your Mac&rsquo;s.</b> The certificate&rsquo;s SHA-256 fingerprint must match
the one shown on your Mac:<br><code>{fingerprint}</code></li>
<li><b>Open Rasva securely</b> and enter the 6-digit code from your Mac.<br>
<a class="btn" id="open" data-port="{port}" href="#">Open Rasva Studio</a></li>
</ol>
<p class="muted">Only devices you pair can use Rasva. You can remove a device any time in Settings on
your Mac.</p>
</main><script>{script}</script></body></html>
"""


class SetupApp:
    """Minimal ASGI app for the plain-HTTP setup port."""

    def __init__(self, lan: LanManager) -> None:
        self.lan = lan

    async def _send(
        self, send: Any, status: int, body: bytes, ctype: str, extra: list | None = None
    ) -> None:
        headers = [
            (b"content-type", ctype.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"cache-control", b"no-store"),
            (b"content-security-policy",
             f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{_SETUP_HASH}'; "
             "frame-ancestors 'none'".encode()),
            *(extra or []),
        ]  # fmt: skip
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        client = (scope.get("client") or ("", 0))[0]
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if (
            not client_allowed(client, self.lan.settings.lan_test_mode)
            or hostname_of(headers.get("host", "")) not in self.lan.allowed_hosts()
        ):
            await self._send(send, 403, b"Forbidden", "text/plain")
            return
        if scope.get("method") not in ("GET", "HEAD"):
            await self._send(send, 405, b"Method not allowed", "text/plain")
            return
        path = scope.get("path", "/")
        ca = self.lan.ca
        if path == "/":
            cert = ca.ca_certificate()
            cn = cert.subject.rfc4514_string().split(",")[0].removeprefix("CN=")
            page = _SETUP_PAGE.format(
                ca_name=html.escape(cn), fingerprint=html.escape(ca.fingerprint()),
                port=self.lan.settings.lan_port, script=_SETUP_SCRIPT,
            )  # fmt: skip
            await self._send(send, 200, page.encode(), "text/html; charset=utf-8")
        elif path == "/rasva-ca.mobileconfig":
            await self._send(send, 200, ca.mobileconfig(), "application/x-apple-aspen-config",
                             [(b"content-disposition", b'attachment; filename="rasva-ca.mobileconfig"')])  # fmt: skip
        elif path == "/rasva-ca.crt":
            await self._send(send, 200, ca.ca_pem(), "application/x-x509-ca-cert",
                             [(b"content-disposition", b'attachment; filename="rasva-ca.crt"')])  # fmt: skip
        else:
            await self._send(send, 404, b"Not found", "text/plain")
