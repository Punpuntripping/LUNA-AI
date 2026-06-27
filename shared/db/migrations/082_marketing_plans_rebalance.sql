-- Migration 082: rebalance the two marketing promo plans.
--
--   marketing_individual ("عرض الأفراد")  -> mirror the basic plan exactly:
--       session 10 / weekly 50 / monthly NULL / ocr 15.
--   marketing_lawyer     ("عرض المحامين") -> session 15 / weekly 76 / ocr 20
--       (was weekly 150 / ocr 40; session is the 5h sub-window, so the smaller
--        of "76, 15" is the session cap and 76 is the 7-day weekly cap).
--
-- Both stay 7-day plans (duration_days unchanged). web_calls_monthly stays 0
-- (the internet-search feature does not exist). These plans are code-activated
-- promos, not shown on the public /pricing page, so no pricing.ts sync needed.
-- Plan rows are data; idempotent UPDATE (plan cache refreshes within 5 min).
-- Dependencies: 068, 076, 077, 078.

UPDATE public.plans SET
    points_session    = 10,
    points_weekly     = 50,
    points_monthly    = NULL,
    ocr_pages_monthly = 15,
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id = 'marketing_individual';

UPDATE public.plans SET
    points_session    = 15,
    points_weekly     = 76,
    points_monthly    = NULL,
    ocr_pages_monthly = 20,
    web_calls_monthly = 0,
    updated_at        = now()
WHERE plan_id = 'marketing_lawyer';
