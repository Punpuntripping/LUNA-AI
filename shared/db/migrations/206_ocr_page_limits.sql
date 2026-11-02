-- =============================================================================
-- 206: رفع حدود صفحات استخراج النص — الأساسية والترويجية 25، الاحترافية 80
-- =============================================================================
-- Owner decision, 2026-11-02:
--   basic 15 → 25 · marketing_lawyer + marketing_lawyer_14 20 → 25 · pro 40 → 80.
-- free (3) and max (500) are unchanged.
--
-- Applied BEFORE the frontend ships, so no card ever promises more than the gate
-- allows. get_user_quota_state reads these rows on every send, so it takes effect
-- at once for current subscribers; nobody's spent pages are reset.
--
-- The guard refuses to run over a value someone has since changed by hand.
--
-- Rollback:
--   UPDATE public.plans SET ocr_pages_monthly = 15 WHERE plan_id = 'basic';
--   UPDATE public.plans SET ocr_pages_monthly = 20
--    WHERE plan_id IN ('marketing_lawyer', 'marketing_lawyer_14');
--   UPDATE public.plans SET ocr_pages_monthly = 40 WHERE plan_id = 'pro';

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF (SELECT count(*) FROM public.plans p
         WHERE (p.plan_id = 'basic' AND p.ocr_pages_monthly = 15)
            OR (p.plan_id IN ('marketing_lawyer', 'marketing_lawyer_14') AND p.ocr_pages_monthly = 20)
            OR (p.plan_id = 'pro' AND p.ocr_pages_monthly = 40)) <> 4 THEN
        RAISE EXCEPTION '206: a plan is not at the value this was written against';
    END IF;
END;
$guard$;

UPDATE public.plans SET ocr_pages_monthly = 25, updated_at = now() WHERE plan_id = 'basic';
UPDATE public.plans SET ocr_pages_monthly = 25, updated_at = now()
 WHERE plan_id IN ('marketing_lawyer', 'marketing_lawyer_14');
UPDATE public.plans SET ocr_pages_monthly = 80, updated_at = now() WHERE plan_id = 'pro';

DO $verify$
DECLARE
    v TEXT;
BEGIN
    SELECT string_agg(p.plan_id || '=' || p.ocr_pages_monthly, ' ' ORDER BY p.plan_id) INTO v
      FROM public.plans p
     WHERE p.plan_id IN ('basic', 'free', 'marketing_lawyer', 'marketing_lawyer_14', 'max', 'pro');
    IF v IS DISTINCT FROM
       'basic=25 free=3 marketing_lawyer=25 marketing_lawyer_14=25 max=500 pro=80' THEN
        RAISE EXCEPTION '206: unexpected limits: %', v;
    END IF;
    RAISE NOTICE '206: %', v;
END;
$verify$;

COMMIT;
