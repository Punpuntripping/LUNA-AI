-- Migration 055: model_pricing unification + cached-input column + drop legacy view.
--
-- Three coordinated changes that move cost accounting from per-tier to per-model:
--
--   1. Drop the provider column and its index. With the FallbackModel chain
--      (Alibaba primary → OpenRouter fallback) the same logical model can fire
--      on either provider, so we collapse to ONE row per logical model. The
--      OpenRouter (`or-*`) rows are deleted; the surviving Alibaba rows hold
--      the unified rate because Alibaba prices are ≥ OpenRouter in every cell
--      and Alibaba is the happy-path provider — conservative + accurate.
--
--   2. Add `cached_input_price_per_1m` (NULLABLE). Replaces the hardcoded
--      `× 0.1` cached-read discount in `agents/utils/agent_models.cost_usd`.
--      NULL preserves legacy behaviour (input_price × 0.1); real per-model
--      values are filled in later.
--
--   3. Drop `user_cost_daily` view. The quota gate (056) rehydrates Redis
--      counters with typed queries against agent_runs directly; the view is
--      orphaned (zero callers in repo) and its agent_family grouping does not
--      match the quota meters (ocr / ord / web).
--
-- Dependencies:
--   - 013_model_pricing.sql   (model_pricing table)
--   - 036_agent_runs_cost.sql (seeded the or-* rows + user_cost_daily view)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. Drop the legacy per-user/per-day/per-agent-family view.
--    The quota module owns its own rehydration query (typed, scoped).
------------------------------------------------------------------------
DROP VIEW IF EXISTS public.user_cost_daily;

------------------------------------------------------------------------
-- 2. Delete OpenRouter alias rows. The surviving Alibaba rows become the
--    unified rate per logical model.
------------------------------------------------------------------------
DELETE FROM public.model_pricing
WHERE model_name LIKE 'or-%';

------------------------------------------------------------------------
-- 3. Drop the provider index and column.
------------------------------------------------------------------------
DROP INDEX IF EXISTS public.idx_pricing_provider;

ALTER TABLE public.model_pricing
    DROP COLUMN IF EXISTS provider;

------------------------------------------------------------------------
-- 4. Add cached-input price column. NULL → fall back to input × 0.1 in
--    application code (preserves current behaviour until values are set).
------------------------------------------------------------------------
ALTER TABLE public.model_pricing
    ADD COLUMN IF NOT EXISTS cached_input_price_per_1m DECIMAL(10,4);

COMMENT ON COLUMN public.model_pricing.cached_input_price_per_1m IS
    'USD per 1M cached-input tokens (prompt cache hit). NULL → cost_usd() '
    'falls back to (prompt_price_per_1m × 0.1). Set per model as real cached '
    'rates become known.';

COMMENT ON TABLE public.model_pricing IS
    'LLM pricing reference + runtime source of truth. One row per logical '
    'model. Loaded into shared/pricing/registry.py cache at FastAPI startup; '
    'cost_usd() reads via pricing.get_price(model_name).';
