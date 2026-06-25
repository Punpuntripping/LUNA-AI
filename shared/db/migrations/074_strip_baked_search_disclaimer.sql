-- 074_strip_baked_search_disclaimer.sql
-- Data cleanup (no schema change).
--
-- Context:
--   The AI legal disclaimer used to be baked into every agent_search synthesis
--   body (aggregator appended "\n\n---\n\n<disclaimer>" to content_md). It now
--   renders in the frontend UI beneath every agent output
--   (frontend/components/workspace/AgentOutputDisclaimer.tsx), and the
--   aggregator no longer appends it (DEFAULT_DISCLAIMER_AR = "").
--
--   This strips the exact baked trailer from existing agent_search rows so the
--   disclaimer isn't shown twice (baked body + UI footer).
--
-- Safety:
--   * Matches the EXACT trailer at the very end of content_md only (right()),
--     so legitimate in-body mentions of "محامٍ مرخّص" are untouched.
--   * Verified live (Supabase MCP, 2026-06-21): 172 agent_search rows matched,
--     0 non-agent_search rows matched. Applied via MCP; this file is the record.
--   * Idempotent: re-running matches nothing once stripped.

BEGIN;

WITH s AS (
  SELECT E'\n\n---\n\nهذه المعلومات لأغراض قانونية عامة ولا تُعدّ استشارة قانونية رسمية. للحصول على رأي ملزم ينبغي مراجعة محامٍ مرخّص.'::text AS suf
)
UPDATE public.workspace_items wi
SET content_md = left(wi.content_md, length(wi.content_md) - length(s.suf)),
    updated_at = now()
FROM s
WHERE wi.kind = 'agent_search'
  AND wi.deleted_at IS NULL
  AND wi.content_md IS NOT NULL
  AND right(wi.content_md, length(s.suf)) = s.suf;

COMMIT;
