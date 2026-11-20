-- Manual language per chapter sentence (PRD A2): the user's choice when
-- detection is wrong. NULL = automatic (each sentence read by its script).
ALTER TABLE segments ADD COLUMN language_override TEXT
    CHECK (language_override IN ('en', 'hi'));
