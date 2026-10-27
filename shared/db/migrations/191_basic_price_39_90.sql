-- Migration 191: raise the `basic` plan (الأساسية) from 29.90 to a flat 39.90.
--
-- Owner decision 2026-10-27 (.claude/plans/pricing_campaigns_v2.md §8). Basic
-- stays outside every campaign tier, so this is a plain catalog change.
--
--   column            before    after
--   price_sar         29.90     39.90
--   promo_price_sar   NULL      NULL   (unchanged — no strike-through, no seat)
--
-- ⚠ Who this reaches: nobody already holding basic — it is one-time (no
-- renewal). 5 basic purchases at 29.90 were made 2026-10-11..10-21; their
-- terms are paid and done. A basic → pro/max upgrade credits the cash actually
-- paid, so earlier buyers are credited on 29.90.
--
-- ⚠ PRICE ORDER == CAPABILITY ORDER is preserved: 39.90 < 94.90 < 289.90, and
-- against the public promos 39.90 < 59.90 (pro tier 2) < 149.90 / 189.90 —
-- `payment_service.PLAN_RANK` and `pricingPlansAbove()` rank on price
-- (see 147, 168, 174).
--
-- ORDER: this is a price RAISE, so the FRONTEND moves FIRST. Deploy
-- `frontend/lib/pricing.ts` (basic `price: "39.90"`), THEN apply this file —
-- between the deploy and this UPDATE the card says 39.90 and checkout still
-- charges 29.90, the mismatch that favours the customer.
--
-- VAT split (15%, inclusive): 39.90 = 34.70 net + 5.20 VAT (3990 halalas).
--
-- Plain idempotent UPDATE; the in-process plan cache refreshes within
-- 5 minutes. Dependencies: 068, 138, 174.

UPDATE public.plans SET
    price_sar  = 39.90,
    updated_at = now()
WHERE plan_id = 'basic'
  AND promo_price_sar IS NULL;

DO $$
DECLARE
    v_price numeric;
    v_promo numeric;
BEGIN
    SELECT price_sar, promo_price_sar INTO v_price, v_promo
      FROM public.plans WHERE plan_id = 'basic';
    IF v_price IS DISTINCT FROM 39.90 OR v_promo IS NOT NULL THEN
        RAISE EXCEPTION '191: basic expected 39.90 / NULL promo, found % / %', v_price, v_promo;
    END IF;
END $$;
