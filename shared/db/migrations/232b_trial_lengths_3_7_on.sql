-- =============================================================================
-- 232b: new free-trial claims get 3 days (everyone) / 7 days (three groups)
-- =============================================================================
-- Owner, 2026-11-23. Points free_trial_campaign at the two rows 232 created:
--   plan_id             marketing_lawyer_v2 (7 d) → marketing_lawyer_3 (3 d)
--   professions_plan_id marketing_lawyer_14 (14 d) → marketing_lawyer_7 (7 d)
--
-- Apply AFTER the web deploy whose free-trial-content.ts maps 3 days to the
-- 10 / 50 / 25 card and 7 days to the 15 / 74 / 50 card. Before it, a 3-day
-- claim would be shown as «أسبوع».
--
-- free_trial_plan() reads these two columns and free_trial_days() reads the
-- duration from the chosen row, so no function changes. Running trials keep
-- the row they claimed; plan codes keep marketing_lawyer_14.
--
-- Rollback (instant, no deploy; the web copy handles 7 and 14 days too):
--   UPDATE public.free_trial_campaign
--      SET plan_id = 'marketing_lawyer_v2', professions_plan_id = 'marketing_lawyer_14'
--    WHERE id;

BEGIN;

SET LOCAL lock_timeout = '5s';

UPDATE public.free_trial_campaign
   SET plan_id             = 'marketing_lawyer_3',
       professions_plan_id = 'marketing_lawyer_7',
       updated_at          = now()
 WHERE id;

DO $selftest$
BEGIN
    IF (SELECT plan_id || '/' || professions_plan_id FROM public.free_trial_campaign WHERE id)
       IS DISTINCT FROM 'marketing_lawyer_3/marketing_lawyer_7' THEN
        RAISE EXCEPTION '232b: campaign is not 3-day / 7-day';
    END IF;
    IF (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_3') IS DISTINCT FROM 3
       OR (SELECT duration_days FROM public.plans WHERE plan_id = 'marketing_lawyer_7') IS DISTINCT FROM 7 THEN
        RAISE EXCEPTION '232b: 232 not applied or durations wrong';
    END IF;
END;
$selftest$;

COMMIT;
