-- Paired devices for home Wi-Fi access (ADR-0004). Only a SHA-256 of each
-- device token is stored; the token itself lives in the device's cookie.

CREATE TABLE devices (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    token_hash   TEXT NOT NULL UNIQUE,
    user_agent   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT,
    last_ip      TEXT
);
