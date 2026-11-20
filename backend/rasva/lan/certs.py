"""Local certificate authority for home Wi-Fi access (ADR-0004).

One CA per installation signs a short-lived server certificate for this Mac's
local name and LAN addresses. Phones install the CA once; after that the
browser shows a normal padlock and microphone recording works (it requires a
secure context).

Private keys are encrypted at rest. The passphrase lives in the macOS Keychain
when available; elsewhere (development, tests) in an owner-only file.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import ipaddress
import os
import plistlib
import secrets
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 3650
# Apple platforms reject TLS server certificates valid for more than 398 days.
SERVER_DAYS = 397
RENEW_BEFORE = dt.timedelta(days=30)
KEYCHAIN_SERVICE = "Rasva Studio"


# ------------------------------------------------------------------ secrets


class SecretStore:
    """Passphrases for the encrypted private keys."""

    def __init__(self, directory: Path, use_keychain: bool | None = None) -> None:
        self._dir = directory
        self._keychain = sys.platform == "darwin" if use_keychain is None else use_keychain

    def get_or_create(self, name: str) -> bytes:
        if self._keychain:
            found = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                 "-a", name, "-w"],
                capture_output=True, text=True, check=False,
            )  # fmt: skip
            if found.returncode == 0 and found.stdout.strip():
                return found.stdout.strip().encode()
            value = secrets.token_urlsafe(32)
            # `security` reads the password from argv; the window in which another
            # local process could see it is the life of this short command.
            added = subprocess.run(
                ["/usr/bin/security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
                 "-a", name, "-w", value],
                capture_output=True, check=False,
            )  # fmt: skip
            if added.returncode == 0:
                return value.encode()
            # Keychain unavailable (e.g. locked, headless): fall back to a file.
        path = self._dir / f"{name}.secret"
        if path.exists():
            return path.read_bytes().strip()
        value = secrets.token_urlsafe(32).encode()
        _write_private(path, value)
        return value


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


# ------------------------------------------------------------------ addresses


def local_hostname() -> str:
    name = socket.gethostname().split(".")[0] or "rasva"
    return f"{name}.local"


def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this machine (no loopback, no link-local)."""
    found = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(a.address)
            if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                found.append(a.address)
    return sorted(set(found))


# ------------------------------------------------------------------ CA


@dataclass(frozen=True)
class CertPaths:
    directory: Path

    @property
    def ca_cert(self) -> Path:
        return self.directory / "ca.pem"

    @property
    def ca_key(self) -> Path:
        return self.directory / "ca.key.pem"

    @property
    def server_cert(self) -> Path:
        return self.directory / "server.pem"

    @property
    def server_key(self) -> Path:
        return self.directory / "server.key.pem"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class LocalCA:
    def __init__(self, directory: Path, store: SecretStore) -> None:
        self.paths = CertPaths(directory)
        self._store = store

    @property
    def ca_passphrase(self) -> bytes:
        return self._store.get_or_create("ca-key")

    @property
    def server_passphrase(self) -> bytes:
        return self._store.get_or_create("server-key")

    # ---- CA

    def ensure_ca(self) -> x509.Certificate:
        if self.paths.ca_cert.exists() and self.paths.ca_key.exists():
            return self.ca_certificate()
        key = ec.generate_private_key(ec.SECP256R1())
        short = uuid.uuid4().hex[:4].upper()
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"Rasva Studio Local CA {short}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Rasva Studio (this Mac only)"),
        ])  # fmt: skip
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )  # fmt: skip
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .sign(key, hashes.SHA256())
        )
        _write_private(
            self.paths.ca_key,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(self.ca_passphrase),
            ),
        )
        _write_private(self.paths.ca_cert, cert.public_bytes(serialization.Encoding.PEM))
        # A new CA invalidates any server certificate signed by an old one.
        self.paths.server_cert.unlink(missing_ok=True)
        return cert

    def ca_certificate(self) -> x509.Certificate:
        return x509.load_pem_x509_certificate(self.paths.ca_cert.read_bytes())

    def ca_pem(self) -> bytes:
        return self.paths.ca_cert.read_bytes()

    def fingerprint(self) -> str:
        """SHA-256 of the CA certificate, as shown on iPhone/Android detail screens."""
        digest = self.ca_certificate().fingerprint(hashes.SHA256()).hex().upper()
        return " ".join(digest[i : i + 2] for i in range(0, len(digest), 2))

    # ---- server certificate

    def server_covers(self, hostnames: list[str], ips: list[str]) -> bool:
        if not (self.paths.server_cert.exists() and self.paths.server_key.exists()):
            return False
        cert = x509.load_pem_x509_certificate(self.paths.server_cert.read_bytes())
        if cert.issuer != self.ca_certificate().subject:
            return False
        if cert.not_valid_after_utc - _now() < RENEW_BEFORE:
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = set(san.get_values_for_type(x509.DNSName))
        addrs = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
        return set(hostnames) <= dns and set(ips) <= addrs

    def ensure_server(self, hostnames: list[str], ips: list[str]) -> bool:
        """Issue a server certificate for these names if needed. Returns True if reissued."""
        self.ensure_ca()
        if self.server_covers(hostnames, ips):
            return False
        ca_cert = self.ca_certificate()
        ca_key = serialization.load_pem_private_key(
            self.paths.ca_key.read_bytes(), password=self.ca_passphrase
        )
        assert isinstance(ca_key, ec.EllipticCurvePrivateKey)
        key = ec.generate_private_key(ec.SECP256R1())
        now = _now()
        sans: list[x509.GeneralName] = [x509.DNSName(h) for h in dict.fromkeys(hostnames)]
        sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in dict.fromkeys(ips)]
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )  # fmt: skip
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write_private(
            self.paths.server_key,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(self.server_passphrase),
            ),
        )
        _write_private(self.paths.server_cert, cert.public_bytes(serialization.Encoding.PEM))
        return True

    # ---- device profiles

    def mobileconfig(self) -> bytes:
        """An iOS/iPadOS configuration profile that installs the CA (unsigned)."""
        cert = self.ca_certificate()
        der = cert.public_bytes(serialization.Encoding.DER)
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        uid = str(uuid.UUID(hashlib.sha256(der).hexdigest()[:32]))
        profile = {
            "PayloadContent": [{
                "PayloadCertificateFileName": "rasva-ca.cer",
                "PayloadContent": der,
                "PayloadDescription": (
                    "Lets this device connect securely to Rasva Studio on your Mac."
                ),
                "PayloadDisplayName": cn,
                "PayloadIdentifier": f"studio.rasva.ca.{uid}",
                "PayloadType": "com.apple.security.root",
                "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_OID, uid)),
                "PayloadVersion": 1,
            }],
            "PayloadDescription": "Installs the certificate Rasva Studio uses on your home Wi-Fi.",
            "PayloadDisplayName": f"Rasva Studio ({cn})",
            "PayloadIdentifier": f"studio.rasva.profile.{uid}",
            "PayloadRemovalDisallowed": False,
            "PayloadType": "Configuration",
            "PayloadUUID": uid,
            "PayloadVersion": 1,
        }  # fmt: skip
        return plistlib.dumps(profile)
