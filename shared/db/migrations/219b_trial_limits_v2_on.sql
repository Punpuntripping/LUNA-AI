-- =============================================================================
-- 219b: new 7-day trial claims get marketing_lawyer_v2 (10 / 50 / 25)
-- =============================================================================
-- Owner, 2026-11-18, after 219 was applied: only the 7-day promo drops to
-- 10 / 50 / 25. The 14-day trial (legal / specialist / entrepreneur) stays on
-- marketing_lawyer_14 at 15 / 74 / 50, so the marketing_lawyer_14_v2 row that
-- 219 created is removed unused, and only free_trial_campaign.plan_id moves.
--
-- Apply AFTER the web deploy whose free-trial-content.ts shows 10 / 50 / 25 on
-- the 7-day card. Before it the card would promise 15 / 74 / 50.
--
-- free_trial_plan() reads plan_id for every profession outside the three groups,
-- and free_trial_days() reads the duration from that row, so no function
-- changes. Running trials keep the row they claimed.
--
-- Rollback (instant, no deploy):
--   UPDATE public.free_trial_campaign SET plan_id = 'marketing_lawyer' WHERE id;

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM public.user_subscriptions WHERE plan_id = 'marketing_lawyer_14_v2')
       OR EXISTS (SELECT 1 FROM public.free_trial_claims WHERE plan_id = 'marketing_lawyer_14_v2')
       OR EXISTS (SELECT 1 FROM public.plan_codes WHERE plan_id = 'marketing_lawyer_14_v2')
       OR EXISTS (SELECT 1 FROM public.free_trial_campaign WHERE professions_plan_id = 'marketing_lawyer_14_v2') THEN
        RAISE EXCEPTION '219b: marketing_lawyer_14_v2 is in use, not dropping it';
    END IF;
END;
$guard$;

DELETE FROM public.plans WHERE plan_id = 'marketing_lawyer_14_v2';

UPDATE public.free_trial_campaign
   SET plan_id    = 'marketing_lawyer_v2',
       updated_at = now()
 WHERE id;

DO $selftest$
BEGIN
    IF (SELECT plan_id || '/' || professions_plan_id FROM public.free_trial_campaign WHERE id)
       IS DISTINCT FROM 'marketing_lawyer_v2/marketing_lawyer_14' THEN
        RAISE EXCEPTION '219b: campaign is not 7-day v2 / 14-day unchanged';
    END IF;
    IF EXISTS (SELECT 1 FROM public.plans WHERE plan_id = 'marketing_lawyer_14_v2') THEN
        RAISE EXCEPTION '219b: marketing_lawyer_14_v2 still present';
    END IF;
END;
$selftest$;

COMMIT;
