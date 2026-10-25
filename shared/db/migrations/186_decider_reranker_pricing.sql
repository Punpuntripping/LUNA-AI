-- Migration 186: price rows for the reranker decision models on OpenRouter.
--
-- Plan: .claude/plans/reranker_decider_engine.md build step 6 (the plan names
-- this 185; 185 went to app_push_tokens). Both rerankers (regulations phase A,
-- case law phase B) can be served by OpenRouter's Decisions API
-- (agents/deep_search_v4/shared/decider.py). The usage entry carries the
-- CONFIGURED id ("liquid/d1-ultra", not the served "-20261015"), so that is
-- the key; _emit_executor_ledger writes it under deep_search.reranker.{exec}.
-- Decision models bill input tokens only: $0.042 per 1M, no completion price.
-- Verified on the regulations A/B (decisions_raw_30.jsonl): 10,559 tokens x
-- $0.042/M = $0.000443, the cost OpenRouter reported for that request.
--
-- Additive + idempotent. Apply BEFORE flipping REG_RERANKER_ENGINE or
-- CASE_RERANKER_ENGINE to "decider": without these rows every decider call
-- bills $0. Dependencies: 055 (model_pricing), 169 (provider).

INSERT INTO public.model_pricing
    (model_name, provider, prompt_price_per_1m, completion_price_per_1m, cached_input_price_per_1m, is_active)
VALUES
    ('liquid/d1-ultra',   'openrouter', 0.042, 0, NULL, true),
    ('typesafe/jev-1.14', 'openrouter', 0.042, 0, NULL, true)
ON CONFLICT (model_name) DO NOTHING;
