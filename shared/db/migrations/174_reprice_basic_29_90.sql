-- Migration 174: reprice the `basic` plan (الأساسية) to a flat 29.90.
--
-- Owner decision 2026-10-08: basic drops to 29.90 and leaves the early-adopter
-- campaign. No list price is struck through on the card any more.
--
--   column            before    after
--   price_sar         49.90     29.90
--   promo_price_sar   39.90     NULL
--
-- ⚠ promo_price_sar NULL ⇒ effective_plan_price() returns price_sar for every
-- context, and the campaign endpoint omits basic from its promo map
-- (_fetch_promo_prices skips NULL), so no card shows a discount or
-- «المقاعد محدودة» for basic.
--
-- ⚠ Who this reaches: nobody today. 0 active basic subscribers on 2026-10-08;
-- basic is one-time (no renewal). The only open basic checkout is an abandoned
-- 2026-09-08 row quoted 39.90.
--
-- ⚠ PRICE ORDER == CAPABILITY ORDER is preserved: 29.90 < 94.90 < 289.90, and
-- with the campaign open 29.90 < 49.90 < 99.90 — `payment_service.PLAN_RANK`
-- and `pricingPlansAbove()` rank on price (see 147, 168).
--
-- ORDER: this is a price CUT, so the DB moves FIRST — between this UPDATE and
-- the frontend deploy the card still says 49.90 and checkout charges 29.90,
-- the mismatch that favours the customer.
--
-- VAT split (15%, inclusive): 29.90 = 26.00 net + 3.90 VAT (2990 halalas).
--
-- `frontend/lib/pricing.ts` carries the display copy and moves in the same
-- change. Plain idempotent UPDATE; the in-process plan cache refreshes within
-- 5 minutes. Dependencies: 068, 138, 168.

UPDATE public.plans SET
    price_sar       = 29.90,
    promo_price_sar = NULL,
    updated_at      = now()
WHERE plan_id = 'basic';
