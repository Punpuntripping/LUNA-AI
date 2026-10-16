-- 176_user_style_profiles.sql
-- Per-user writing-style profile for the unified responder (language mirroring).
--
-- Written by the Layer-4 memory agent `style_profiler`
-- (agents/memory/style_profiler/) asynchronously after a turn, every
-- STYLE_PROFILE_EVERY_N user messages (default 8) or when no row exists. Read by
-- the responder and rendered at the END of its dynamic tail (prefix-cache safe).
--
-- The profile is IDENTIFIER-FREE by construction (PDPL + وضع السرية): register,
-- preferred answer length, counsel-vs-party, verbatim legal terms, short notes.
-- The agent's validator strips digits, emails and name-like entries before the
-- row is written. No facts, names, numbers or case details are ever stored here.
--
--   profile           jsonb  — the validated StyleProfile (register, length_pref,
--                              writes_as, terms[], notes)
--   rendered_md       text   — the ≤600-char block injected into the responder
--   source_msg_count  int    — the user's total message count when this profile
--                              was computed; the refresh threshold compares
--                              against it
--
-- Access: owner-only SELECT. No client INSERT / UPDATE / DELETE policy — only
-- the service role (backend) writes. Account deletion: ON DELETE CASCADE from
-- users(user_id) — the purge's terminal auth-user delete removes the users row
-- and this row with it.
--
-- Plan: .claude/plans/unified_responder.md §7 (phase P4).
-- Idempotent: re-runs are safe.

CREATE TABLE IF NOT EXISTS public.user_style_profiles (
    user_id           UUID PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    profile           JSONB NOT NULL,
    rendered_md       TEXT NOT NULL,
    source_msg_count  INTEGER NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.user_style_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_style_profiles_select ON public.user_style_profiles;
CREATE POLICY user_style_profiles_select ON public.user_style_profiles FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- Deliberately NO insert / update / delete policies: with RLS on, anon and
-- authenticated clients cannot write; the service role bypasses RLS.

COMMENT ON TABLE public.user_style_profiles IS
    'Identifier-free writing-style profile per user (register, length, counsel/party, verbatim legal terms). Written by the style_profiler memory agent (service role); owner-only read. Plan: unified_responder.md §7.';
