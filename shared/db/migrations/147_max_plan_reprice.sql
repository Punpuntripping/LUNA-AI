-- Migration 147: reprice + re-cap the `max` plan (القصوى).
--
-- Owner decision 2026-08-29. The LIST price and the three capacity caps move
-- together; the المشتركون الأوائل promo price does NOT move.
--
--   column                  before    after
--   price_sar               189.90    289.90
--   points_session          50        75
--   points_weekly           250       375
--   ocr_pages_monthly       200       500
--   promo_price_sar         99.90     99.90   (UNCHANGED — see below)
--   library_unlocks_period  1000      1000    (UNCHANGED)
--
-- ⚠ promo_price_sar STAYS 99.90 on purpose. `apply_promo_price()` (migration
-- 138) reads the catalog at checkout, so raising `price_sar` alone widens the
-- early-adopter discount rather than breaking it — which is the intent. The
-- seat holders were sold 99.90 and keep it.
--
-- ⚠ PRICE ORDER == CAPABILITY ORDER is preserved: 49.90 < 89.90 < 289.90, and
-- the promo order 39.90 < 49.90 < 99.90 is untouched. Both `payment_service.
-- PLAN_RANK` and `pricingPlansAbove()` rank on price, so neither can now offer
-- a downgrade as an upgrade (migration 131 §, quota_upgrade_ladder.md).
--
-- ⚠ The prorated-credit CEILING still cannot bind: the largest credit anyone
-- can carry into a `max` checkout is a stacked `pro` term, and 289.90 is
-- further above it than 189.90 was. No change needed in payment_service.
--
-- VAT split (15%, inclusive) for the new amount, for the receipt reconcilers:
--   289.90 = 252.09 net + 37.81 VAT  (28990 halalas)
--
-- Plan rows are data; this is a plain idempotent UPDATE (the in-process plan
-- cache refreshes within 5 minutes). Dependencies: 068, 076, 113, 138.

UPDATE public.plans SET
    price_sar         = 289.90,
    points_session    = 75,
    points_weekly     = 375,
    ocr_pages_monthly = 500,
    updated_at        = now()
WHERE plan_id = 'max'
  AND (price_sar         IS DISTINCT FROM 289.90
    OR points_session    IS DISTINCT FROM 75
    OR points_weekly     IS DISTINCT FROM 375
    OR ocr_pages_monthly IS DISTINCT FROM 500);

-- Verification (expect one row: max · 289.90 · 99.90 · 75 · 375 · 500 · 1000)
--   SELECT plan_id, price_sar, promo_price_sar, points_session, points_weekly,
--          ocr_pages_monthly, library_unlocks_period
--     FROM public.plans WHERE plan_id = 'max';
