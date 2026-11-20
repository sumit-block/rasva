-- Rasva Studio initial schema (PRD §25). JSON columns hold TEXT-encoded objects.

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    module      TEXT NOT NULL CHECK (module IN ('audio', 'image')),
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX projects_updated ON projects (updated_at DESC);

CREATE TABLE assets (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    -- reference: user-supplied original; output: generated; import: licensed music/effects
    kind          TEXT NOT NULL CHECK (kind IN ('reference', 'output', 'import')),
    media_type    TEXT NOT NULL,
    original_name TEXT NOT NULL,
    rel_path      TEXT NOT NULL UNIQUE,
    size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256        TEXT NOT NULL,
    metadata      TEXT NOT NULL DEFAULT '{}',
    -- Protected assets are never removed by automatic cleanup (PRD §23).
    protected     INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
    job_id        TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX assets_project ON assets (project_id, created_at DESC);

CREATE TABLE models (
    id               TEXT PRIMARY KEY,
    engine_id        TEXT NOT NULL,
    source           TEXT NOT NULL,
    revision         TEXT NOT NULL,
    license          TEXT NOT NULL,
    license_class    TEXT NOT NULL
                     CHECK (license_class IN ('permissive', 'open-weight', 'restricted', 'unknown')),
    install_state    TEXT NOT NULL
                     CHECK (install_state IN ('installing', 'installed', 'failed', 'removing')),
    installed_bytes  INTEGER NOT NULL DEFAULT 0,
    validated_at     TEXT,
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE jobs (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    task           TEXT NOT NULL,
    engine_id      TEXT NOT NULL,
    state          TEXT NOT NULL CHECK (state IN (
                       'draft', 'queued', 'downloading', 'preparing', 'loading_model',
                       'processing', 'finalizing', 'completed', 'failed', 'canceled',
                       'interrupted')),
    inputs         TEXT NOT NULL DEFAULT '{}',
    outputs        TEXT NOT NULL DEFAULT '[]',
    progress       REAL CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1)),
    stage_detail   TEXT,
    error_code     TEXT,
    error_message  TEXT,
    metrics        TEXT NOT NULL DEFAULT '{}',
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    retry_of       TEXT REFERENCES jobs (id),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT
);

CREATE INDEX jobs_state_created ON jobs (state, created_at);
CREATE INDEX jobs_project ON jobs (project_id, created_at DESC);

-- Append-only audit trail of state transitions.
CREATE TABLE job_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    state    TEXT NOT NULL,
    detail   TEXT,
    at       TEXT NOT NULL
);

CREATE INDEX job_events_job ON job_events (job_id, id);

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
