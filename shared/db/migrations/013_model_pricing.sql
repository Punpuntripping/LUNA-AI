-- 013_model_pricing.sql
-- LLM model pricing reference table for cost tracking

CREATE TABLE IF NOT EXISTS public.model_pricing (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name                  VARCHAR(100) UNIQUE NOT NULL,
    provider                    VARCHAR(100) NOT NULL,
    prompt_price_per_1m         DECIMAL(10,4) NOT NULL,
    completion_price_per_1m     DECIMAL(10,4) NOT NULL,
    is_active                   BOOLEAN NOT NULL DEFAULT true,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_pricing_provider
    ON public.model_pricing (provider);

CREATE INDEX IF NOT EXISTS idx_pricing_active
    ON public.model_pricing (is_active)
    WHERE is_active = true;

COMMENT ON TABLE public.model_pricing IS 'LLM pricing reference table. Used to calculate per-message costs.';
COMMENT ON COLUMN public.model_pricing.prompt_price_per_1m IS 'USD cost per 1 million prompt tokens.';
COMMENT ON COLUMN public.model_pricing.completion_price_per_1m IS 'USD cost per 1 million completion tokens.';
