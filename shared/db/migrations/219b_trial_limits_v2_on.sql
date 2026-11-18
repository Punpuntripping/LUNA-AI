-- =============================================================================
-- 219b: new free-trial claims get the v2 rows (10 / 50 / 25)
-- =============================================================================
-- Apply AFTER 219 and AFTER the web deploy whose free-trial-content.ts says
-- «10 نقاط لكل جلسة» / «50 نقطة» / «25 صفحة». Before that deploy the card would
-- promise 15 / 74 / 50 while the claim grants less.
--
-- free_trial_plan() reads these two columns, and free_trial_days() reads the
-- duration from the chosen row, so no function changes. Running trials keep
-- the row they claimed.
--
-- Rollback (instant, no deploy):
--   UPDATE public.free_trial_campaign
--      SET plan_id = 'marketing_lawyer', professions_plan_id = 'marketing_lawyer_14'
--    WHERE id;

BEGIN;

UPDATE public.free_trial_campaign
   SET plan_id             = 'marketing_lawyer_v2',
       professions_plan_id = 'marketing_lawyer_14_v2',
       updated_at          = now()
 WHERE id;

DO $selftest$
BEGIN
    IF (SELECT plan_id || '/' || professions_plan_id FROM public.free_trial_campaign WHERE id)
       IS DISTINCT FROM 'marketing_lawyer_v2/marketing_lawyer_14_v2' THEN
        RAISE EXCEPTION '219b: campaign not pointed at the v2 rows';
    END IF;
END;
$selftest$;

COMMIT;
