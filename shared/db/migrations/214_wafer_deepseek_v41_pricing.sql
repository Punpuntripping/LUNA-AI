-- Migration 214: price row for DeepSeek V4.1 Flash 1017 served by Wafer.
--
-- 2026-11-11: Wafer heads the router, both deep_search expanders and the
-- artifact_analyzer, and is the backup cell behind Baseten for the aggregator
-- and agent_writer (agents/utils/agent_models.py _WAFER_V41*, _V41_MAX). The
-- ledger keys this host by its registry key (agents/model_registry.py);
-- Wafer echoes "DeepSeek-V4.1-Flash-1017", which registry_key_for resolves to
-- this key by exact model_id match. Wafer list price
-- (pass.wafer.ai/v1/models, checked 2026-11-11):
-- $0.13 input / $0.50 output / $0.008 cached-input per 1M tokens.
--
-- Additive + idempotent. Apply BEFORE deploying the code: without this row
-- every Wafer call bills $0. Dependencies: 055 (model_pricing), 169 (provider).

INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, cached_input_price_per_1m, is_active)
VALUES
    ('wafer-deepseek-v4.1-flash-1017', 'wafer', 0.13, 0.50, 0.008, true)
ON CONFLICT (model_name) DO NOTHING;
