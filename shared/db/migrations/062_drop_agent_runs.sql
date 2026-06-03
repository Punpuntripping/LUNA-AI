-- 062_drop_agent_runs.sql
--
-- ⚠️ DO NOT APPLY UNTIL THE agent_runs-free CODE IS DEPLOYED AND BAKED. ⚠️
--
-- The currently-deployed backend still INSERTs/UPDATEs agent_runs on every turn.
-- Dropping the table before that code ships breaks the live app. This migration
-- is the FINAL gate of the agent_runs → llm_calls + paused_runs migration:
--
--   Phase 0  llm_calls ledger + sink (migration 058)                    [done]
--   Phase 1  quota rehydrate + OCR count read llm_calls + backfill (059)[done]
--   Phase 2  paused_runs; pause/resume moved off agent_runs (060)       [done]
--   Phase 3  llm_calls.run_id pause↔resume linkage (061)                [done]
--   Phase 4  delete agents/runs.py + all writers, THEN this drop        [code done]
--
-- Pre-apply checklist (run AFTER deploy, BEFORE applying this):
--   1. Confirm no app code references agent_runs (grep returns only migrations).
--   2. Dual-read parity: sum(llm_calls.cost_usd) ≈ historical sum(agent_runs.cost_usd)
--      for the overlap window (the 059 backfill made them equal at cutover).
--   3. Smoke test a full turn + a pause→resume cycle on the deployed build.
--
-- What this drops:
--   - user_cost_daily VIEW  — selects from agent_runs; app-unused (only in SQL
--     migration files). Cost reporting now derives from llm_calls.
--   - agent_runs TABLE.
--   - agent_run_status ENUM — used ONLY by agent_runs.status.
-- What this KEEPS:
--   - agent_family_enum — still used by workspace_items.agent_family and
--     task_state.agent_family. Do NOT drop it.

DROP VIEW IF EXISTS public.user_cost_daily;

DROP TABLE IF EXISTS public.agent_runs;

DROP TYPE IF EXISTS public.agent_run_status;
