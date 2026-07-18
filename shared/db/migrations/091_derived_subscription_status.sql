-- ════════════════════════════════════════════════════════════════════════════
-- 091 — derived subscription status (kill the lying `status` column)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Problem: user_subscriptions.status was stamped 'active' on assignment and
-- never touched again, so an expired subscription (expires_at in the past)
-- still read status='active', plan_id='max' — while the quota gate
-- (shared/quota/__init__.py _user_limits) had already fallen the user back to
-- the free plan. The column was an operator-facing hint that nothing enforced
-- and time made false.
--
-- Fix: status is 100% derivable from (plan_id, expires_at), so stop storing
-- it. The stored row keeps only facts (what plan was assigned, until when);
-- interpretation lives in a view computed at read time — identical logic to
-- the gate, so a DB glance can never disagree with enforcement again.
--
--   * locked   ⇔ plan_id IS NULL
--   * expired  ⇔ expires_at <= now()
--   * active   ⇔ otherwise
--   * effective_plan_id: 'free' when expired (the gate's fallback), else plan_id
--
-- Verified before writing: no Python code, RLS policy, or view reads `status`;
-- its only writers are the three functions rewritten below (prod definitions
-- fetched live 2026-07-15 — includes the 089 handle_new_user free-signup change).
--
-- Order matters: rewrite the writer functions FIRST, then drop the column.

-- ── 1. Assignment trigger — stamp expiry only (status no longer stored) ──────

CREATE OR REPLACE FUNCTION public.handle_subscription_assignment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    d INTEGER;
BEGIN
    IF NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN
        IF NEW.plan_id IS NULL THEN
            NEW.expires_at := NULL;
        ELSE
            SELECT duration_days INTO d FROM public.plans WHERE plan_id = NEW.plan_id;
            NEW.expires_at := CASE WHEN d IS NULL THEN NULL
                                   ELSE now() + make_interval(days => d) END;
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ── 2. redeem_plan_code — drop the status write, everything else unchanged ───

CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code text, p_user_id uuid)
RETURNS TABLE(plan_id text, name_ar text, expires_at timestamp with time zone)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_norm      TEXT := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    v_cur       TEXT;
    v_curexp    TIMESTAMPTZ;
    v_plan      TEXT;
    v_max       INTEGER;
    v_used      INTEGER;
    v_redeemers UUID[];
    v_codeexp   TIMESTAMPTZ;
    v_dur       INTEGER;
    v_newexp    TIMESTAMPTZ;
BEGIN
    -- 1. Downgrade guard.
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';
    END IF;

    -- 2. Lock the code row and validate existence -> shelf-life -> dedup -> capacity.
    SELECT c.plan_id, c.max_uses, c.uses_count, c.redeemed_by_users, c.expires_at
      INTO v_plan, v_max, v_used, v_redeemers, v_codeexp
      FROM public.plan_codes c
     WHERE c.code = v_norm
       FOR UPDATE;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF v_codeexp IS NOT NULL AND v_codeexp <= now() THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF p_user_id = ANY(v_redeemers) THEN
        RAISE EXCEPTION 'code_already_redeemed';
    END IF;
    IF v_used >= v_max THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;

    -- 3. Consume one slot (atomic under the row lock held above).
    UPDATE public.plan_codes c
       SET uses_count        = c.uses_count + 1,
           redeemed_by_users = array_append(c.redeemed_by_users, p_user_id),
           redeemed_by       = p_user_id,
           redeemed_at       = now()
     WHERE c.code = v_norm;

    -- 4. Grant.
    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    INSERT INTO public.user_subscriptions
        (user_id, plan_id, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, v_plan, 'code', now(), v_newexp, v_norm)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
        source        = 'code',
        started_at    = now(),
        expires_at    = EXCLUDED.expires_at,
        redeemed_code = EXCLUDED.redeemed_code,
        updated_at    = now();

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_newexp;
END;
$$;

-- ── 3. handle_new_user — drop the status write from the signup seed ─────────

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
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
        INSERT INTO public.user_subscriptions (user_id, plan_id, source)
        VALUES (v_user_id, 'free', 'signup')
        ON CONFLICT (user_id) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'handle_new_user: user_subscriptions seed failed for %: %',
            v_user_id, SQLERRM;
    END;

    RETURN NEW;
END;
$$;

-- ── 4. Drop the stored column ────────────────────────────────────────────────

ALTER TABLE public.user_subscriptions DROP COLUMN IF EXISTS status;

-- ── 5. Live view — the operator glance surface, always truthful ──────────────
-- security_invoker: base-table RLS applies to whoever queries it (a user sees
-- only their own row; Studio/service role sees all).

CREATE OR REPLACE VIEW public.user_subscriptions_live
WITH (security_invoker = true) AS
SELECT
    s.user_id,
    u.email,
    s.plan_id,
    p.name_ar AS plan_name_ar,
    CASE
        WHEN s.plan_id IS NULL                                   THEN 'locked'
        WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'expired'
        ELSE 'active'
    END AS status,
    (s.expires_at IS NOT NULL AND s.expires_at <= now()) AS is_expired,
    CASE
        WHEN s.plan_id IS NULL                                   THEN NULL
        WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'free'
        ELSE s.plan_id
    END AS effective_plan_id,
    ep.name_ar AS effective_name_ar,
    s.source,
    s.started_at,
    s.expires_at,
    s.redeemed_code,
    s.points_session_override,
    s.points_weekly_override,
    s.points_monthly_override,
    s.ocr_pages_monthly_override,
    s.web_calls_monthly_override,
    s.created_at,
    s.updated_at
FROM public.user_subscriptions s
JOIN public.users u  ON u.user_id = s.user_id
LEFT JOIN public.plans p  ON p.plan_id = s.plan_id
LEFT JOIN public.plans ep ON ep.plan_id = CASE
        WHEN s.plan_id IS NULL                                   THEN NULL
        WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'free'
        ELSE s.plan_id END;

COMMENT ON VIEW public.user_subscriptions_live IS
    'Truthful, read-time view of subscription state. status/effective_plan_id '
    'are DERIVED from (plan_id, expires_at) with the same logic as the quota '
    'gate (_user_limits): expired time-boxed plan → free fallback. Operators '
    'should glance HERE, not at the base table (091).';

COMMENT ON TABLE public.user_subscriptions IS
    'Per-user subscription identity — the SSoT the quota gate reads '
    '(plan_id, expires_at, overrides). Stores FACTS only; live status is '
    'derived in user_subscriptions_live (the old stored status column lied '
    'once expiry passed — dropped in 091). Limits live in the plans catalog; '
    'usage lives in the llm_calls ledger.';
