-- Migration 056: per-user usage quotas + OCR page accounting.
--
-- Adds six per-user quota limits and one new agent_runs column. The quota gate
-- (shared/quota/) fires once per message, before OCR + router, and rejects when
-- the projected request would push the user past any of:
--
--   * ocr_pages_daily_limit       — page count, today (UTC)
--   * ocr_pages_weekly_limit      — page count, rolling 7 days
--   * ord_cost_daily_limit_usd    — USD spent by ordinary pipeline today
--   * ord_cost_weekly_limit_usd   — USD spent by ordinary pipeline, rolling 7
--   * web_calls_daily_limit       — web-search calls today (future skill)
--   * web_calls_weekly_limit      — web-search calls, rolling 7 (future skill)
--
-- Weekly is derived (sum of 7 daily Redis buckets), not stored — only the daily
-- counter is ever written. PG is the rehydration source on Redis miss; the
-- quota module queries agent_runs directly (no view).
--
-- agent_runs.pages_used: how many pages the OCR extractor processed for this
-- run. NULL on every non-OCR run.
--
-- Dependencies:
--   - 003_users.sql        (users table)
--   - 029_agent_runs.sql   (agent_runs table)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. User quota columns. Conservative defaults — easy to tune per-user
--    via UPDATE statements later.
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS ocr_pages_daily_limit       INTEGER       NOT NULL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS ocr_pages_weekly_limit      INTEGER       NOT NULL DEFAULT 200,
    ADD COLUMN IF NOT EXISTS ord_cost_daily_limit_usd    NUMERIC(8,4)  NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS ord_cost_weekly_limit_usd   NUMERIC(8,4)  NOT NULL DEFAULT 5.0,
    ADD COLUMN IF NOT EXISTS web_calls_daily_limit       INTEGER       NOT NULL DEFAULT 20,
    ADD COLUMN IF NOT EXISTS web_calls_weekly_limit      INTEGER       NOT NULL DEFAULT 80;

COMMENT ON COLUMN public.users.ocr_pages_daily_limit IS
    'Max OCR pages user may extract per UTC day. Hard limit — gate rejects when reached.';
COMMENT ON COLUMN public.users.ocr_pages_weekly_limit IS
    'Max OCR pages user may extract per rolling 7 days. Computed as sum of last 7 daily counters.';
COMMENT ON COLUMN public.users.ord_cost_daily_limit_usd IS
    'Max USD ordinary-pipeline spend per UTC day. Hard limit — gate rejects when reached.';
COMMENT ON COLUMN public.users.ord_cost_weekly_limit_usd IS
    'Max USD ordinary-pipeline spend per rolling 7 days. Computed as sum of last 7 daily counters.';
COMMENT ON COLUMN public.users.web_calls_daily_limit IS
    'Max web-search calls per UTC day. Future skill — scaffold only, not enforced yet.';
COMMENT ON COLUMN public.users.web_calls_weekly_limit IS
    'Max web-search calls per rolling 7 days. Future skill — scaffold only, not enforced yet.';

------------------------------------------------------------------------
-- 2. agent_runs.pages_used — OCR-only column. NULL on every non-OCR run.
--    Populated by agents/memory/ocr_extractor/runner.py.
------------------------------------------------------------------------
ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS pages_used INTEGER;

COMMENT ON COLUMN public.agent_runs.pages_used IS
    'Pages processed by the OCR extractor (memory.ocr_extractor runs only). '
    'NULL on every other agent_family / subtype. Read by shared/quota/ when '
    'rehydrating the OCR counter from PG on Redis miss.';

------------------------------------------------------------------------
-- 3. Indexes for rehydration queries. Both are partial — they only cover
--    the rows the quota module actually reads.
------------------------------------------------------------------------
-- (a) Ordinary cost rehydration:  SUM(cost_usd) WHERE user_id=? AND day=?
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_day_cost
    ON public.agent_runs (user_id, ((created_at AT TIME ZONE 'UTC')::date))
    WHERE cost_usd IS NOT NULL;

-- (b) OCR page rehydration:  SUM(pages_used) WHERE user_id=? AND day=?
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_day_pages
    ON public.agent_runs (user_id, ((created_at AT TIME ZONE 'UTC')::date))
    WHERE pages_used IS NOT NULL;
