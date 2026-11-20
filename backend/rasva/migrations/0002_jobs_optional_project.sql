-- System jobs (model install/remove) belong to no project. SQLite cannot drop
-- NOT NULL in place, so rebuild the table (https://sqlite.org/lang_altertable.html#otheralter).
-- The migration runner disables foreign keys around this and runs
-- PRAGMA foreign_key_check before committing.

CREATE TABLE jobs_new (
    id             TEXT PRIMARY KEY,
    project_id     TEXT REFERENCES projects (id) ON DELETE RESTRICT,
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
    finished_at    TEXT,
    -- Project jobs must name a project; only system tasks may omit it.
    CHECK (project_id IS NOT NULL OR task LIKE 'model.%')
);

INSERT INTO jobs_new SELECT
    id, project_id, task, engine_id, state, inputs, outputs, progress, stage_detail,
    error_code, error_message, metrics, cancel_requested, retry_of, created_at,
    updated_at, started_at, finished_at
FROM jobs;

DROP TABLE jobs;

ALTER TABLE jobs_new RENAME TO jobs;

CREATE INDEX jobs_state_created ON jobs (state, created_at);
CREATE INDEX jobs_project ON jobs (project_id, created_at DESC);
CREATE INDEX jobs_engine_state ON jobs (engine_id, state);

CREATE UNIQUE INDEX models_engine ON models (engine_id);
