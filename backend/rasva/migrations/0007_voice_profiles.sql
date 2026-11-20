-- Consented voice profiles (PRD §11, phase A3).

CREATE TABLE voice_profiles (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description          TEXT NOT NULL DEFAULT '',
    language             TEXT NOT NULL CHECK (language IN ('hi', 'en', 'any')),
    reference_asset_id   TEXT NOT NULL REFERENCES assets (id),
    transcript           TEXT NOT NULL,
    transcript_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (transcript_confirmed IN (0, 1)),
    quality              TEXT NOT NULL DEFAULT '{}',
    -- {basis: "self" | "permission", speaker_name, statement, recorded_at, recorded_from}
    consent              TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Consent history outlives deleted profiles; it never holds audio or transcripts.
CREATE TABLE consent_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    event        TEXT NOT NULL CHECK (event IN ('granted', 'deleted')),
    basis        TEXT NOT NULL,
    speaker_name TEXT NOT NULL,
    at           TEXT NOT NULL
);
