-- =============================================================================
-- 205c: الأسبوع المجاني لكل الحسابات، لا الجديدة وحدها
-- =============================================================================
-- Owner correction, 2026-11-02: accounts created before the launch get the free
-- week too. 205b had dated "new" from the launch instant, so nobody qualified.
--
-- The date is moved back past the first account instead of dropping the column,
-- so narrowing the offer again is still one UPDATE. Every other rule in
-- free_trial_eligible stands: on free now, never paid or refunded, never
-- redeemed a code, week not yet had.
--
-- product_docs.pricing is guarded by md5, so an edit made since this was written
-- fails the migration instead of being overwritten.
--
-- Rollback:
--   UPDATE public.free_trial_campaign SET new_accounts_from = '2026-11-02 11:36:14+00';

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF (SELECT md5(d.content_md) FROM public.product_docs d WHERE d.doc_key = 'pricing')
       IS DISTINCT FROM 'bf1eb1fdd203afe54c014624a61a3931' THEN
        RAISE EXCEPTION '205c: product_docs.pricing changed since this was written';
    END IF;
END;
$guard$;

UPDATE public.free_trial_campaign
   SET new_accounts_from = '2000-01-01 00:00:00+00',
       updated_at = now();

UPDATE public.product_docs
   SET content_md = replace(
           content_md,
           'يظهر للحساب الجديد عرض أسبوع مجاني مرة واحدة',
           'يظهر عرض أسبوع مجاني مرة واحدة'),
       updated_at = now()
 WHERE doc_key = 'pricing';

DO $verify$
DECLARE
    v_eligible INTEGER;
    v_paid     UUID;
BEGIN
    SELECT count(*) INTO v_eligible
      FROM public.users u
     WHERE public.free_trial_eligible(u.user_id);
    IF v_eligible = 0 THEN
        RAISE EXCEPTION '205c: no account is eligible';
    END IF;

    SELECT t.user_id INTO v_paid
      FROM public.payment_transactions t
     WHERE t.status = 'paid' AND t.user_id IS NOT NULL
     LIMIT 1;
    IF public.free_trial_eligible(v_paid) THEN
        RAISE EXCEPTION '205c: a buyer must stay out';
    END IF;

    IF (SELECT position('للحساب الجديد' IN d.content_md)
          FROM public.product_docs d WHERE d.doc_key = 'pricing') <> 0 THEN
        RAISE EXCEPTION '205c: the pricing doc still says new accounts';
    END IF;

    RAISE NOTICE '205c: % accounts eligible', v_eligible;
END;
$verify$;

COMMIT;
