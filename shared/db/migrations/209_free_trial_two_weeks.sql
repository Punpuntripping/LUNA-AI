-- =============================================================================
-- 209: أسبوعان مجانيان للمهن، وسعر الشهرين لمرة واحدة قبل التجربة
-- =============================================================================
-- Owner decisions, 2026-11-02:
--   * legal, specialist and entrepreneur accounts get two free weeks, not one.
--   * Choosing the free trial instead of Pro at 19.90 for 2 months uses that offer
--     up: from then on the same price runs for 1 month (pro_t1_b).
--
-- A refusal is the claim itself. Closing a popup is not a refusal and nothing is
-- backfilled, so a free_trial_claims row is the whole rule and there is no new
-- table. Accounts that merely saw the offer keep it.
--
-- WHY INSIDE promo_quote. The popup, the limit dialog and checkout all read it, so
-- no surface can quote 2 months once the account has taken the trial.
--
-- "Profession" is profession_first_group, as for Pro tier 1, so editing a
-- profession later cannot buy a longer trial.
--
-- product_docs.pricing and promo_quote are guarded, so a change made since this
-- was written fails the migration instead of being overwritten.
--
-- Rollback:
--   UPDATE public.free_trial_campaign SET professions_plan_id = plan_id;
--   then re-run promo_quote from 204_basic_first_payment_offer.sql.

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF md5(pg_get_functiondef('public.promo_quote(uuid,text)'::regprocedure))
       IS DISTINCT FROM '6a78fc114a97a20eebd65d69953691c3' THEN
        RAISE EXCEPTION '209: promo_quote changed since this was written';
    END IF;
    IF (SELECT md5(d.content_md) FROM public.product_docs d WHERE d.doc_key = 'pricing')
       IS DISTINCT FROM '482b2df7f92c79aa5bbec493651c29ec' THEN
        RAISE EXCEPTION '209: product_docs.pricing changed since this was written';
    END IF;
END;
$guard$;

-- ── 1. Which plan the trial grants ───────────────────────────────────────────

ALTER TABLE public.free_trial_campaign
    ADD COLUMN IF NOT EXISTS professions_plan_id TEXT NOT NULL
        DEFAULT 'marketing_lawyer_14' REFERENCES public.plans(plan_id);

CREATE OR REPLACE FUNCTION public.free_trial_plan(p_user_id uuid)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT CASE
               WHEN u.profession_first_group IN ('legal', 'specialist', 'entrepreneur')
                   THEN c.professions_plan_id
               ELSE c.plan_id
           END
      FROM public.free_trial_campaign c
      JOIN public.users u ON u.user_id = p_user_id
     WHERE c.id;
$function$;

CREATE OR REPLACE FUNCTION public.free_trial_days(p_user_id uuid)
 RETURNS integer
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT p.duration_days
      FROM public.plans p
     WHERE p.plan_id = public.free_trial_plan(p_user_id);
$function$;

REVOKE EXECUTE ON FUNCTION public.free_trial_plan(uuid) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.free_trial_days(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.free_trial_plan(uuid) TO service_role;
GRANT  EXECUTE ON FUNCTION public.free_trial_days(uuid) TO service_role;

-- ── 2. The claim, changed only in where the plan comes from ──────────────────

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

    v_plan := public.free_trial_plan(p_user_id);

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

-- ── 3. The quote: the live body, changed at (209) ────────────────────────────

CREATE OR REPLACE FUNCTION public.promo_quote(p_user_id uuid, p_plan_id text)
 RETURNS TABLE(batch_id text, price_tier smallint, price_sar numeric, months integer)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_camp    RECORD;
    v_eff     TEXT;
    v_group   TEXT;
    v_prof_ok BOOLEAN;
    v_t1_ok   BOOLEAN;
    v_tier    SMALLINT;
    v_b       public.promo_batches%ROWTYPE;
BEGIN
    IF p_user_id IS NULL OR p_plan_id IS NULL OR p_plan_id NOT IN ('basic', 'pro', 'max') THEN
        RETURN;
    END IF;

    SELECT c.enabled AS is_on, c.ladder_v2 AS v2
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;
    IF NOT FOUND OR NOT v_camp.v2 OR NOT v_camp.is_on THEN
        RETURN;
    END IF;

    -- (204) Basic: once per user, whatever they bought before. Nothing below
    -- applies to it: the Pro/Max seat rules, the profession gate and the tier
    -- ladder all describe a recurring plan.
    IF p_plan_id = 'basic' THEN
        IF EXISTS (SELECT 1
                     FROM public.basic_offer_seats s
                    WHERE s.user_id = p_user_id) THEN
            RETURN;
        END IF;

        SELECT * INTO v_b FROM public.promo_active_batch('basic', 1::smallint);
        IF FOUND THEN
            RETURN QUERY
                SELECT v_b.batch_id, v_b.price_tier, round(v_b.price_sar, 2), v_b.months;
        END IF;
        RETURN;
    END IF;

    -- The plan the user is ON right now. The view folds expiry → 'free' (and
    -- locked → NULL); never re-derive it here (183). It is security_invoker:
    -- inside this SECURITY DEFINER function it runs as the owner, exactly as
    -- 183's intro_offer_quote does. One read, by user_id, so the quota-state
    -- lateral runs for this user only.
    SELECT l.effective_plan_id
      INTO v_eff
      FROM public.user_subscriptions_live l
     WHERE l.user_id = p_user_id;

    -- §1.3-1: on Pro or Max now → no new seat for that plan; renewals are
    -- priced by the seat they hold. Max → Pro is a downgrade, never quoted.
    IF v_eff = p_plan_id OR (v_eff = 'max' AND p_plan_id = 'pro') THEN
        RETURN;
    END IF;

    -- (I2) A live seat still inside its window keeps pricing what it prices
    -- (effective_plan_price branch b); a quote here would contradict the
    -- charge. A BATCH seat prices only its own plan. A LEGACY seat prices EVERY
    -- plan (as before 192), so it blocks every quote — except the one
    -- owner-approved switch (A3): a legacy seat claimed by a PRO payment, asked
    -- for MAX, takes the Max quote and is released 'upgraded' by the claim. The
    -- COALESCE keeps a legacy seat with no payment (t.plan_id NULL) blocking.
    IF EXISTS (
        SELECT 1
          FROM public.early_adopter_seats s
          LEFT JOIN public.payment_transactions t ON t.payment_id = s.payment_id
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
           AND s.promo_ends_at > now()
           AND CASE WHEN s.batch_id IS NOT NULL THEN s.plan_id = p_plan_id
                    ELSE NOT COALESCE(t.plan_id = 'pro' AND p_plan_id = 'max', false)
               END
    ) THEN
        RETURN;
    END IF;

    -- The FIRST profession answer (183 §4b), never the current one.
    SELECT u.profession_first_group
      INTO v_group
      FROM public.users u
     WHERE u.user_id = p_user_id;
    v_prof_ok := COALESCE(v_group IN ('legal', 'specialist', 'entrepreneur'), false);

    IF p_plan_id = 'max' THEN
        v_t1_ok := true;                                 -- Max tier 1: everyone
    ELSE
        -- Pro tier 1: the three professions, the intro window (§1.4-9), and
        -- NOT while a Basic term runs (§1.3-2 / A10).
        v_t1_ok := v_prof_ok
                   AND v_eff IS DISTINCT FROM 'basic'
                   AND EXISTS (SELECT 1
                                 FROM public.intro_offer_campaign ic
                                WHERE ic.id
                                  AND ic.enabled
                                  AND now() < ic.ends_at);
    END IF;

    -- Tier 1, else tier 2; each once per plan; batches fill in order. A tier
    -- whose batches are all full falls through to the next (A2).
    FOREACH v_tier IN ARRAY ARRAY[1, 2]::smallint[] LOOP
        CONTINUE WHEN v_tier = 1 AND NOT v_t1_ok;
        CONTINUE WHEN public.promo_tier_used(p_user_id, p_plan_id, v_tier);

        -- (209) Taking the free trial instead of Pro tier 1 uses up the first
        -- batch: from then on the quote starts at the next one.
        IF p_plan_id = 'pro' AND v_tier = 1
           AND EXISTS (SELECT 1
                         FROM public.free_trial_claims f
                        WHERE f.user_id = p_user_id) THEN
            SELECT b.* INTO v_b
              FROM public.promo_batches b
             WHERE b.plan_id = 'pro'
               AND b.price_tier = 1
               AND b.batch_order > 1
               AND b.enabled
               AND (SELECT count(*)
                      FROM public.early_adopter_seats s
                     WHERE s.released_at IS NULL
                       AND s.batch_id = b.batch_id) < b.seat_limit
             ORDER BY b.batch_order
             LIMIT 1;
        ELSE
            SELECT * INTO v_b FROM public.promo_active_batch(p_plan_id, v_tier);
        END IF;
        CONTINUE WHEN NOT FOUND;
        CONTINUE WHEN v_b.profession_only AND NOT v_prof_ok;          -- (I7)

        RETURN QUERY
            SELECT v_b.batch_id, v_b.price_tier, round(v_b.price_sar, 2), v_b.months;
        RETURN;
    END LOOP;

    RETURN;
END;
$function$;

-- ── 4. Router doc: no length, so it stays true for both ──────────────────────

UPDATE public.product_docs
   SET content_md = replace(
           content_md,
           'يظهر عرض أسبوع مجاني مرة واحدة',
           'يظهر عرض تجربة مجانية مرة واحدة'),
       updated_at = now()
 WHERE doc_key = 'pricing';

-- ── 5. Self-test on real rows, undone by the closing RAISE ───────────────────

DO $selftest$
DECLARE
    v_pro   UUID;
    v_pro2  UUID;
    v_ind   UUID;
    v_q     RECORD;
    v_r     RECORD;
    v_exp   TIMESTAMPTZ;
BEGIN
    SELECT u.user_id INTO v_pro
      FROM public.users u
     WHERE u.profession_first_group IN ('legal', 'specialist', 'entrepreneur')
       AND public.free_trial_eligible(u.user_id)
       AND EXISTS (SELECT 1 FROM public.promo_quote(u.user_id, 'pro') q WHERE q.batch_id = 'pro_t1_a')
     ORDER BY u.created_at DESC
     LIMIT 1;

    SELECT u.user_id INTO v_pro2
      FROM public.users u
     WHERE u.profession_first_group IN ('legal', 'specialist', 'entrepreneur')
       AND public.free_trial_eligible(u.user_id)
       AND u.user_id <> v_pro
       AND EXISTS (SELECT 1 FROM public.promo_quote(u.user_id, 'pro') q WHERE q.batch_id = 'pro_t1_a')
     ORDER BY u.created_at DESC
     LIMIT 1;

    SELECT u.user_id INTO v_ind
      FROM public.users u
     WHERE COALESCE(u.profession_first_group, 'individual') NOT IN ('legal', 'specialist', 'entrepreneur')
       AND public.free_trial_eligible(u.user_id)
     ORDER BY u.created_at DESC
     LIMIT 1;

    IF v_pro IS NULL OR v_pro2 IS NULL OR v_ind IS NULL THEN
        RAISE EXCEPTION '209 self-test: needs two professional accounts and one other';
    END IF;

    IF has_function_privilege('authenticated', 'public.free_trial_days(uuid)', 'EXECUTE')
       OR has_function_privilege('anon', 'public.free_trial_plan(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '209 self-test [clients must not call these]';
    END IF;

    BEGIN
        -- A. Length follows the first profession
        IF public.free_trial_days(v_pro) IS DISTINCT FROM 14 THEN
            RAISE EXCEPTION '209 self-test [professions get 14]: %', public.free_trial_days(v_pro);
        END IF;
        IF public.free_trial_days(v_ind) IS DISTINCT FROM 7 THEN
            RAISE EXCEPTION '209 self-test [everyone else gets 7]: %', public.free_trial_days(v_ind);
        END IF;

        -- B. Before the claim: 2 months
        SELECT * INTO v_q FROM public.promo_checkout_price(v_pro, 'pro');
        IF v_q.batch_id IS DISTINCT FROM 'pro_t1_a' OR v_q.months IS DISTINCT FROM 2
           OR v_q.price_sar IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '209 self-test [before claim]: % % %', v_q.batch_id, v_q.months, v_q.price_sar;
        END IF;

        -- C. The claim grants 14 days
        SELECT * INTO v_r FROM public.claim_free_trial(v_pro);
        IF v_r.action IS DISTINCT FROM 'claimed' OR v_r.trial_plan_id IS DISTINCT FROM 'marketing_lawyer_14' THEN
            RAISE EXCEPTION '209 self-test [claim]: % %', v_r.action, v_r.trial_plan_id;
        END IF;
        SELECT s.expires_at INTO v_exp FROM public.user_subscriptions s WHERE s.user_id = v_pro;
        IF v_exp < now() + interval '14 days' - interval '1 minute'
           OR v_exp > now() + interval '14 days' + interval '1 minute' THEN
            RAISE EXCEPTION '209 self-test [14 days]: %', v_exp;
        END IF;

        -- D. After the claim: same price, 1 month, at quote and at checkout
        SELECT * INTO v_q FROM public.promo_checkout_price(v_pro, 'pro');
        IF v_q.batch_id IS DISTINCT FROM 'pro_t1_b' OR v_q.months IS DISTINCT FROM 1
           OR v_q.price_sar IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '209 self-test [after claim]: % % %', v_q.batch_id, v_q.months, v_q.price_sar;
        END IF;

        -- E. Someone who has not claimed keeps 2 months
        SELECT * INTO v_q FROM public.promo_checkout_price(v_pro2, 'pro');
        IF v_q.batch_id IS DISTINCT FROM 'pro_t1_a' OR v_q.months IS DISTINCT FROM 2 THEN
            RAISE EXCEPTION '209 self-test [unclaimed keeps 2 months]: % %', v_q.batch_id, v_q.months;
        END IF;

        -- F. Everyone else: a 7-day trial, and no tier 1
        SELECT * INTO v_r FROM public.claim_free_trial(v_ind);
        IF v_r.trial_plan_id IS DISTINCT FROM 'marketing_lawyer' THEN
            RAISE EXCEPTION '209 self-test [one week plan]: %', v_r.trial_plan_id;
        END IF;

        RAISE EXCEPTION 'rollback' USING ERRCODE = 'LN209';
    EXCEPTION WHEN SQLSTATE 'LN209' THEN
        RAISE NOTICE '209 self-test: all checks passed, test rows rolled back';
    END;
END;
$selftest$;

COMMIT;
