-- 063_backfill_llm_calls_gap.sql
--
-- Final gap backfill before dropping agent_runs. Migration 059 snapshotted
-- agent_runs → llm_calls on 2026-06-01; the old deployed code kept writing
-- agent_runs until the agent_runs-free build deployed (2026-06-03, commit
-- 36d44b8). Those in-between rows (~15, ~$0.04) have cost in agent_runs but not
-- in llm_calls. Copy them forward so DROP TABLE agent_runs (migration 062)
-- loses no cost history.
--
-- Idempotent: only rows with NO matching llm_calls row (same created_at +
-- user_id — 059 preserved the source created_at) are inserted. Re-running is a
-- no-op, and the new code's llm_calls rows (their own post-deploy created_at)
-- are never matched as duplicates of an old agent_runs row.

INSERT INTO public.llm_calls (
    message_id, conversation_id, user_id, case_id,
    agent, agent_family, subtype, model,
    tokens_in, tokens_out, tokens_reasoning, pages_used, cost_usd,
    outcome, created_at
)
SELECT
    ar.message_id,
    ar.conversation_id,
    ar.user_id,
    ar.case_id,
    CASE
        WHEN ar.subtype = 'ocr_extraction' THEN 'memory.ocr_extraction'
        WHEN ar.subtype IS NOT NULL AND ar.subtype <> '' THEN ar.agent_family::text || '.' || ar.subtype
        ELSE ar.agent_family::text
    END AS agent,
    ar.agent_family::text,
    ar.subtype,
    ar.model_used,
    COALESCE(ar.tokens_in, 0),
    COALESCE(ar.tokens_out, 0),
    COALESCE(ar.tokens_reasoning, 0),
    ar.pages_used,
    COALESCE(ar.cost_usd, 0),
    ar.status::text,
    ar.created_at
FROM public.agent_runs ar
WHERE (ar.cost_usd > 0 OR ar.pages_used > 0)
  AND NOT EXISTS (
    SELECT 1 FROM public.llm_calls l
    WHERE l.created_at = ar.created_at AND l.user_id = ar.user_id
  );
