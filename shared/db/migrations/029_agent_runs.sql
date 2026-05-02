-- Migration 029: Agent runs audit log (Wave 9 Task 1).
--
-- Append-only record of every agent invocation. Insert-only from the
-- application layer; SELECT scoped to the owning user via RLS. No UPDATE
-- or DELETE policies — runs are immutable history.
--
-- Includes Logfire correlation columns (trace_id, span_id) so each row can
-- be pivoted to the matching trace in the observability backend wired up
-- in commit 44002d2.
--
-- Dependencies:
--   - 018_enums_agent.sql            (agent_family_enum — already includes 'memory')
--   - 003_users.sql                  (users.user_id)
--   - 007_conversations.sql          (conversations.conversation_id)
--   - 004_lawyer_cases.sql           (lawyer_cases.case_id)
--   - 008_messages.sql               (messages.message_id)
--   - 026_workspace_items.sql        (workspace_items.item_id)
--   - 016_rls.sql                    (get_current_user_id() helper)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. Status enum
------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE agent_run_status AS ENUM ('ok', 'error', 'timeout');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

------------------------------------------------------------------------
-- 2. Ensure agent_family_enum has 'memory'
-- (018_enums_agent.sql already declares it; this is a defensive no-op
--  for environments where the enum was created from an older snapshot.)
------------------------------------------------------------------------
ALTER TYPE agent_family_enum ADD VALUE IF NOT EXISTS 'memory';

------------------------------------------------------------------------
-- 3. agent_runs table
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.agent_runs (
    run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES public.users(user_id),
    conversation_id   UUID NOT NULL REFERENCES public.conversations(conversation_id),
    case_id           UUID REFERENCES public.lawyer_cases(case_id),
    message_id        UUID REFERENCES public.messages(message_id),
    agent_family      agent_family_enum NOT NULL,
    subtype           TEXT,
    status            agent_run_status NOT NULL DEFAULT 'ok',
    input_summary     TEXT,
    output_item_id    UUID REFERENCES public.workspace_items(item_id),
    duration_ms       INTEGER,
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    model_used        TEXT,
    per_phase_stats   JSONB DEFAULT '{}'::jsonb,
    error             JSONB,
    -- Logfire correlation (commit 44002d2 wired Logfire). Cheap to populate
    -- from the active span at run completion; lets the audit table pivot
    -- straight to traces.
    trace_id          TEXT,
    span_id           TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

------------------------------------------------------------------------
-- 4. Indexes
------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
    ON public.agent_runs (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_family
    ON public.agent_runs (user_id, agent_family, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_runs_case
    ON public.agent_runs (case_id, created_at DESC)
    WHERE case_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_runs_item
    ON public.agent_runs (output_item_id)
    WHERE output_item_id IS NOT NULL;

------------------------------------------------------------------------
-- 5. RLS — SELECT + INSERT only. Runs are immutable.
------------------------------------------------------------------------
ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY agent_runs_select ON public.agent_runs
        FOR SELECT USING (user_id = public.get_current_user_id());
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY agent_runs_insert ON public.agent_runs
        FOR INSERT WITH CHECK (user_id = public.get_current_user_id());
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

------------------------------------------------------------------------
-- Comments
------------------------------------------------------------------------
COMMENT ON TABLE public.agent_runs IS
    'Append-only audit log of every agent invocation. Immutable: SELECT + INSERT only via RLS.';
COMMENT ON COLUMN public.agent_runs.trace_id IS
    'Logfire trace id captured from the active span at run completion. Enables pivot from audit row to full trace.';
COMMENT ON COLUMN public.agent_runs.span_id IS
    'Logfire span id of the agent run root span.';
COMMENT ON COLUMN public.agent_runs.per_phase_stats IS
    'Per-phase timing/token breakdown (e.g., {"retrieval_ms": 120, "synthesis_ms": 800}). Shape is agent-specific.';
COMMENT ON COLUMN public.agent_runs.output_item_id IS
    'workspace_items row produced by this run (search result, draft, note). NULL for runs that produced no artifact.';
