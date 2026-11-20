"""`python -m rasva` / `rasva`: start the local server."""

from __future__ import annotations

import ipaddress
import sys

import uvicorn

from rasva.config import Settings


def main() -> int:
    cfg = Settings.from_env()
    try:
        loopback = ipaddress.ip_address(cfg.host).is_loopback
    except ValueError:
        loopback = cfg.host == "localhost"
    if not loopback:
        print(
            "Rasva's main port only listens on this Mac. Turn on home Wi-Fi access in "
            "Settings instead: it adds a separate, encrypted port for paired devices.",
            file=sys.stderr,
        )
        return 2

    from rasva.app import create_app

    uvicorn.run(
        create_app(cfg),
        host=cfg.host,
        port=cfg.port,
        log_config=None,
        access_log=False,  # URLs can contain ids; keep logs minimal
        proxy_headers=False,  # never trust X-Forwarded-For: client IPs drive access rules
        timeout_graceful_shutdown=15,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
