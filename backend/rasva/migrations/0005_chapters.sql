-- Long-form narration (PRD §12-13, phase A4): chapters split into segments;
-- every generation of a segment is a take (non-destructive versions);
-- music/effect tracks for the mix.

CREATE TABLE chapters (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    script      TEXT NOT NULL DEFAULT '',
    settings    TEXT NOT NULL DEFAULT '{}',   -- {engine_id, voice_id, speed, use_dictionary}
    mix         TEXT NOT NULL DEFAULT '{}',   -- {duck_db, ...}
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX chapters_project ON chapters (project_id, position);

CREATE TABLE segments (
    id              TEXT PRIMARY KEY,
    chapter_id      TEXT NOT NULL REFERENCES chapters (id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    paragraph       INTEGER NOT NULL,
    text            TEXT NOT NULL,
    pause_after_s   REAL NOT NULL,
    current_take_id TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (chapter_id, position)
);

CREATE TABLE takes (
    id          TEXT PRIMARY KEY,
    segment_id  TEXT NOT NULL REFERENCES segments (id) ON DELETE CASCADE,
    job_id      TEXT NOT NULL REFERENCES jobs (id),
    settings    TEXT NOT NULL,              -- snapshot used for this take
    -- Text this take speaks; kept so takes survive re-segmentation by text match.
    text        TEXT NOT NULL,
    reviewed    INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    created_at  TEXT NOT NULL
);

CREATE INDEX takes_segment ON takes (segment_id, created_at);

CREATE TABLE mix_tracks (
    id            TEXT PRIMARY KEY,
    chapter_id    TEXT NOT NULL REFERENCES chapters (id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('music', 'effect')),
    asset_id      TEXT NOT NULL REFERENCES assets (id),
    -- Start: at a segment (by position) plus an offset, or at a time when no segment.
    anchor_segment INTEGER,
    offset_s      REAL NOT NULL DEFAULT 0,
    gain_db       REAL NOT NULL DEFAULT -18 CHECK (gain_db BETWEEN -60 AND 6),
    fade_in_s     REAL NOT NULL DEFAULT 0 CHECK (fade_in_s >= 0),
    fade_out_s    REAL NOT NULL DEFAULT 0 CHECK (fade_out_s >= 0),
    loop          INTEGER NOT NULL DEFAULT 0 CHECK (loop IN (0, 1)),
    duck          INTEGER NOT NULL DEFAULT 1 CHECK (duck IN (0, 1)),
    created_at    TEXT NOT NULL
);

CREATE INDEX mix_tracks_chapter ON mix_tracks (chapter_id);
