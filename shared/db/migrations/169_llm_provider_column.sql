-- Migration 169: provider column on model_pricing + llm_calls; Crusoe price row.
--
-- Plan: .claude/plans/crusoe_primary_migration.md §4.4. Owner decision
-- 2026-09-29: DeepSeek V4 Flash gets a SEPARATE pricing row for Crusoe; every
-- existing row (and every historical llm_calls row) stays Alibaba.
--
--   model_pricing.provider  NOT NULL DEFAULT 'alibaba' — which provider the
--                           price belongs to. Existing 6 rows are all Alibaba
--                           (OpenRouter `or-` keys normalise onto them).
--   llm_calls.provider      nullable — who SERVED the call. Code stamps it from
--                           the model registry; NULL = unknown/pre-stamp.
--
-- Backfill of llm_calls: LLM rows → 'alibaba' (owner: the past is Alibaba),
-- except rows whose model names another vendor outright (Mistral OCR,
-- OpenRouter echo names `deepseek/...`, the early Gemini rows).
--
-- Additive + idempotent. Safe before the code ships: load_pricing selects
-- explicit columns and nothing yet resolves `crusoe-deepseek-v4-flash`.
-- Dependencies: 055 (model_pricing), 058 (llm_calls).

ALTER TABLE public.model_pricing
    ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'alibaba';

ALTER TABLE public.llm_calls
    ADD COLUMN IF NOT EXISTS provider text;

INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, cached_input_price_per_1m, is_active)
VALUES
    ('crusoe-deepseek-v4-flash', 'crusoe', 0.14, 0.28, 0.03, true)
ON CONFLICT (model_name) DO NOTHING;

UPDATE public.llm_calls SET provider = CASE
        WHEN model ILIKE 'mistral%'   THEN 'mistral'
        WHEN model ILIKE 'deepseek/%' THEN 'openrouter'
        WHEN model ILIKE 'or-%'       THEN 'openrouter'
        WHEN model ILIKE 'gemini%'    THEN 'google'
        WHEN model IS NULL OR model = 'none' THEN NULL
        ELSE 'alibaba'
    END
WHERE provider IS NULL;
