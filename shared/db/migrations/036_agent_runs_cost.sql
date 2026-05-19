-- Migration 036: Per-run cost tracking for agent_runs.
--
-- Adds an estimated USD cost and a reasoning-token count to every agent run,
-- seeds the model_pricing reference table (013) with the tier-system models,
-- and exposes a per-user/day rollup view for cost monitoring.
--
-- Cost is computed in the application layer (agents/runs.py via
-- agents/utils/agent_models.py) and billed per *tier*: each deep_search phase
-- is priced at its tier's qwen/Alibaba primary rate. FallbackModel only swaps
-- off that primary on a 4xx/5xx API error and the fallbacks are cheaper, so
-- the stored cost is a conservative ceiling.
--
-- Dependencies:
--   - 029_agent_runs.sql      (agent_runs table)
--   - 013_model_pricing.sql   (model_pricing table)
--   - 016_rls.sql             (get_current_user_id() helper)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. agent_runs: cost + reasoning-token columns
------------------------------------------------------------------------
-- cost_usd: estimated USD cost of the whole run. NUMERIC(12,6) — sub-cent
--   precision; a run costs well under $1.
-- tokens_reasoning: "thinking" tokens. Stored separately because providers
--   bill them at the output rate but pydantic_ai's output_tokens excludes
--   them (they live in usage.details.reasoning_tokens).
ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS cost_usd         NUMERIC(12,6);

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS tokens_reasoning INTEGER;

COMMENT ON COLUMN public.agent_runs.cost_usd IS
    'Estimated USD cost of the run. Computed app-side, billed per tier at the '
    'qwen/Alibaba primary rate (conservative ceiling). See agents/utils/agent_models.py.';
COMMENT ON COLUMN public.agent_runs.tokens_reasoning IS
    'Reasoning/thinking tokens. Billed at the output rate; not included in tokens_out.';

------------------------------------------------------------------------
-- 2. Seed model_pricing with the tier-system models (013_model_pricing.sql).
--    Provider list prices, USD per 1M tokens, May 2026. Promo prices
--    (qwen ~35% off, deepseek-v4-pro 75% off until 2026-05-31) are NOT used
--    here — list prices give a stable cost baseline.
------------------------------------------------------------------------
INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, is_active)
VALUES
    ('qwen3.6-plus',         'alibaba',    0.5700, 3.4400, true),
    ('deepseek-v4-pro',      'alibaba',    1.7400, 3.4800, true),
    ('qwen3.5-flash',        'alibaba',    0.1000, 0.4000, true),
    ('deepseek-v4-flash',    'alibaba',    0.1400, 0.2800, true),
    ('or-qwen3.6-plus',      'openrouter', 0.5000, 3.0000, true),
    ('or-deepseek-v4-pro',   'openrouter', 1.7400, 3.4800, true),
    ('or-qwen3.5-flash',     'openrouter', 0.1000, 0.4000, true),
    ('or-deepseek-v4-flash', 'openrouter', 0.1120, 0.2240, true)
ON CONFLICT (model_name) DO UPDATE SET
    provider                = EXCLUDED.provider,
    prompt_price_per_1m     = EXCLUDED.prompt_price_per_1m,
    completion_price_per_1m = EXCLUDED.completion_price_per_1m,
    is_active               = EXCLUDED.is_active,
    updated_at              = now();

------------------------------------------------------------------------
-- 3. Per-user / per-day cost rollup view.
--    security_invoker = true → the SELECT runs under the caller's RLS, so
--    each user sees only their own agent_runs rows through the view.
------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.user_cost_daily
WITH (security_invoker = true) AS
SELECT
    user_id,
    (created_at AT TIME ZONE 'UTC')::date    AS day,
    agent_family,
    count(*)                                 AS runs,
    sum(coalesce(tokens_in, 0))              AS tokens_in,
    sum(coalesce(tokens_out, 0))             AS tokens_out,
    sum(coalesce(tokens_reasoning, 0))       AS tokens_reasoning,
    round(sum(coalesce(cost_usd, 0)), 6)     AS cost_usd
FROM public.agent_runs
GROUP BY user_id, (created_at AT TIME ZONE 'UTC')::date, agent_family;

COMMENT ON VIEW public.user_cost_daily IS
    'Per-user, per-day, per-agent-family token + USD cost rollup over agent_runs. '
    'security_invoker — RLS-scoped to the calling user.';
