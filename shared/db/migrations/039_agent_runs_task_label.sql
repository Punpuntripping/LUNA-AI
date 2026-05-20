-- Migration 039: agent_runs.task_label
--
-- Full-redesign Phase A (see agents_reports/half_baked_prompts/full_redesign.md §1.2).
--
-- Adds a short, indexable label for the dispatched task. Emitted by the router
-- on DispatchAgent. Used by the planner to enumerate prior tasks without
-- reading full describe_query text. Same string is reused as
-- workspace_items.title at publish time, so labels and titles always agree.
--
-- No new index needed — the existing idx_agent_runs_conversation on
-- (conversation_id, created_at DESC) from migration 029 already covers the
-- listing queries.
--
-- The CHECK cap of 200 chars is a generous guardrail (Pydantic enforces 80 at
-- the DispatchAgent.task_label field) — exists to catch a runaway INSERT
-- bypassing the router contract.
--
-- Idempotent.

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS task_label TEXT
        CHECK (task_label IS NULL OR char_length(task_label) <= 200);

COMMENT ON COLUMN public.agent_runs.task_label IS
    'Short Arabic label for the dispatched task (≤80 chars, typical 30–60). '
    'Emitted by the router on DispatchAgent. Used by the planner to enumerate '
    'prior tasks without reading full describe_query text. CHECK cap of 200 '
    'is a guardrail — actual cap is 80 enforced at the Pydantic field.';
