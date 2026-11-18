-- =============================================================================
-- 219: التجربة المجانية بحدود أقل — 10 نقاط للجلسة، 50 للأسبوع، 25 صفحة استخراج
-- =============================================================================
-- Owner decision, 2026-11-18: new free-trial claims get 10 points per 5-hour
-- session, 50 points per rolling week and 25 OCR pages (was 15 / 74 / 50).
--
-- NEW CLAIMS ONLY. The 65 trials running on 2026-11-18 stay on their plan row,
-- and so do the plan codes minted on marketing_lawyer / marketing_lawyer_14.
-- Editing those rows would cut both mid-term. So the new limits go on two NEW
-- rows, and 219b points free_trial_campaign at them.
--
-- Copied from the old rows, so every other column (duration, names, library
-- unlocks, parallel runs, NULL price = not purchasable) stays identical.
--
-- INERT ON ITS OWN: nothing grants these plans until 219b. Apply 219b only after
-- the web copy that states the limits (free-trial-content.ts) is live, so the
-- card never promises more than the plan gives.
--
-- Rollback (only while no user_subscriptions / free_trial_claims row uses them):
--   DELETE FROM public.plans WHERE plan_id IN ('marketing_lawyer_v2', 'marketing_lawyer_14_v2');

BEGIN;

SET LOCAL lock_timeout = '5s';

INSERT INTO public.plans (
    plan_id, name_ar, name_en,
    points_monthly, points_weekly, points_session,
    ocr_pages_monthly, web_calls_monthly, duration_days,
    price_sar, billing_cycle, library_unlocks_period, promo_price_sar,
    max_parallel_runs, deep_search_min_points, deep_search_per_window
)
SELECT v.new_id, p.name_ar, p.name_en || ' — v2',
       p.points_monthly, 50, 10,
       25, p.web_calls_monthly, p.duration_days,
       p.price_sar, p.billing_cycle, p.library_unlocks_period, p.promo_price_sar,
       p.max_parallel_runs, p.deep_search_min_points, p.deep_search_per_window
  FROM (VALUES ('marketing_lawyer',    'marketing_lawyer_v2'),
               ('marketing_lawyer_14', 'marketing_lawyer_14_v2')) AS v (old_id, new_id)
  JOIN public.plans p ON p.plan_id = v.old_id
ON CONFLICT (plan_id) DO NOTHING;

DO $selftest$
BEGIN
    IF (SELECT count(*) FROM public.plans
         WHERE plan_id IN ('marketing_lawyer_v2', 'marketing_lawyer_14_v2')
           AND points_session = 10 AND points_weekly = 50 AND ocr_pages_monthly = 25
           AND price_sar IS NULL) <> 2 THEN
        RAISE EXCEPTION '219: v2 trial rows missing or wrong limits';
    END IF;
    IF (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_v2') IS DISTINCT FROM 7
       OR (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_14_v2') IS DISTINCT FROM 14 THEN
        RAISE EXCEPTION '219: v2 trial durations wrong';
    END IF;
END;
$selftest$;

COMMIT;
