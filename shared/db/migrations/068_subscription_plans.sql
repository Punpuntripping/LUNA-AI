-- Migration 068: subscription plans — points-based limits per plan.
--
-- Replaces the flat per-user USD quota columns with a plan catalog. Currency:
-- 1 USD = 100 points. Each plan defines three ordinary-spend windows
-- (rolling 30d monthly / rolling 7d weekly / rolling 5h session) plus
-- per-plan OCR + web monthly caps. NULL limit = unlimited; 0 = feature not
-- included in the plan.
--
-- Paid-plan ratios: weekly = 4 × session, monthly = 4 × weekly.
--
--   plan                  monthly  weekly  session  ocr/30d  web/30d  duration
--   free                  100      50      20       0        0        —
--   basic (أسبوعي)        640      160     40       40       40       7d
--   pro                   800      200     50       40       40       —
--   max                   2400     600     150      200      200      —
--   marketing_lawyer      —        150     —        40       40       7d
--   marketing_individual  —        75      —        20       20       7d
--   dev                   —        —       —        —        —        —
--
-- users.plan_id is NULL by default → account LOCKED (gate rejects every send
-- with PlanInactive) until the operator manually assigns a plan in Supabase.
-- Time-boxed plans (duration_days NOT NULL): a BEFORE UPDATE trigger stamps
-- subscription_expires_at = now() + duration on assignment; once expired the
-- gate falls back to the free plan's limits.
--
-- Per-user override columns (NULL = inherit plan) exist so dev accounts can
-- test specific limits without touching the plan catalog.
--
-- The old USD columns (ord_cost_daily_limit_usd / ord_cost_weekly_limit_usd)
-- are left in place for deploy-order safety (old backend still reads them);
-- they are dead once the points-based gate deploys. Drop in a later cleanup
-- migration, same pattern as the agent_runs drop (062).
--
-- Dependencies: 003_users.sql, 056/057 (quota columns being repurposed).
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. Plan catalog.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.plans (
    plan_id            TEXT PRIMARY KEY,
    name_ar            TEXT NOT NULL,
    name_en            TEXT,
    points_monthly     INTEGER,  -- rolling 30d, NULL = unlimited (1 USD = 100 pts)
    points_weekly      INTEGER,  -- rolling 7d
    points_session     INTEGER,  -- rolling 5h
    ocr_pages_monthly  INTEGER,  -- rolling 30d; 0 = OCR not included
    web_calls_monthly  INTEGER,  -- rolling 30d; future skill
    duration_days      INTEGER,  -- NULL = non-expiring; else trigger stamps expiry
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.plans IS
    'Subscription plan catalog. Limits in points (1 USD = 100 points). '
    'NULL limit = unlimited, 0 = feature not included. Rows are data — tune '
    'with plain UPDATEs, no code change needed.';

ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plans_select_authenticated ON public.plans;
CREATE POLICY plans_select_authenticated ON public.plans
    FOR SELECT TO authenticated USING (true);
-- No INSERT/UPDATE/DELETE policies: catalog is managed via service role only.

INSERT INTO public.plans
    (plan_id, name_ar, name_en, points_monthly, points_weekly, points_session,
     ocr_pages_monthly, web_calls_monthly, duration_days)
VALUES
    ('free',                 'المجانية',      'Free',                 100,  50,   20,   0,    0,    NULL),
    ('basic',                'الأساسية',      'Basic',                640,  160,  40,   40,   40,   7),
    ('pro',                  'الاحترافية',    'Pro',                  800,  200,  50,   40,   40,   NULL),
    ('max',                  'القصوى',        'Max',                  2400, 600,  150,  200,  200,  NULL),
    ('marketing_lawyer',     'عرض المحامين',  'Marketing — Lawyers',  NULL, 150,  NULL, 40,   40,   7),
    ('marketing_individual', 'عرض الأفراد',   'Marketing — Personal', NULL, 75,   NULL, 20,   20,   7),
    ('dev',                  'حساب مطوّر',    'Developer',            NULL, NULL, NULL, NULL, NULL, NULL)
ON CONFLICT (plan_id) DO UPDATE SET
    name_ar           = EXCLUDED.name_ar,
    name_en           = EXCLUDED.name_en,
    points_monthly    = EXCLUDED.points_monthly,
    points_weekly     = EXCLUDED.points_weekly,
    points_session    = EXCLUDED.points_session,
    ocr_pages_monthly = EXCLUDED.ocr_pages_monthly,
    web_calls_monthly = EXCLUDED.web_calls_monthly,
    duration_days     = EXCLUDED.duration_days,
    updated_at        = now();

------------------------------------------------------------------------
-- 2. users.plan_id (NULL = locked) + per-user point overrides.
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS plan_id TEXT REFERENCES public.plans(plan_id),
    ADD COLUMN IF NOT EXISTS points_monthly_override INTEGER,
    ADD COLUMN IF NOT EXISTS points_weekly_override  INTEGER,
    ADD COLUMN IF NOT EXISTS points_session_override INTEGER;

COMMENT ON COLUMN public.users.plan_id IS
    'FK to plans. NULL = account not activated — the quota gate rejects every '
    'send until the operator assigns a plan manually.';
COMMENT ON COLUMN public.users.points_monthly_override IS
    'Per-user override of plans.points_monthly. NULL = inherit plan. For dev limit-testing.';
COMMENT ON COLUMN public.users.points_weekly_override IS
    'Per-user override of plans.points_weekly. NULL = inherit plan.';
COMMENT ON COLUMN public.users.points_session_override IS
    'Per-user override of plans.points_session. NULL = inherit plan.';

-- Repurpose the OCR/web limit columns as per-user overrides (NULL = inherit
-- plan). Old backend code merges non-NULL values over its own defaults, so
-- NULLing them is deploy-order safe (it falls back to the previous 600/300).
ALTER TABLE public.users
    ALTER COLUMN ocr_pages_monthly_limit DROP NOT NULL,
    ALTER COLUMN ocr_pages_monthly_limit DROP DEFAULT,
    ALTER COLUMN web_calls_monthly_limit DROP NOT NULL,
    ALTER COLUMN web_calls_monthly_limit DROP DEFAULT;

UPDATE public.users
SET ocr_pages_monthly_limit = NULL,
    web_calls_monthly_limit = NULL
WHERE ocr_pages_monthly_limit IS NOT NULL
   OR web_calls_monthly_limit IS NOT NULL;

COMMENT ON COLUMN public.users.ocr_pages_monthly_limit IS
    'Per-user override of plans.ocr_pages_monthly. NULL = inherit plan.';
COMMENT ON COLUMN public.users.web_calls_monthly_limit IS
    'Per-user override of plans.web_calls_monthly. NULL = inherit plan.';

------------------------------------------------------------------------
-- 3. Plan assignment trigger — stamps subscription_expires_at from
--    plans.duration_days whenever plan_id changes via UPDATE (the manual
--    Supabase assignment path). Non-expiring plans clear the expiry.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_plan_assignment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    d INTEGER;
BEGIN
    IF NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN
        IF NEW.plan_id IS NULL THEN
            NEW.subscription_expires_at := NULL;
        ELSE
            SELECT duration_days INTO d FROM public.plans WHERE plan_id = NEW.plan_id;
            NEW.subscription_expires_at :=
                CASE WHEN d IS NULL THEN NULL
                     ELSE now() + make_interval(days => d)
                END;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_users_plan_assignment ON public.users;
CREATE TRIGGER trg_users_plan_assignment
    BEFORE UPDATE OF plan_id ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_plan_assignment();

------------------------------------------------------------------------
-- 4. Dev accounts — owner + test-domain accounts. Real users stay NULL
--    (locked) until manually assigned.
------------------------------------------------------------------------
UPDATE public.users
SET plan_id = 'dev'
WHERE plan_id IS NULL
  AND (
        email = 'mhfallath99@gmail.com'
     OR email LIKE '%@luna.dev'
     OR email LIKE '%@luna-legal.dev'
     OR email = 'test@luna.ai'
     OR email = 'testluna@test.com'
  );
