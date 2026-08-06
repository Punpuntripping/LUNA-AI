-- 115_users_profession.sql
-- Date: 2026-08-05
--
-- Purpose:
--   The «اتعرف على ريحان» onboarding gains a profession step («وش أقرب وصف
--   لك؟») — a 2×2 card grid (قانوني / رائد أعمال / مختص / فرد) plus a
--   full-width «أفضل عدم الإجابة» row. The answer is a segmentation signal
--   stored on the users row itself (NOT the preferences JSONB — one source,
--   queryable by plain SQL).
--
--     * profession_group — NULL   = never asked (the prompt gate: every
--                                   pre-115 user is NULL, so each existing
--                                   user is asked exactly once).
--                          'declined' = deliberately chose not to answer, or
--                                   dismissed the prompt (never nag again).
--                          else   = one of the four group slugs.
--     * profession_label — optional finer segment, only ever set for the
--                          'specialist' and 'individual' groups (chip pick or
--                          free-typed «أخرى» text). The other two groups are
--                          single-tap answers with no sub-options.
--
--   Writes go exclusively through the backend service role
--   (PATCH /api/v1/auth/profession) — the frontend never touches this table
--   directly, so no RLS policy change (RLS gates rows, not columns; the
--   self-row policies from 016/017 already cover reads).
--
-- Dependencies:
--   - 003_users.sql (users table)
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + guarded ADD CONSTRAINT.

------------------------------------------------------------------------
-- 1. Profession columns on public.users.
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS profession_group TEXT,
    ADD COLUMN IF NOT EXISTS profession_label TEXT;

COMMENT ON COLUMN public.users.profession_group IS
    'Onboarding profession segment. NULL = never asked; ''declined'' = chose '
    'not to answer; else legal | entrepreneur | specialist | individual. '
    'Written only via PATCH /api/v1/auth/profession (service role).';

COMMENT ON COLUMN public.users.profession_label IS
    'Optional finer segment under profession_group — chip pick or free-typed '
    '«أخرى» text. Only ever set for specialist/individual; NULL otherwise.';

------------------------------------------------------------------------
-- 2. Value guards. Backend validates too — these are the last line.
------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_profession_group_check'
          AND conrelid = 'public.users'::regclass
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_profession_group_check CHECK (
                profession_group IS NULL
                OR profession_group IN
                    ('legal', 'entrepreneur', 'specialist', 'individual', 'declined')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_profession_label_length_check'
          AND conrelid = 'public.users'::regclass
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_profession_label_length_check CHECK (
                profession_label IS NULL
                OR char_length(profession_label) BETWEEN 1 AND 120
            );
    END IF;
END $$;
