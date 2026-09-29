-- Migration 168: reprice the `pro` plan (المتقدمة) 89.90 → 94.90.
--
-- Owner decision 2026-09-29. One price everywhere (web today, the native app's
-- in-app purchase later) — only the LIST price moves.
--
--   column            before    after
--   price_sar         89.90     94.90
--   promo_price_sar   49.90     49.90   (UNCHANGED)
--
-- ⚠ Who this reaches: renewals charge `effective_plan_price(user, plan,
-- 'current')` at charge time (renewal_service rule 4). Every live pro payer on
-- 2026-09-29 is an early-adopter seat holder quoted 49.90, so NO charge changes
-- now; each steps to 94.90 (not 89.90) only when their 90-day window ends.
--
-- ⚠ PRICE ORDER == CAPABILITY ORDER is preserved: 49.90 < 94.90 < 289.90, and
-- the promo order 39.90 < 49.90 < 99.90 is untouched — `payment_service.
-- PLAN_RANK` and `pricingPlansAbove()` rank on price (see 147).
--
-- ⚠ The prorated-credit ceiling still cannot bind: the largest credit into a
-- `max` checkout is a stacked `pro` term, and 94.90 < 289.90.
--
-- VAT split (15%, inclusive), for the receipt reconcilers:
--   94.90 = 82.52 net + 12.38 VAT  (9490 halalas)
--
-- `frontend/lib/pricing.ts` carries the display copy of this number and must
-- move in the same change. Plain idempotent UPDATE; the in-process plan cache
-- refreshes within 5 minutes. Dependencies: 068, 138, 147.

UPDATE public.plans SET
    price_sar  = 94.90,
    updated_at = now()
WHERE plan_id = 'pro';
