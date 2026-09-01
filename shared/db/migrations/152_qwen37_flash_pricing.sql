-- Migration 152: price row for qwen3.7-flash (the new reranker head model).
--
-- The two reranker slots (reg_compliance_reranker, case_search_reranker) move
-- off qwen3.5-flash onto qwen3.7-flash — see agents/utils/agent_models.py
-- (`_RERANKER` policy, `head="qwen3.7-flash"`). Without a price row here,
-- shared.pricing.get_price() returns None and cost_usd() silently bills every
-- reranker call at $0.00, so the llm_calls ledger would under-report the whole
-- deep_search family.
--
-- Rates: Alibaba Model Studio, SINGAPORE / international region, base input
-- tier (input <= 32K tokens) — the same convention every other row here uses
-- (qwen3.5-flash 0.10/0.40 is that model's Singapore rate, not its cheaper
-- Beijing rate). qwen3.7-flash is TIERED internationally:
--     input <=  32K  ->  $0.03 in / $0.13 out   <- stored here
--     input <= 256K  ->  ~3x the base tier
--     input <=   1M  ->  ~6x the base tier
-- Reranker calls run ~8-30K input tokens, so the base tier is the right
-- single rate for this table's one-rate-per-model shape. A pathological
-- >32K call is under-billed; that is the same simplification already in
-- effect for every other tiered model in this table.
--
-- Explicit cache read is $0.003/1M (vs the NULL fallback of input x 0.1 =
-- $0.003) — identical, so cached_input_price_per_1m is set explicitly rather
-- than left to the fallback.
--
-- Dependencies:
--   - 013_model_pricing.sql   (model_pricing table)
--   - 055_model_pricing_unify.sql (dropped `provider`, added cached column)
--
-- Idempotent.

INSERT INTO public.model_pricing
    (model_name, prompt_price_per_1m, completion_price_per_1m,
     cached_input_price_per_1m, is_active)
VALUES
    ('qwen3.7-flash', 0.0300, 0.1300, 0.0030, true)
ON CONFLICT (model_name) DO UPDATE SET
    prompt_price_per_1m       = EXCLUDED.prompt_price_per_1m,
    completion_price_per_1m   = EXCLUDED.completion_price_per_1m,
    cached_input_price_per_1m = EXCLUDED.cached_input_price_per_1m,
    is_active                 = EXCLUDED.is_active,
    updated_at                = now();
