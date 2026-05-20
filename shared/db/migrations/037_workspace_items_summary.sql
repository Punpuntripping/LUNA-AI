-- Migration 037: Agent-facing summary columns on workspace_items.
--
-- Wave 10 — artifact_summarizer.
-- Adds a short Arabic markdown summary written by the artifact_summarizer
-- agent right after a workspace item is published. The summary's audience is
-- DOWNSTREAM AGENTS (router, planner, follow-up turns), not the end user —
-- it tells the next agent what this item covers and what it does NOT cover,
-- so the next decision (re-query, route elsewhere, stop) can be made without
-- re-reading the full content_md.
--
-- Columns:
--   - summary               : Arabic markdown blob (free-form, no length cap).
--   - summary_updated_at    : when the summary was last (re)generated.
--   - summary_source_length : char-length of the content_md the summary was
--                             produced from; cheap cache-invalidation check
--                             so a future re-summarizer can skip unchanged
--                             rows.
--
-- Telemetry (model_used, tokens_in/out/reasoning, fallback_used) lives in the
-- existing ``metadata`` jsonb under the key ``artifact_summary`` — no new
-- columns needed for that.
--
-- Dependencies:
--   - 026_workspace_items.sql (creates workspace_items via rename)
--
-- This migration is idempotent.

ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS summary               text,
    ADD COLUMN IF NOT EXISTS summary_updated_at    timestamptz,
    ADD COLUMN IF NOT EXISTS summary_source_length integer;

COMMENT ON COLUMN public.workspace_items.summary IS
    'Agent-facing summary: tells the next agent what this item covers and '
    'does not cover. Written by agents/memory/artifact_summarizer after publish.';
COMMENT ON COLUMN public.workspace_items.summary_updated_at IS
    'Timestamp of the last summary write.';
COMMENT ON COLUMN public.workspace_items.summary_source_length IS
    'char_length(content_md) at the moment the summary was generated. '
    'Used to skip re-summarization when content_md is unchanged.';
