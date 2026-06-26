-- Migration 079: user_subscriptions — single source of truth for subscription
-- IDENTITY (which plan, since when, until when, where it came from, overrides) +
-- a get_user_usage_windows() RPC that makes the llm_calls ledger the single
-- source of truth for USAGE (rolling session/weekly/ocr windows).
--
-- WHY ----------------------------------------------------------------------
-- Subscription identity was scattered across users.plan_id, users.subscription_
-- expires_at, the dead users.subscription_tier, five per-user override columns,
-- the plans catalog, plan_codes, and two triggers. Usage was split across Redis
-- (three windows on two inconsistent counting models) + the llm_calls ledger,
-- so the gate could block on a window (rolling-30d "monthly") the dialog never
-- showed → "wrong" warnings. This migration consolidates identity into one row
-- per user and gives the gate + the dialog ONE rolling-usage source to share.
--
-- WHAT ----------------------------------------------------------------------
--  1. public.user_subscriptions — one current row per user (UNIQUE(user_id)).
--  2. handle_subscription_assignment() — BEFORE UPDATE OF plan_id trigger that
--     stamps expires_at from plans.duration_days + status (mirrors the existing
--     handle_plan_assignment on users; operator just sets plan_id).
--  3. Backfill every existing users row into user_subscriptions.
--  4. Rewrite redeem_plan_code() to upsert user_subscriptions (still mirrors
--     users.plan_id/expiry during the transition for deploy-order safety).
--  5. Rewrite handle_new_user() to also seed a LOCKED user_subscriptions row on
--     signup (so every account has a subscription record). Terms stamping kept.
--  6. get_user_usage_windows() — server-side rolling SUM over llm_calls.
--
-- DEPLOY-ORDER SAFETY: the legacy users columns (plan_id, subscription_expires_at,
-- subscription_tier, *_override, ocr_pages_monthly_limit, web_calls_monthly_limit)
-- are LEFT IN PLACE and kept in sync by redeem (mirror) so the old backend keeps
-- working mid-rollout. They are dropped in a later cleanup migration (080) once
-- the new backend is confirmed live. The old handle_plan_assignment trigger on
-- users also stays (harmless — recomputes the same expiry the mirror writes).
--
-- Dependencies: 003_users.sql, 058 (llm_calls + (user_id, created_at) index),
-- 068_subscription_plans.sql, 069_plan_codes.sql, 075_user_terms_consent.sql.
-- Idempotent.

------------------------------------------------------------------------
-- 1. user_subscriptions — identity SSoT (one current row per user).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_subscriptions (
    subscription_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL UNIQUE REFERENCES public.users(user_id) ON DELETE CASCADE,
    plan_id          text REFERENCES public.plans(plan_id),  -- NULL = account not activated (locked)
    status           text NOT NULL DEFAULT 'locked',          -- 'active' | 'expired' | 'locked'
    source           text NOT NULL DEFAULT 'manual',          -- 'signup' | 'manual' | 'code' | 'payment'
    started_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,                             -- NULL = non-expiring
    redeemed_code    text,                                    -- plan_codes.code used (source='code')
    points_monthly_override     integer,
    points_weekly_override      integer,
    points_session_override     integer,
    ocr_pages_monthly_override  integer,
    web_calls_monthly_override  integer,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.user_subscriptions IS
    'Single source of truth for subscription identity (one current row per user). '
    'plan_id NULL = account locked (not activated). The binding truth is '
    '(plan_id, expires_at); status is an operator-facing hint. Limits live in the '
    'plans catalog; *_override columns (NULL = inherit plan) are per-user. Writes '
    'go through the redeem_plan_code RPC, the handle_subscription_assignment '
    'trigger, or service-role UPDATEs — never the browser.';

COMMENT ON COLUMN public.user_subscriptions.plan_id IS
    'FK to plans. NULL = not activated (locked); the quota gate rejects every send.';
COMMENT ON COLUMN public.user_subscriptions.status IS
    'Operator hint: active | expired | locked. NOT authoritative — Python recomputes '
    'expiry from expires_at and falls back to the free plan when past.';

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_plan ON public.user_subscriptions (plan_id);

ALTER TABLE public.user_subscriptions ENABLE ROW LEVEL SECURITY;

-- Self-row read only (future-proofs a direct frontend read; today the backend
-- reads via the service role). No client INSERT/UPDATE/DELETE — same posture as
-- plans / plan_codes.
DROP POLICY IF EXISTS user_subscriptions_select_self ON public.user_subscriptions;
CREATE POLICY user_subscriptions_select_self ON public.user_subscriptions
    FOR SELECT TO authenticated
    USING (user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid()));

------------------------------------------------------------------------
-- 2. Assignment trigger — stamp expiry + status from plans.duration_days
--    whenever plan_id changes via UPDATE (the operator activation path).
--    Mirrors handle_plan_assignment (068) but on the new table. Fires only on
--    UPDATE OF plan_id so the backfill INSERT (below) keeps the real expiry.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_subscription_assignment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    d INTEGER;
BEGIN
    IF NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN
        IF NEW.plan_id IS NULL THEN
            NEW.expires_at := NULL;
            NEW.status     := 'locked';
        ELSE
            SELECT duration_days INTO d FROM public.plans WHERE plan_id = NEW.plan_id;
            NEW.expires_at := CASE WHEN d IS NULL THEN NULL
                                   ELSE now() + make_interval(days => d) END;
            NEW.status     := 'active';
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_user_subscriptions_assignment ON public.user_subscriptions;
CREATE TRIGGER trg_user_subscriptions_assignment
    BEFORE UPDATE OF plan_id ON public.user_subscriptions
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_subscription_assignment();

------------------------------------------------------------------------
-- 3. Backfill — one row per existing user, carrying identity verbatim from
--    the legacy users columns. Idempotent (skips users that already have a row).
------------------------------------------------------------------------
INSERT INTO public.user_subscriptions
    (user_id, plan_id, status, source, started_at, expires_at,
     points_monthly_override, points_weekly_override, points_session_override,
     ocr_pages_monthly_override, web_calls_monthly_override)
SELECT
    u.user_id,
    u.plan_id,
    CASE
        WHEN u.plan_id IS NULL THEN 'locked'
        WHEN u.subscription_expires_at IS NOT NULL
             AND u.subscription_expires_at <= now() THEN 'expired'
        ELSE 'active'
    END,
    'manual',
    now(),
    u.subscription_expires_at,
    u.points_monthly_override,
    u.points_weekly_override,
    u.points_session_override,
    u.ocr_pages_monthly_limit,
    u.web_calls_monthly_limit
FROM public.users u
ON CONFLICT (user_id) DO NOTHING;

------------------------------------------------------------------------
-- 4. Rewrite redeem_plan_code() — upsert user_subscriptions as the truth,
--    mirror users.plan_id/expiry during the transition. Same return shape +
--    same raised errors as 069 (the backend's Arabic mapping is unchanged).
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code TEXT, p_user_id UUID)
RETURNS TABLE(plan_id TEXT, name_ar TEXT, expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_norm   TEXT := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    v_cur    TEXT;
    v_curexp TIMESTAMPTZ;
    v_plan   TEXT;
    v_dur    INTEGER;
    v_newexp TIMESTAMPTZ;
BEGIN
    -- 1. Lock the subscription row; block if an ACTIVE paid/dev plan would be
    --    downgraded. The user_subscriptions row is the SSoT (every user has one
    --    via signup/backfill); fall back to allow if somehow absent.
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';   -- code NOT burned
    END IF;

    -- 2. Atomic single-use claim (also enforces the code's own expiry).
    UPDATE public.plan_codes c
       SET redeemed_by = p_user_id,
           redeemed_at = now()
     WHERE c.code = v_norm
       AND c.redeemed_by IS NULL
       AND (c.expires_at IS NULL OR c.expires_at > now())
    RETURNING c.plan_id INTO v_plan;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;

    -- 3. Grant. Compute expiry EXPLICITLY (renewal trap: the assignment trigger
    --    only fires on a plan_id CHANGE, so renewing the same plan must set it).
    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    -- 3a. SSoT write (upsert; the row exists for all real users, but be safe).
    INSERT INTO public.user_subscriptions
        (user_id, plan_id, status, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, v_plan, 'active', 'code', now(), v_newexp, v_norm)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
        status        = 'active',
        source        = 'code',
        started_at    = now(),
        expires_at    = EXCLUDED.expires_at,
        redeemed_code = EXCLUDED.redeemed_code,
        updated_at    = now();

    -- 3b. Legacy mirror (deploy-order safety; dropped in 080).
    UPDATE public.users u
       SET plan_id = v_plan,
           subscription_expires_at = v_newexp
     WHERE u.user_id = p_user_id;

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_newexp;
END;
$$;

COMMENT ON FUNCTION public.redeem_plan_code(TEXT, UUID) IS
    'Atomically redeem a single-use plan code → activate user_subscriptions (SSoT) '
    'and mirror users.plan_id/expiry during the transition. Blocks downgrade of '
    'active paid/dev plans; stamps expiry explicitly. Called by POST '
    '/api/v1/plans/redeem with the service role.';

------------------------------------------------------------------------
-- 5. Rewrite handle_new_user() — create the users row (terms stamping kept from
--    075) AND seed a LOCKED user_subscriptions row so every account has a record.
--    The subscription insert is in a sub-block so a failure can never break
--    signup (the user would just be locked, recoverable by operator activation).
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    v_user_id uuid;
BEGIN
    INSERT INTO public.users (auth_id, email, full_name_ar, terms_accepted_at, terms_version)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name_ar', NEW.email),
        now(),
        NEW.raw_user_meta_data->>'terms_version'
    )
    RETURNING user_id INTO v_user_id;

    BEGIN
        INSERT INTO public.user_subscriptions (user_id, plan_id, status, source)
        VALUES (v_user_id, NULL, 'locked', 'signup')
        ON CONFLICT (user_id) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        -- Never let the subscription seed break account creation.
        RAISE WARNING 'handle_new_user: user_subscriptions seed failed for %: %',
            v_user_id, SQLERRM;
    END;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

------------------------------------------------------------------------
-- 6. get_user_usage_windows() — rolling usage from the llm_calls ledger. ONE
--    indexed scan of the user's last 30 days computes every window the gate +
--    dialog need, so they always agree. SECURITY INVOKER (the backend calls it
--    with the service role; no SECURITY DEFINER leak to authenticated callers).
--      session = last 5h cost (USD)   weekly = last 7d cost (USD)
--      ocr_pages = last 30d pages     *_oldest = MIN(created_at) in window
--    Points are derived in Python (1 USD = 100 points). resets_at for a rolling
--    window = oldest_in_window + window_length (soonest the used figure drops).
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_user_usage_windows(p_user_id uuid)
RETURNS TABLE(
    session_cost   double precision,
    weekly_cost    double precision,
    ocr_pages      bigint,
    session_oldest timestamptz,
    weekly_oldest  timestamptz,
    ocr_oldest     timestamptz
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        COALESCE(SUM(cost_usd)  FILTER (WHERE created_at >= now() - interval '5 hours'), 0)::double precision,
        COALESCE(SUM(cost_usd)  FILTER (WHERE created_at >= now() - interval '7 days'),  0)::double precision,
        COALESCE(SUM(pages_used) FILTER (WHERE created_at >= now() - interval '30 days'), 0)::bigint,
        MIN(created_at) FILTER (WHERE created_at >= now() - interval '5 hours'),
        MIN(created_at) FILTER (WHERE created_at >= now() - interval '7 days'),
        MIN(created_at) FILTER (WHERE pages_used > 0 AND created_at >= now() - interval '30 days')
    FROM public.llm_calls
    WHERE user_id = p_user_id
      AND created_at >= now() - interval '30 days';
$$;

COMMENT ON FUNCTION public.get_user_usage_windows(uuid) IS
    'Rolling usage windows from the llm_calls ledger (the usage SSoT): session 5h '
    '+ weekly 7d cost (USD) + ocr 30d pages, with each window''s oldest timestamp '
    'for an honest reset countdown. Shared by the quota gate and GET /usage.';
