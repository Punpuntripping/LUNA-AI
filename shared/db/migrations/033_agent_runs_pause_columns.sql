-- Migration 033: agent_runs pause/resume columns (Wave 9 Task 13.1 / 13.4).
--
-- Adds the schema needed to pause a Pydantic AI agent run when it issues a
-- DeferredToolCall (e.g. AskUser) and resume it on a later turn after the
-- user supplies an answer. The pre-router checks for an `awaiting_user`
-- run on the conversation and, if present, resumes that run instead of
-- routing the new user message as a fresh turn.
--
-- Changes:
--   1. Extend agent_run_status enum with 'awaiting_user'.
--   2. agent_runs.message_history  — Pydantic AI `result.all_messages_json()`
--                                    bytes, replayed on resume.
--   3. agent_runs.deferred_payload — JSON describing the pending tool call
--                                    (tool name, args, call_id, etc.).
--   4. agent_runs.question_text    — rendered question shown to the user.
--   5. agent_runs.asked_at         — when the question was emitted.
--   6. agent_runs.expires_at       — soft TTL for the pause window.
--   7. Partial index for fast pre-router lookup of pending runs scoped to
--      a conversation.
--   8. RLS UPDATE policy — required for status flips on resume/timeout
--      (Task 13.4: the original 028 migration only added SELECT + INSERT).
--
-- IMPORTANT: PostgreSQL forbids ALTER TYPE ... ADD VALUE inside a
-- transaction block. This migration must therefore NOT be wrapped in
-- BEGIN/COMMIT — run it as-is in the Supabase SQL Editor (which executes
-- each top-level statement in its own implicit transaction). The ADD
-- VALUE statement is placed first, on its own line, so it commits before
-- any subsequent DDL references the new label.
--
-- Dependencies:
--   - 028_agent_runs.sql (agent_runs table + agent_run_status enum)
--   - 007_conversations.sql (conversation_id FK target)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. Extend agent_run_status enum
------------------------------------------------------------------------
-- Must run outside a transaction block; keep on its own statement.
ALTER TYPE public.agent_run_status ADD VALUE IF NOT EXISTS 'awaiting_user';

------------------------------------------------------------------------
-- 2-6. agent_runs columns for pause/resume state
------------------------------------------------------------------------
ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS message_history   BYTEA,
    ADD COLUMN IF NOT EXISTS deferred_payload  JSONB,
    ADD COLUMN IF NOT EXISTS question_text     TEXT,
    ADD COLUMN IF NOT EXISTS asked_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_at        TIMESTAMPTZ;

COMMENT ON COLUMN public.agent_runs.message_history IS
    'Pydantic AI result.all_messages_json() bytes captured at pause; replayed verbatim on resume.';
COMMENT ON COLUMN public.agent_runs.deferred_payload IS
    'JSON describing the pending DeferredToolCall (tool name, args, call_id) used to construct the resume payload.';
COMMENT ON COLUMN public.agent_runs.question_text IS
    'Rendered question text surfaced to the user while the run is awaiting_user.';
COMMENT ON COLUMN public.agent_runs.asked_at IS
    'Timestamp when the awaiting_user question was emitted.';
COMMENT ON COLUMN public.agent_runs.expires_at IS
    'Soft TTL for the pause window; runs past this time may be auto-cancelled by a sweeper.';

------------------------------------------------------------------------
-- 7. Partial index for pre-router pending-run lookup
------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_agent_runs_awaiting
    ON public.agent_runs (conversation_id, asked_at DESC)
    WHERE status = 'awaiting_user';

------------------------------------------------------------------------
-- 8. RLS UPDATE policy
--    Migration 028 only grants SELECT + INSERT.  Task 13 needs UPDATE to
--    flip status from 'awaiting_user' → 'ok'/'error'/'abandoned'/'timeout'
--    and to refresh message_history bytes on chained pauses.
------------------------------------------------------------------------
CREATE POLICY agent_runs_update ON public.agent_runs
    FOR UPDATE
    USING (user_id = (SELECT public.get_current_user_id()))
    WITH CHECK (user_id = (SELECT public.get_current_user_id()));
