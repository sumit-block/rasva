-- Image versions (PRD phase I4): results of the same request are versions of one image.
-- A group holds the versions; the favourite is the one the owner chose to keep. With no
-- favourite, the newest completed version counts as current. Grouping holds no prompts.

CREATE TABLE image_groups (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    task             TEXT NOT NULL,
    -- image.edit: SHA-256 of the sorted reference asset ids and roles, so edits of the
    -- same reference photos join one group. NULL for groups made explicitly.
    request_key      TEXT,
    favourite_job_id TEXT REFERENCES jobs (id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX image_groups_project ON image_groups (project_id, updated_at DESC);
CREATE UNIQUE INDEX image_groups_request ON image_groups (project_id, task, request_key)
    WHERE request_key IS NOT NULL;

CREATE TABLE image_versions (
    job_id     TEXT PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    group_id   TEXT NOT NULL REFERENCES image_groups (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE INDEX image_versions_group ON image_versions (group_id, created_at);

-- Existing images (phase I0) each become a group of one, so cleanup never treats
-- them as older versions of something else.
INSERT INTO image_groups (id, project_id, task, request_key, favourite_job_id, created_at, updated_at)
    SELECT 'imgg_' || substr(id, 5), project_id, task, NULL, NULL, created_at, updated_at
    FROM jobs WHERE task LIKE 'image.%';

INSERT INTO image_versions (job_id, group_id, created_at)
    SELECT id, 'imgg_' || substr(id, 5), created_at FROM jobs WHERE task LIKE 'image.%'
