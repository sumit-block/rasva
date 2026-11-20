# ADR-0004: Home Wi-Fi access stays off until pairing and TLS ship (phase A5)

- Status: Proposed (recommendation recorded; decision due at the start of A5)
- Date: 2026-11-20
- PRD refs: §22, NFR-05

## Context

The PRD requires authenticated, encrypted access from paired phones and laptops.
Browsers only allow microphone recording, which voice enrollment needs, from a
secure context (HTTPS). A self-signed certificate shows a full-page warning on
phones. A publicly trusted certificate needs a public domain name and DNS.

## Current state (A0)

- The server binds `127.0.0.1` only and refuses to start on any other address.
- `LocalOnlyGuard` rejects:
  - clients that are not on loopback,
  - requests whose `Host` is not a loopback name (DNS-rebinding defence),
  - state-changing requests whose `Origin` is cross-site (CSRF defence).
- `lan.enabled` cannot be changed through the settings API.

## Recommendation for A5

1. When LAN mode is first enabled, generate a local certificate authority (CA)
   unique to this install. Store its private key in the macOS Keychain, not on
   disk in plain text. Issue a server certificate for the Mac's `.local` name
   and its current LAN IP address.
2. Pair each device by scanning a QR code with a short-lived one-time code. The
   device then installs the CA profile once (an iOS configuration profile or an
   Android user CA) and receives a long-lived session token, scoped to that
   device and revocable from Settings.
3. Keep HTTP on loopback for the Mac itself, so the owner gets no certificate
   friction there.
4. Keep the same Host and Origin checks, extended to the paired names.

## Alternatives

- **Self-signed certificate per launch:** rejected; it shows recurring warnings
  and trains users to click through them.
- **Plain HTTP with a pairing token:** rejected; traffic on home Wi-Fi is
  unencrypted (fails NFR-05), and the microphone cannot be used.
- **Public tunnel:** rejected; the PRD explicitly forbids public exposure.
