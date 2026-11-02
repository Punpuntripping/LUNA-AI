-- =============================================================================
-- 207: رفع حدود صفحات استخراج النص — الأساسية والترويجية 50، الاحترافية 150، القصوى 1000
-- =============================================================================
-- Owner decision, 2026-11-02, the same day as 206:
--   basic 25 → 50 · marketing_lawyer + marketing_lawyer_14 25 → 50
--   pro 80 → 150 · max 500 → 1000. free (3) is unchanged.
--
-- Applied BEFORE the frontend ships, so no card ever promises more than the gate
-- allows. Effective at once for current subscribers; spent pages are not reset.
--
-- The guard refuses to run over a value someone has since changed by hand.
--
-- Rollback:
--   UPDATE public.plans SET ocr_pages_monthly = 25
--    WHERE plan_id IN ('basic', 'marketing_lawyer', 'marketing_lawyer_14');
--   UPDATE public.plans SET ocr_pages_monthly = 80 WHERE plan_id = 'pro';
--   UPDATE public.plans SET ocr_pages_monthly = 500 WHERE plan_id = 'max';

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF (SELECT count(*) FROM public.plans p
         WHERE (p.plan_id IN ('basic', 'marketing_lawyer', 'marketing_lawyer_14') AND p.ocr_pages_monthly = 25)
            OR (p.plan_id = 'pro' AND p.ocr_pages_monthly = 80)
            OR (p.plan_id = 'max' AND p.ocr_pages_monthly = 500)) <> 5 THEN
        RAISE EXCEPTION '207: a plan is not at the value this was written against';
    END IF;
END;
$guard$;

UPDATE public.plans SET ocr_pages_monthly = 50, updated_at = now()
 WHERE plan_id IN ('basic', 'marketing_lawyer', 'marketing_lawyer_14');
UPDATE public.plans SET ocr_pages_monthly = 150, updated_at = now() WHERE plan_id = 'pro';
UPDATE public.plans SET ocr_pages_monthly = 1000, updated_at = now() WHERE plan_id = 'max';

DO $verify$
DECLARE
    v TEXT;
BEGIN
    SELECT string_agg(p.plan_id || '=' || p.ocr_pages_monthly, ' ' ORDER BY p.plan_id) INTO v
      FROM public.plans p
     WHERE p.plan_id IN ('basic', 'free', 'marketing_lawyer', 'marketing_lawyer_14', 'max', 'pro');
    IF v IS DISTINCT FROM
       'basic=50 free=3 marketing_lawyer=50 marketing_lawyer_14=50 max=1000 pro=150' THEN
        RAISE EXCEPTION '207: unexpected limits: %', v;
    END IF;
    RAISE NOTICE '207: %', v;
END;
$verify$;

COMMIT;
