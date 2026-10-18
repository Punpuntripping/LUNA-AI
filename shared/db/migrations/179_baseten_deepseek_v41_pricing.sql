-- Migration 179: price row for DeepSeek V4.1 Flash 1017 served by Baseten.
--
-- 2026-10-18: the deep_search aggregator, the writer (agent_writer) and the
-- reg/case expanders head on V4.1 Flash 1017 via Baseten
-- (agents/utils/agent_models.py _V41_*). V4.1 bills well above the 0731 flash
-- (0.14/0.28 on Alibaba), so it gets its OWN row — keyed by the registry key
-- the slot resolves to (agents/model_registry.py). Baseten list price:
-- $0.30 input / $1.20 output / $0.007 cached-input per 1M tokens.
--
-- Additive + idempotent. Dependencies: 055 (model_pricing), 169 (provider).

INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, cached_input_price_per_1m, is_active)
VALUES
    ('baseten-deepseek-v4.1-flash-1017', 'baseten', 0.30, 1.20, 0.007, true)
ON CONFLICT (model_name) DO NOTHING;
