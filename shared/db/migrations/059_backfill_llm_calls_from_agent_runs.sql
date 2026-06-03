-- 059_backfill_llm_calls_from_agent_runs.sql
--
-- ONE-SHOT history preservation. The cost/pages reads (quota rehydrate_from_pg,
-- OCR lifetime count) were repointed from agent_runs to llm_calls, but
-- historical billing data (pre-migration 058) lives only in agent_runs. Copy it
-- forward so the repointed reads are lossless AND so history survives the
-- eventual DROP of agent_runs (Phase 4).
--
-- Aggregate→per-call granularity is lost for historical rows (one agent_runs
-- row = one llm_calls row), which is fine: quota only sums cost_usd / pages_used.
--
-- Safe to run exactly once: llm_calls was empty at backfill time (verified), and
-- the new per-call writers only produce rows with created_at AFTER deploy, so
-- there is no overlap window with these historical rows.

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
WHERE (ar.cost_usd IS NOT NULL AND ar.cost_usd > 0)
   OR (ar.pages_used IS NOT NULL AND ar.pages_used > 0);
