-- =============================================================================
-- 205: الأسبوع المجاني — أسبوع مجاني مرة واحدة للحسابات الجديدة عند انتهاء الرصيد
-- =============================================================================
-- Owner decisions, 2026-11-02:
--   * Any NEW account, of any profession, gets one free week when it runs out of
--     its free allowance. It claims the week from the limit dialog.
--   * Same allowance as the promo plan `marketing_lawyer`, and that row is what
--     gets granted. A trial user can still buy Pro at any tier.
--   * No card and no charge: at the end of the week the account is simply free
--     again, through the same expiry fallback every time-boxed plan uses.
--
-- SHIPS OFF. free_trial_campaign.enabled starts false, so applying this changes
-- nothing for anyone. Order:
--   1. apply this migration;
--   2. deploy backend + frontend;
--   3. apply 205b_free_trial_launch.sql (trial on, 9.80 off, router docs).
-- Off switch: UPDATE public.free_trial_campaign SET enabled = false;
-- A week already claimed runs to its end.
--
-- WHY "NEW" IS A DATE AND NOT A FLAG ON THE USER. The launch instant lives in one
-- row, so widening the offer to existing free accounts is one UPDATE of
-- new_accounts_from and needs no backfill.
--
-- WHY A CLAIM IS NEVER DELETED. One row means one thing, forever: this user has
-- had the week. The row survives the plan expiring, a later purchase, or the
-- account being deleted (user_id goes NULL, the count stays right).

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The switch ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.free_trial_campaign (
    id                BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    enabled           BOOLEAN NOT NULL DEFAULT false,
    plan_id           TEXT NOT NULL REFERENCES public.plans(plan_id),
    -- Accounts created at or after this instant are "new".
    new_accounts_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.free_trial_campaign (id, enabled, plan_id)
VALUES (true, false, 'marketing_lawyer')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.free_trial_campaign ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.free_trial_campaign FROM PUBLIC;
REVOKE ALL ON TABLE public.free_trial_campaign FROM anon;
REVOKE ALL ON TABLE public.free_trial_campaign FROM authenticated;
GRANT  ALL ON TABLE public.free_trial_campaign TO service_role;

COMMENT ON TABLE public.free_trial_campaign IS
    '205: one-row switch for الأسبوع المجاني. enabled, the plan granted, and the '
    'instant from which an account counts as new. Service-role only.';

-- ── 2. The ledger ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.free_trial_claims (
    claim_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- SET NULL like basic_offer_seats: a deleted account's claim stays counted.
    user_id    UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    plan_id    TEXT NOT NULL REFERENCES public.plans(plan_id),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT free_trial_claims_one_per_user UNIQUE (user_id)
);

ALTER TABLE public.free_trial_claims ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.free_trial_claims FROM PUBLIC;
REVOKE ALL ON TABLE public.free_trial_claims FROM anon;
REVOKE ALL ON TABLE public.free_trial_claims FROM authenticated;
GRANT  ALL ON TABLE public.free_trial_claims TO service_role;

COMMENT ON TABLE public.free_trial_claims IS
    '205: one row per user who claimed الأسبوع المجاني. Never deleted: it is what '
    'makes the week once per user. Service-role only.';

-- ── 3. Who may claim ─────────────────────────────────────────────────────────
-- One definition, read by the offer endpoint and by the claim itself.
--   * the campaign is on, and the account is new;
--   * the account is on free right now (the view folds expiry → 'free');
--   * it never paid, was never refunded, and never redeemed a code;
--   * it has not had the week.
-- user_subscriptions_live is security_invoker: inside this SECURITY DEFINER
-- function it runs as the owner, as in promo_quote (204).

CREATE OR REPLACE FUNCTION public.free_trial_eligible(p_user_id uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT COALESCE((
        SELECT c.enabled
               AND u.created_at >= c.new_accounts_from
               AND l.effective_plan_id = 'free'
               AND s.redeemed_code IS NULL
               AND NOT EXISTS (SELECT 1
                                 FROM public.free_trial_claims f
                                WHERE f.user_id = p_user_id)
               AND NOT EXISTS (SELECT 1
                                 FROM public.payment_transactions t
                                WHERE t.user_id = p_user_id
                                  AND t.status IN ('paid', 'refunded'))
          FROM public.free_trial_campaign c
          JOIN public.users u ON u.user_id = p_user_id
          JOIN public.user_subscriptions s ON s.user_id = p_user_id
          JOIN public.user_subscriptions_live l ON l.user_id = p_user_id
         WHERE c.id), false);
$function$;

REVOKE EXECUTE ON FUNCTION public.free_trial_eligible(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.free_trial_eligible(uuid) TO service_role;

-- ── 4. The claim ─────────────────────────────────────────────────────────────
-- usage_reset_at = now(): points and OCR windows count only calls after it
-- (migration 137), so what the user spent on free does not eat the week. It also
-- anchors the library period and the deep-search count, for the same reason.
-- trg_user_subscriptions_assignment stamps expires_at from plans.duration_days,
-- the same as every other grant.

CREATE OR REPLACE FUNCTION public.claim_free_trial(p_user_id uuid)
 RETURNS TABLE(trial_plan_id text, trial_name_ar text, trial_expires_at timestamptz, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_plan TEXT;
    v_exp  TIMESTAMPTZ;
BEGIN
    -- Serialises two taps, and two tabs, on the same account.
    PERFORM 1
       FROM public.user_subscriptions s
      WHERE s.user_id = p_user_id
        FOR UPDATE;

    IF NOT FOUND OR NOT public.free_trial_eligible(p_user_id) THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::timestamptz, 'not_eligible'::text;
        RETURN;
    END IF;

    SELECT c.plan_id INTO v_plan FROM public.free_trial_campaign c WHERE c.id;

    UPDATE public.user_subscriptions s
       SET plan_id        = v_plan,
           source         = 'trial',
           started_at     = now(),
           usage_reset_at = now()
     WHERE s.user_id = p_user_id
    RETURNING s.expires_at INTO v_exp;

    INSERT INTO public.free_trial_claims AS f (user_id, plan_id, expires_at)
    VALUES (p_user_id, v_plan, v_exp);

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_exp,
               'claimed'::text;
END;
$function$;

REVOKE EXECUTE ON FUNCTION public.claim_free_trial(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.claim_free_trial(uuid) TO service_role;

-- ── 5. Self-test ─────────────────────────────────────────────────────────────
-- Runs against the real functions, then undoes all of it: the closing RAISE
-- rolls this block back, and any failed check aborts the whole migration.

DO $selftest$
DECLARE
    v_fresh   UUID;
    v_fresh2  UUID;
    v_old     UUID;
    v_paid    UUID;
    v_from    TIMESTAMPTZ;
    v_r       RECORD;
    v_n       INTEGER;
    v_eff     TEXT;
    v_source  TEXT;
    v_reset   TIMESTAMPTZ;
    v_exp     TIMESTAMPTZ;
    v_dur     INTEGER;
    v_pro     NUMERIC;
BEGIN
    -- Two never-paid free accounts, the newest two.
    SELECT u.user_id INTO v_fresh
      FROM public.users u
      JOIN public.user_subscriptions s ON s.user_id = u.user_id
     WHERE s.plan_id = 'free'
       AND s.redeemed_code IS NULL
       AND NOT EXISTS (SELECT 1 FROM public.payment_transactions t
                        WHERE t.user_id = u.user_id AND t.status IN ('paid', 'refunded'))
     ORDER BY u.created_at DESC
     LIMIT 1;

    SELECT u.user_id INTO v_fresh2
      FROM public.users u
      JOIN public.user_subscriptions s ON s.user_id = u.user_id
     WHERE s.plan_id = 'free'
       AND s.redeemed_code IS NULL
       AND u.user_id <> v_fresh
       AND NOT EXISTS (SELECT 1 FROM public.payment_transactions t
                        WHERE t.user_id = u.user_id AND t.status IN ('paid', 'refunded'))
     ORDER BY u.created_at DESC
     LIMIT 1;

    SELECT t.user_id INTO v_paid
      FROM public.payment_transactions t
     WHERE t.status = 'paid' AND t.user_id IS NOT NULL
     LIMIT 1;

    IF v_fresh2 IS NULL OR v_paid IS NULL THEN
        RAISE NOTICE '205 self-test: skipped, needs two free accounts and a buyer';
        RETURN;
    END IF;

    v_old := v_fresh2;
    SELECT u.created_at INTO v_from FROM public.users u WHERE u.user_id = v_fresh;

    BEGIN
        -- Grants and RLS
        IF has_table_privilege('anon', 'public.free_trial_claims', 'SELECT')
           OR has_table_privilege('authenticated', 'public.free_trial_claims', 'SELECT')
           OR has_table_privilege('authenticated', 'public.free_trial_campaign', 'SELECT') THEN
            RAISE EXCEPTION '205 self-test [clients must not read]';
        END IF;
        IF has_function_privilege('authenticated', 'public.claim_free_trial(uuid)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.claim_free_trial(uuid)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.free_trial_eligible(uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION '205 self-test [clients must not claim]';
        END IF;
        IF NOT (SELECT c.relrowsecurity FROM pg_class c WHERE c.oid = 'public.free_trial_claims'::regclass)
           OR NOT (SELECT c.relrowsecurity FROM pg_class c WHERE c.oid = 'public.free_trial_campaign'::regclass) THEN
            RAISE EXCEPTION '205 self-test [RLS must be on]';
        END IF;

        -- A. Switched off: nobody, whatever their date
        UPDATE public.free_trial_campaign SET new_accounts_from = v_from, enabled = false;
        IF public.free_trial_eligible(v_fresh) THEN
            RAISE EXCEPTION '205 self-test [off: not eligible]';
        END IF;
        SELECT * INTO v_r FROM public.claim_free_trial(v_fresh);
        IF v_r.action IS DISTINCT FROM 'not_eligible' THEN
            RAISE EXCEPTION '205 self-test [off: no claim]: %', v_r.action;
        END IF;

        -- B. On: the new account qualifies, the older one does not
        UPDATE public.free_trial_campaign SET enabled = true;
        IF NOT public.free_trial_eligible(v_fresh) THEN
            RAISE EXCEPTION '205 self-test [on: new account]';
        END IF;
        IF public.free_trial_eligible(v_old) THEN
            RAISE EXCEPTION '205 self-test [on: older account]';
        END IF;
        SELECT * INTO v_r FROM public.claim_free_trial(v_old);
        IF v_r.action IS DISTINCT FROM 'not_eligible' THEN
            RAISE EXCEPTION '205 self-test [older account claims]: %', v_r.action;
        END IF;

        -- C. A buyer never qualifies, even dated as new
        UPDATE public.free_trial_campaign SET new_accounts_from = '-infinity';
        IF public.free_trial_eligible(v_paid) THEN
            RAISE EXCEPTION '205 self-test [buyer]';
        END IF;
        UPDATE public.free_trial_campaign SET new_accounts_from = v_from;

        -- D. The claim grants the plan, the week and a clean window
        SELECT * INTO v_r FROM public.claim_free_trial(v_fresh);
        IF v_r.action IS DISTINCT FROM 'claimed'
           OR v_r.trial_plan_id IS DISTINCT FROM 'marketing_lawyer'
           OR v_r.trial_expires_at IS NULL THEN
            RAISE EXCEPTION '205 self-test [claim]: % % %', v_r.action, v_r.trial_plan_id, v_r.trial_expires_at;
        END IF;

        SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = 'marketing_lawyer';
        SELECT s.source, s.usage_reset_at, s.expires_at INTO v_source, v_reset, v_exp
          FROM public.user_subscriptions s WHERE s.user_id = v_fresh;
        IF v_source IS DISTINCT FROM 'trial' OR v_reset IS NULL
           OR v_exp < now() + make_interval(days => v_dur) - interval '1 minute'
           OR v_exp > now() + make_interval(days => v_dur) + interval '1 minute' THEN
            RAISE EXCEPTION '205 self-test [grant]: % % %', v_source, v_reset, v_exp;
        END IF;

        SELECT l.effective_plan_id INTO v_eff
          FROM public.user_subscriptions_live l WHERE l.user_id = v_fresh;
        IF v_eff IS DISTINCT FROM 'marketing_lawyer' THEN
            RAISE EXCEPTION '205 self-test [effective plan]: %', v_eff;
        END IF;

        -- E. Once per user, including after the week has ended
        SELECT * INTO v_r FROM public.claim_free_trial(v_fresh);
        IF v_r.action IS DISTINCT FROM 'not_eligible' THEN
            RAISE EXCEPTION '205 self-test [claim twice]: %', v_r.action;
        END IF;

        -- F. The week ends with no charge and the account is free again
        UPDATE public.user_subscriptions s
           SET expires_at = now() - interval '1 minute'
         WHERE s.user_id = v_fresh;
        SELECT l.effective_plan_id INTO v_eff
          FROM public.user_subscriptions_live l WHERE l.user_id = v_fresh;
        IF v_eff IS DISTINCT FROM 'free' THEN
            RAISE EXCEPTION '205 self-test [after expiry]: %', v_eff;
        END IF;
        IF public.free_trial_eligible(v_fresh) THEN
            RAISE EXCEPTION '205 self-test [no second week]';
        END IF;
        SELECT count(*) INTO v_n
          FROM public.payment_transactions t
         WHERE t.user_id = v_fresh;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '205 self-test [no payment row]: %', v_n;
        END IF;

        -- G. Pro stays quotable during the week, on the same terms as off it
        UPDATE public.user_subscriptions s
           SET expires_at = now() + interval '1 day'
         WHERE s.user_id = v_fresh;
        SELECT q.price_sar INTO v_pro FROM public.promo_quote(v_fresh, 'pro') q;
        IF EXISTS (SELECT 1 FROM public.early_adopter_campaign c WHERE c.id AND c.enabled AND c.ladder_v2)
           AND v_pro IS NULL THEN
            RAISE EXCEPTION '205 self-test [pro quote during the week]';
        END IF;

        RAISE EXCEPTION 'rollback' USING ERRCODE = 'LN205';
    EXCEPTION WHEN SQLSTATE 'LN205' THEN
        RAISE NOTICE '205 self-test: all checks passed, test rows rolled back';
    END;
END;
$selftest$;

COMMIT;

-- Rollback, once applied:
--   UPDATE public.free_trial_campaign SET enabled = false;
-- That is the whole off switch. free_trial_claims is the record of who had the
-- week, so keep it.
