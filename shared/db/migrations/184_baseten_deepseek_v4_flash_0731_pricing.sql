-- Migration 184: price row for DeepSeek V4 Flash 0731 served by Baseten.
--
-- 2026-10-22: every flash slot heads on 0731 via Baseten, Alibaba is backup
-- only (agents/utils/agent_models.py _FLASH*). The ledger keys this host by
-- the registry key the slot resolves to (agents/model_registry.py) — Baseten
-- echoes the same "deepseek-v4-flash-0731" as Alibaba, so tracking maps a
-- Baseten response to this key via provider_url. Baseten list price
-- (baseten.co/pricing, checked 2026-10-22):
-- $0.13 input / $0.26 output / $0.028 cached-input per 1M tokens.
--
-- Additive + idempotent. Apply BEFORE deploying the code: without this row
-- every Baseten flash call bills $0 (the key resolves to itself, no price).
-- Dependencies: 055
-- (model_pricing), 169 (provider). (181 was never used.)

INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, cached_input_price_per_1m, is_active)
VALUES
    ('baseten-deepseek-v4-flash-0731', 'baseten', 0.13, 0.26, 0.028, true)
ON CONFLICT (model_name) DO NOTHING;
