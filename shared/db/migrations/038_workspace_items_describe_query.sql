-- Migration 038: workspace_items.describe_query
--
-- Full-redesign Phase A (see agents_reports/half_baked_prompts/full_redesign.md §1.1).
--
-- Adds a column carrying the router-emitted query-description for this dispatch.
-- The planner reads this in subsequent turns to understand prior task intent
-- without parsing agent_runs.input_summary[:500] truncations.
--
-- Pre-migration rows have NULL describe_query. Loaders MUST tolerate this and
-- fall back to workspace_items.title. Do NOT add NOT NULL in a future migration
-- without first backfilling — every consumer of this column treats NULL as
-- "router did not emit a query description for this dispatch".
--
-- The CHECK cap of 4000 chars is a guardrail against a runaway-paraphrase loop
-- (Pydantic catches it at the router field, but a direct INSERT or backfill
-- script could bypass that). Typical describe_query is ≤150 words (~1.5k chars).
--
-- Idempotent.

ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS describe_query TEXT
        CHECK (describe_query IS NULL OR char_length(describe_query) <= 4000);

COMMENT ON COLUMN public.workspace_items.describe_query IS
    'The describe_query string the router wrote for this dispatch — a '
    'description of the question the artifact answers. Persisted at publish '
    'time. Used by the planner to read prior task intent without parsing '
    'input_summary truncations. CHECK cap is a runaway-LLM guardrail — '
    'typical describe_query is ≤150 words (~1.5k chars).';
