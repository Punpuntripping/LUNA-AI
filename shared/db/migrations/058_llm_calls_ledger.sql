-- 058_llm_calls_ledger.sql
--
-- Flat, append-only per-call token/cost ledger. ONE row per LLM call (and per
-- OCR extraction). Replaces the unreliable cost columns on agent_runs, where
-- cost was captured *voluntarily* by each specialist threading per_phase_stats
-- up through its SpecialistResult — a contract the writer never honoured
-- (tokens_in/out hardcoded to 0), so every writing-family row billed $0.
--
-- llm_calls is reliable BY CONSTRUCTION: the row is written from the single
-- chokepoint every model call already passes through (agents/utils/tracking.py
-- run_tracked / AgentSpan.record_run), buffered per-turn in a ContextVar
-- (agents/utils/usage_sink.py) and flushed once at the dispatch boundary.
-- deep_search (which aggregates usage manually, not via run_tracked) feeds the
-- same buffer from its per_phase_stats per_model breakdown.
--
-- Division of responsibility after this migration:
--   llm_calls  → token/cost ledger (source of truth for cost + quota settle)
--   agent_runs → pause/resume state + run identity/outcome ONLY
--
-- RLS: enabled with NO policies — service-role-only, exactly like agent_runs.
-- The backend writes with the service key (bypasses RLS); end users never read
-- this table directly (the usage API reads roll-ups from the quota store).

CREATE TABLE IF NOT EXISTS public.llm_calls (
    call_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Join key: the user message that triggered the turn (mirrors how
    -- agent_runs.message_id was attributed). Nullable for background jobs
    -- (artifact_summarizer webhook) that have no triggering message.
    message_id       uuid        REFERENCES public.messages(message_id) ON DELETE SET NULL,
    conversation_id  uuid        NOT NULL,
    user_id          uuid        NOT NULL,
    case_id          uuid,
    -- Stage label, e.g. 'writer.execute', 'writer_planner.decide',
    -- 'deep_search.reg_search', 'memory.summarize', 'memory.ocr_extraction',
    -- 'router.classify'. Free text (not an enum) so new agents add rows with
    -- zero schema churn.
    agent            text        NOT NULL,
    agent_family     text,
    subtype          text,
    -- The model that ACTUALLY responded (FallbackModel may swap off the slot's
    -- primary on a 4xx/5xx). Drives cost via shared.pricing.
    model            text,
    tokens_in        integer     NOT NULL DEFAULT 0,
    tokens_out       integer     NOT NULL DEFAULT 0,
    tokens_reasoning integer     NOT NULL DEFAULT 0,
    -- Subset of tokens_in served from the provider prompt cache (billed at the
    -- cached rate). Captured from usage.cache_read_tokens.
    tokens_cached    integer     NOT NULL DEFAULT 0,
    -- OCR only — pages processed (drives the OCR quota meter).
    pages_used       integer,
    cost_usd         numeric     NOT NULL DEFAULT 0,
    requests         integer     NOT NULL DEFAULT 1,
    duration_ms      integer,
    outcome          text        NOT NULL DEFAULT 'ok',
    created_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.llm_calls IS
    'Per-call token/cost ledger. One row per LLM call / OCR extraction. Source of truth for cost + quota. Written via agents/utils/usage_sink.py.';

-- Rollups: per-message cost (SELECT sum(cost_usd) ... GROUP BY message_id),
-- per-conversation, per-user/day. Index the join keys.
CREATE INDEX IF NOT EXISTS idx_llm_calls_message_id      ON public.llm_calls (message_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_conversation_id ON public.llm_calls (conversation_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_user_created    ON public.llm_calls (user_id, created_at DESC);

ALTER TABLE public.llm_calls ENABLE ROW LEVEL SECURITY;
-- No policies on purpose: service-role-only, matching agent_runs.

-- ── Retire the dead audit columns on agent_runs ──────────────────────────────
-- trace_id / span_id were meant to join agent_runs ↔ Logfire, but the
-- hydration never worked: 0 / 206 rows over 14 days carried a trace_id. Drop
-- them rather than keep pretending the join exists. Logfire is queried by
-- conversation_id / message_id directly.
ALTER TABLE public.agent_runs DROP COLUMN IF EXISTS trace_id;
ALTER TABLE public.agent_runs DROP COLUMN IF EXISTS span_id;
