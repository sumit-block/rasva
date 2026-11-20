"""Home Wi-Fi access (ADR-0004): certificates, pairing and the access rules,
exercised against a real server with real TLS."""

import datetime as dt
import plistlib
import socket
import ssl
import threading
import time
from dataclasses import replace

import httpx
import pytest
import uvicorn
from cryptography import x509

from rasva.app import create_app
from rasva.lan import devices as devices_mod
from rasva.lan.certs import LocalCA, SecretStore
from rasva.lan.devices import COOKIE_NAME, Pairing
from rasva.lan.server import client_allowed


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ units


def test_client_ranges():
    assert client_allowed("192.168.1.20", False)
    assert client_allowed("10.0.0.5", False)
    assert client_allowed("fd00::5", False)
    assert not client_allowed("8.8.8.8", False)
    assert not client_allowed("127.0.0.1", False)  # loopback uses the other listener
    assert client_allowed("127.0.0.1", True)
    assert not client_allowed("not-an-ip", True)


def test_certificates(tmp_path):
    ca = LocalCA(tmp_path / "s", SecretStore(tmp_path / "s", use_keychain=False))
    assert ca.ensure_server(["mac.local"], ["192.168.1.5"]) is True
    assert ca.ensure_server(["mac.local"], ["192.168.1.5"]) is False  # still covers
    assert ca.ensure_server(["mac.local"], ["192.168.1.9"]) is True  # address changed
    cert = x509.load_pem_x509_certificate(ca.paths.server_cert.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "mac.local" in san.get_values_for_type(x509.DNSName)
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc) <= dt.timedelta(days=398)
    for path in (ca.paths.ca_key, ca.paths.server_key):
        assert b"ENCRYPTED" in path.read_bytes()
        assert path.stat().st_mode & 0o077 == 0
    profile = plistlib.loads(ca.mobileconfig())
    assert profile["PayloadContent"][0]["PayloadType"] == "com.apple.security.root"
    # The CA is created once and reused.
    first = ca.ca_pem()
    ca.ensure_server(["mac.local"], ["192.168.1.10"])
    assert ca.ca_pem() == first


def test_pairing_codes(monkeypatch):
    p = Pairing()
    assert not p.redeem("123456")  # no session
    s = p.start()
    wrong = "000000" if s.code != "000000" else "111111"
    for _ in range(devices_mod.MAX_ATTEMPTS):
        assert not p.redeem(wrong)
    assert not p.redeem(s.code)  # locked after five wrong guesses
    s = p.start()
    assert p.redeem(s.code) and not p.redeem(s.code)  # single use
    s = p.start()
    monkeypatch.setattr(devices_mod.time, "time", lambda: s.expires_at + 1)
    assert not p.redeem(s.code)  # expired


# ------------------------------------------------------------------ end to end


class Running:
    def __init__(self, settings):
        self.settings = settings
        self.app = create_app(settings)
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=settings.port, log_config=None, lifespan="on",
        ))  # fmt: skip
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(15)


@pytest.fixture
def lan_settings(settings):
    return replace(
        settings, port=free_port(), lan_port=free_port(), lan_setup_port=free_port(),
        lan_bind_host="127.0.0.1", lan_test_mode=True, use_keychain=False,
    )  # fmt: skip


def wait_for_port(port, up=True, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                if up:
                    return
        except OSError:
            if not up:
                return
        time.sleep(0.05)
    raise AssertionError(f"port {port} {'never opened' if up else 'still open'}")


def test_pair_and_use_over_https(lan_settings, tmp_path):
    s = lan_settings
    mac = httpx.Client(base_url=f"http://127.0.0.1:{s.port}/api/v1")
    with Running(s):
        assert mac.get("/lan").json()["running"] is False
        status = mac.post("/lan/enable").json()
        assert status["running"] and status["enabled"] and "127.0.0.1" in status["addresses"]
        wait_for_port(s.lan_port)

        # Setup page over plain HTTP: instructions and the public CA only.
        setup = httpx.get(f"http://127.0.0.1:{s.lan_setup_port}/")
        assert setup.status_code == 200 and status["ca_fingerprint"] in setup.text
        assert "script-src 'sha256-" in setup.headers["content-security-policy"]
        pem = httpx.get(f"http://127.0.0.1:{s.lan_setup_port}/rasva-ca.crt").content
        assert b"BEGIN CERTIFICATE" in pem and b"PRIVATE" not in pem
        assert httpx.get(f"http://127.0.0.1:{s.lan_setup_port}/api/v1/projects").status_code == 404
        ca_file = tmp_path / "ca.pem"
        ca_file.write_bytes(pem)

        # The phone trusts the CA, so TLS verifies without warnings.
        phone = httpx.Client(base_url=f"https://127.0.0.1:{s.lan_port}/api/v1",
                             verify=ssl.create_default_context(cafile=str(ca_file)))  # fmt: skip
        r = phone.get("/projects")
        assert r.status_code == 401 and r.json()["error"]["code"] == "not_paired"
        assert phone.get("/devices/me").json()["kind"] == "unpaired"
        assert phone.post("/devices/pair", json={"code": "000000", "name": "x"}).status_code == 403

        code = mac.post("/devices/pairing").json()["pairing"]["code"]
        assert mac.post("/devices/pair", json={"code": code}).status_code == 409  # not from the Mac
        r = phone.post("/devices/pair", json={"code": code, "name": "Sumit's iPhone"})
        assert r.status_code == 201, r.text
        cookie = r.headers["set-cookie"]
        assert COOKIE_NAME in cookie and "HttpOnly" in cookie and "Secure" in cookie
        assert "samesite=strict" in cookie.lower()
        assert phone.post("/devices/pair", json={"code": code, "name": "again"}).status_code == 403

        # Paired: normal use works; owner-only actions do not.
        assert phone.get("/projects").status_code == 200
        assert phone.get("/devices/me").json()["device"]["name"] == "Sumit's iPhone"
        assert phone.get("/lan").status_code == 403
        assert phone.get("/devices").status_code == 403
        assert phone.post("/devices/pairing").status_code == 403
        assert (
            phone.patch("/settings", json={"storage.budget_bytes": 10 * 1024**3}).status_code == 403
        )
        assert phone.patch("/settings", json={"ui.mode": "advanced"}).status_code == 200
        assert phone.get("/system/status").json()["client"]["kind"] == "device"

        # DNS rebinding and cross-site writes are refused on the Wi-Fi port too.
        assert phone.get("/projects", headers={"Host": "evil.example"}).status_code == 403
        r = phone.post("/projects", json={"name": "x", "module": "audio"},
                       headers={"Origin": "https://evil.example"})  # fmt: skip
        assert r.status_code == 403

        # Revoking the device locks it out immediately.
        listed = mac.get("/devices").json()
        assert len(listed) == 1 and listed[0]["name"] == "Sumit's iPhone"
        assert mac.delete(f"/devices/{listed[0]['id']}").status_code == 204
        assert phone.get("/projects").status_code == 401

        # The Mac's own port never accepted LAN rules, and still works.
        assert mac.get("/projects").status_code == 200

        assert mac.post("/lan/disable").json()["running"] is False
        wait_for_port(s.lan_port, up=False)
        wait_for_port(s.lan_setup_port, up=False)


def test_lan_access_survives_restart(lan_settings):
    s = lan_settings
    with Running(s):
        httpx.post(f"http://127.0.0.1:{s.port}/api/v1/lan/enable")
    with Running(s):
        wait_for_port(s.lan_port)
        assert httpx.get(f"http://127.0.0.1:{s.port}/api/v1/lan").json()["running"] is True
