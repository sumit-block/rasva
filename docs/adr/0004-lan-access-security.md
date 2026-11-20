# ADR-0004: Home Wi-Fi access through a local CA and paired devices

- Status: Accepted and implemented (phase A5)
- Date: 2026-11-20
- PRD refs: §22, NFR-05

## Context

The PRD requires authenticated, encrypted access from paired phones and laptops
on home Wi-Fi, with no public exposure. Browsers only allow microphone
recording, which voice enrollment needs, from a secure context (HTTPS). A
self-signed certificate shows a full-page warning on phones. A publicly trusted
certificate needs a public domain and DNS.

## Decision

**Listeners.**
- The main port (8765) serves only the Mac itself, unchanged.
- Turning on "Allow devices on my Wi-Fi" starts two more servers inside the
  same process:
  - **HTTPS on 8766**, serving the full app to paired devices.
  - **Plain HTTP on 8767**, serving only an instruction page and the *public*
    CA certificate (an iOS `.mobileconfig`, or PEM for other devices).
- The setting persists and is restored on restart. Turning it off closes the
  listening ports at once, with a 2-second grace period for open connections.

**Certificates** (`rasva/lan/certs.py`).
- One CA per installation: EC P-256, valid 10 years, named "Rasva Studio Local
  CA XXXX".
- It signs a server certificate valid for 397 days, covering `<host>.local`
  and the Mac's private IPv4 addresses. The certificate is reissued
  automatically when the addresses change.
- Both private keys are encrypted (PKCS#8). The passphrases are stored in the
  macOS Keychain, falling back to an owner-only file where the Keychain isn't
  available.

**Access rules on the Wi-Fi port** (`rasva/security.py`):
- Clients must be on a private network; public addresses are refused even if a
  router forwards the port.
- `Host` must be this Mac's local name or address, which blocks DNS rebinding.
- State-changing requests must carry a same-site `Origin`.
- Every API call needs a paired-device cookie (`HttpOnly`, `Secure`,
  `SameSite=Strict`, 400 days). Only a SHA-256 hash of each token is stored.
- `X-Forwarded-*` headers are never trusted.

**Pairing.**
- The Mac shows a QR code and a 6-digit code: one at a time, valid for 5
  minutes, single use, and five wrong guesses end the session.
- The phone scans the QR code, installs and trusts the CA, opens the HTTPS
  link and enters the code (prefilled from the link).
- The Mac confirms the new device by name.
- Devices can be removed at any time, which locks them out immediately.

**Owner-only actions.** Turning Wi-Fi access on or off, pairing, the device
list and storage settings are refused from paired devices.

## Residual risks, accepted and documented

- **Pairing-window interception.** An attacker actively intercepting traffic
  on the home network during the 5-minute window could substitute their own CA
  on the plain-HTTP setup page. The setup page and the Mac both show the CA's
  SHA-256 fingerprint for comparison with the phone's certificate details.
- **Keychain prompt argument.** On macOS the Keychain passphrase is passed to
  `/usr/bin/security` as a command-line argument, so another process run by
  the same user could see it for the moment that command runs.
- **Profile shown as "Unverified".** The iOS profile is unsigned, so iOS labels
  it that way. The instructions say so.
- **Mac sleep.** Access only works while the Mac is awake and Rasva is
  running; closing the lid usually stops it. The UI states this.

## Alternatives rejected

- **Self-signed certificate per launch:** recurring warnings train users to
  click through them.
- **Plain HTTP with a token:** unencrypted on the home network (fails NFR-05),
  and the microphone can't be used.
- **Public tunnel or UPnP port forwarding:** the PRD forbids public exposure.
