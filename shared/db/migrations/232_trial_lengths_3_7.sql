-- =============================================================================
-- 232: shorter free trials — 3 days for everyone, 7 days for the three groups
-- =============================================================================
-- Owner decision, 2026-11-23: "the offers is too generous". New claims of the
-- free trial at the limit dialog get:
--   * everyone outside legal / specialist / entrepreneur → 3 days (was 7),
--     same limits as marketing_lawyer_v2: 10 / session, 50 / week, 25 pages
--   * legal / specialist / entrepreneur → 7 days (was 14), same limits as
--     marketing_lawyer_14: 15 / session, 74 / week, 50 pages
--
-- NEW CLAIMS ONLY, on two NEW rows, as with 219. Neither old row is edited:
--   * marketing_lawyer_14 is still granted by 31 plan_codes (autopilot_t_202610
--     and autopilot_t_202611, 30 unused slots); owner chose to keep them at 14 days.
--   * marketing_lawyer_v2 has 23 running trials. get_user_quota_state derives
--     the allowance period index from plans.duration_days, so cutting that row
--     to 3 days would hand those trials a fresh OCR / library allowance on day
--     3 and day 6 of their week.
-- expires_at is stamped at claim (handle_subscription_assignment), so running
-- trials keep their end dates on either row.
--
-- Copied from the source rows, so every other column (names, library unlocks,
-- parallel runs, NULL price = not purchasable, deep-search gate) stays identical.
--
-- INERT ON ITS OWN: nothing grants these plans until 232b. Apply 232b only after
-- the web copy that reads 3 days / 7 days (free-trial-content.ts) is live.
--
-- Rollback (only while no user_subscriptions / free_trial_claims row uses them):
--   DELETE FROM public.plans WHERE plan_id IN ('marketing_lawyer_3', 'marketing_lawyer_7');

BEGIN;

SET LOCAL lock_timeout = '5s';

INSERT INTO public.plans (
    plan_id, name_ar, name_en,
    points_monthly, points_weekly, points_session,
    ocr_pages_monthly, web_calls_monthly, duration_days,
    price_sar, billing_cycle, library_unlocks_period, promo_price_sar,
    max_parallel_runs, deep_search_min_points, deep_search_per_window
)
SELECT v.new_id, p.name_ar, v.name_en,
       p.points_monthly, p.points_weekly, p.points_session,
       p.ocr_pages_monthly, p.web_calls_monthly, v.days,
       p.price_sar, p.billing_cycle, p.library_unlocks_period, p.promo_price_sar,
       p.max_parallel_runs, p.deep_search_min_points, p.deep_search_per_window
  FROM (VALUES ('marketing_lawyer_v2', 'marketing_lawyer_3', 'Marketing — Lawyers (3 days)', 3),
               ('marketing_lawyer_14', 'marketing_lawyer_7', 'Marketing — Lawyers (7 days)', 7))
       AS v (old_id, new_id, name_en, days)
  JOIN public.plans p ON p.plan_id = v.old_id
ON CONFLICT (plan_id) DO NOTHING;

DO $selftest$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.plans
                    WHERE plan_id = 'marketing_lawyer_3' AND duration_days = 3
                      AND points_session = 10 AND points_weekly = 50 AND ocr_pages_monthly = 25
                      AND price_sar IS NULL) THEN
        RAISE EXCEPTION '232: marketing_lawyer_3 missing or wrong';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.plans
                    WHERE plan_id = 'marketing_lawyer_7' AND duration_days = 7
                      AND points_session = 15 AND points_weekly = 74 AND ocr_pages_monthly = 50
                      AND price_sar IS NULL) THEN
        RAISE EXCEPTION '232: marketing_lawyer_7 missing or wrong';
    END IF;
    IF (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_v2') IS DISTINCT FROM 7
       OR (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_14') IS DISTINCT FROM 14 THEN
        RAISE EXCEPTION '232: the old rows changed — they must stay 7 / 14 days';
    END IF;
END;
$selftest$;

COMMIT;
