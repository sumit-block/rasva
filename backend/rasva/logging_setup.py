"""Structured local logs (P0-014).

Privacy rule (PRD §27-28): never log request bodies, scripts, transcripts, file
contents or secrets. Log identifiers (job/project/asset ids) instead.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if getattr(h, "_rasva", False):
            root.removeHandler(h)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "rasva.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler._rasva = True  # type: ignore[attr-defined]
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console._rasva = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    root.addHandler(console)
    # httpx logs every request URL at INFO. Download redirects carry signed, expiring
    # URLs; keep them (and the noise) out of the logs. Failures are logged by Rasva.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


ENGINE_LOG_MAX_BYTES = 5 * 1024 * 1024


def rotate_if_large(path: Path, max_bytes: int = ENGINE_LOG_MAX_BYTES) -> None:
    """Keep one previous engine log; engine stderr is appended across runs."""
    try:
        if path.stat().st_size > max_bytes:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except FileNotFoundError:
        pass
