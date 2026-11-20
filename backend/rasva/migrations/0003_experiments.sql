-- Experiments (PRD §20, A1-010): fixed inputs x candidate engines/settings,
-- with measured metrics (from jobs) and human review scores.

-- Projects created to hold experiment outputs are kept out of the Library.
ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'user'
    CHECK (kind IN ('user', 'experiment'));

CREATE TABLE experiments (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    objective    TEXT NOT NULL DEFAULT '',
    task         TEXT NOT NULL,
    benchmark    TEXT,                       -- preset id, e.g. 'audio-v1', or NULL for custom
    candidates   TEXT NOT NULL DEFAULT '[]', -- [{key, engine_id, settings}]
    usability    TEXT NOT NULL DEFAULT '{}', -- {candidate_key: 1..5}
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'decided', 'archived')),
    decision     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE experiment_runs (
    id             TEXT PRIMARY KEY,
    experiment_id  TEXT NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    case_id        TEXT NOT NULL,
    category       TEXT NOT NULL,
    candidate_key  TEXT NOT NULL,
    engine_id      TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    model_revision TEXT,
    job_id         TEXT REFERENCES jobs (id),
    skip_reason    TEXT,                     -- set when the candidate cannot take this input
    created_at     TEXT NOT NULL,
    UNIQUE (experiment_id, case_id, candidate_key),
    CHECK ((job_id IS NULL) <> (skip_reason IS NULL))
);

CREATE INDEX experiment_runs_experiment ON experiment_runs (experiment_id, case_id);

CREATE TABLE experiment_scores (
    run_id     TEXT NOT NULL REFERENCES experiment_runs (id) ON DELETE CASCADE,
    dimension  TEXT NOT NULL,
    score      INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, dimension)
);
