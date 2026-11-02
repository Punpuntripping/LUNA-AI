-- =============================================================================
-- 208: الأسبوع المجاني لمن اشترك سابقاً وانتهى اشتراكه
-- =============================================================================
-- Owner decision, 2026-11-02: accounts that paid before qualify too, once they
-- are back on free. Active subscribers still do not: the effective-plan test
-- keeps them out, and it also keeps out anyone in the renewal grace window,
-- because get_user_quota_state reports the paid plan during it.
--
-- WHY THE 2-DAY BUFFER. renewal_service still selects a term for up to 2 hours
-- after it lapses, and only for source = 'payment'. A claim in that gap would set
-- source = 'trial' and silently cancel a renewal that was about to run, so a paid
-- term has to be 2 days past its end before the week is open to it. That clears
-- the whole retry ladder with room to spare.
--
-- Rollback: re-run the function from 205_free_trial_week.sql.

BEGIN;

SET LOCAL lock_timeout = '5s';

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
               AND (s.source IS DISTINCT FROM 'payment'
                    OR s.expires_at IS NULL
                    OR s.expires_at < now() - interval '2 days')
               AND NOT EXISTS (SELECT 1
                                 FROM public.free_trial_claims f
                                WHERE f.user_id = p_user_id)
          FROM public.free_trial_campaign c
          JOIN public.users u ON u.user_id = p_user_id
          JOIN public.user_subscriptions s ON s.user_id = p_user_id
          JOIN public.user_subscriptions_live l ON l.user_id = p_user_id
         WHERE c.id), false);
$function$;

-- Self-test on real rows, undone by the closing RAISE. Any failed check aborts.
DO $selftest$
DECLARE
    v_lapsed   UUID;
    v_active   UUID;
    v_eligible INTEGER;
BEGIN
    SELECT s.user_id INTO v_lapsed
      FROM public.user_subscriptions s
      JOIN public.user_subscriptions_live l ON l.user_id = s.user_id
     WHERE l.effective_plan_id = 'free'
       AND s.source = 'payment'
       AND s.redeemed_code IS NULL
       AND s.expires_at < now() - interval '2 days'
       AND EXISTS (SELECT 1 FROM public.payment_transactions t
                    WHERE t.user_id = s.user_id AND t.status = 'paid')
       AND NOT EXISTS (SELECT 1 FROM public.free_trial_claims f WHERE f.user_id = s.user_id)
     LIMIT 1;

    SELECT l.user_id INTO v_active
      FROM public.user_subscriptions_live l
     WHERE l.effective_plan_id IN ('basic', 'pro', 'max')
     LIMIT 1;

    IF v_lapsed IS NULL OR v_active IS NULL THEN
        RAISE EXCEPTION '208 self-test: needs a lapsed buyer and an active subscriber';
    END IF;

    SELECT count(*) INTO v_eligible
      FROM public.users u
     WHERE public.free_trial_eligible(u.user_id);

    BEGIN
        IF NOT public.free_trial_eligible(v_lapsed) THEN
            RAISE EXCEPTION '208 self-test [lapsed buyer qualifies]';
        END IF;
        IF public.free_trial_eligible(v_active) THEN
            RAISE EXCEPTION '208 self-test [active subscriber stays out]';
        END IF;

        -- A term that ended an hour ago may still renew.
        UPDATE public.user_subscriptions s
           SET expires_at = now() - interval '1 hour'
         WHERE s.user_id = v_lapsed;
        IF public.free_trial_eligible(v_lapsed) THEN
            RAISE EXCEPTION '208 self-test [recent term stays out]';
        END IF;

        RAISE EXCEPTION 'rollback' USING ERRCODE = 'LN208';
    EXCEPTION WHEN SQLSTATE 'LN208' THEN
        RAISE NOTICE '208 self-test passed, % accounts eligible', v_eligible;
    END;
END;
$selftest$;

COMMIT;
