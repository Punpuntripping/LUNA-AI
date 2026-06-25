-- Migration 076: rebalance the sold plans' point limits + drop internet search.
--
-- New usage-point design (1 USD = 100 points). The weekly window is now a fixed
-- 7-day window anchored to the user's first message (see shared/quota — it moved
-- from a fixed Friday-13:00-Riyadh reset to the session model, 7d instead of 5h),
-- so for the 7-day `basic` plan the weekly window spans the whole subscription.
--
--   plan   session  weekly  monthly        ocr/30d  web/30d  duration
--   basic  10       50      NULL (none)    40       0        7d
--   pro    15       75      300 (=75×4)    40       0        30d
--   max    50       250     1000 (=250×4)  200      0        30d
--
-- Changes vs migration 068:
--   * Lower session/weekly/monthly across basic/pro/max to the new values.
--   * basic.points_monthly → NULL: a 7-day plan has no monthly concept; the
--     weekly (= the plan's 7-day life) is the only binding ord window. NULL =
--     "window not read" in the gate, so basic is bounded by session + weekly.
--   * web_calls_monthly → 0 everywhere it was offered: the internet-search
--     feature does not exist, so it is shown as "not included" (and the UI no
--     longer surfaces it). OCR caps are unchanged.
--
-- Plan rows are data; this is a plain idempotent UPDATE (the in-process plan
-- cache refreshes within 5 minutes). Dependencies: 068_subscription_plans.sql.

UPDATE public.plans SET
    points_session    = 10,
    points_weekly     = 50,
    points_monthly    = NULL,
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id = 'basic';

UPDATE public.plans SET
    points_session    = 15,
    points_weekly     = 75,
    points_monthly    = 300,
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id = 'pro';

UPDATE public.plans SET
    points_session    = 50,
    points_weekly     = 250,
    points_monthly    = 1000,
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id = 'max';

-- Internet search removed from the marketing offers too (free is already 0; dev
-- stays NULL/unlimited as an internal account).
UPDATE public.plans SET
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id IN ('marketing_lawyer', 'marketing_individual');
