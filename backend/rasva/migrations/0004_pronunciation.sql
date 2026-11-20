-- Pronunciation dictionary (PRD §10.3, A2-005) and a 'system' project kind for
-- internal outputs such as pronunciation previews. Changing the kind CHECK needs
-- a table rebuild; the runner disables foreign keys and verifies them after.

CREATE TABLE projects_new (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    module      TEXT NOT NULL CHECK (module IN ('audio', 'image')),
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'user' CHECK (kind IN ('user', 'experiment', 'system'))
);

INSERT INTO projects_new SELECT
    id, name, module, description, status, settings, created_at, updated_at, kind
FROM projects;

DROP TABLE projects;

ALTER TABLE projects_new RENAME TO projects;

CREATE INDEX projects_updated ON projects (updated_at DESC);

CREATE TABLE pronunciation_entries (
    id             TEXT PRIMARY KEY,
    -- NULL = applies to every project; otherwise only to this project.
    project_id     TEXT REFERENCES projects (id) ON DELETE CASCADE,
    written_forms  TEXT NOT NULL,             -- JSON list, e.g. ["कृष्ण", "Krishna"]
    spoken         TEXT NOT NULL CHECK (length(spoken) BETWEEN 1 AND 200),
    phonetic_hint  TEXT NOT NULL DEFAULT '',  -- informational, e.g. IAST "Kṛṣṇa"
    language       TEXT NOT NULL CHECK (language IN ('hi', 'en', 'any')),
    whole_word     INTEGER NOT NULL DEFAULT 1 CHECK (whole_word IN (0, 1)),
    case_sensitive INTEGER NOT NULL DEFAULT 0 CHECK (case_sensitive IN (0, 1)),
    approved       INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),
    examples       TEXT NOT NULL DEFAULT '[]',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX pronunciation_project ON pronunciation_entries (project_id);
