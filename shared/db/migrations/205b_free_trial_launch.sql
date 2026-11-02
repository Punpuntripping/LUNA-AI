-- =============================================================================
-- 205b: تشغيل الأسبوع المجاني وإيقاف عرض الأساسية (9.80)
-- =============================================================================
-- Apply AFTER 205 and AFTER the backend + frontend carrying it are live. This is
-- the launch: the trial goes on, the 9.80 Basic offer goes off, and the router's
-- pricing doc learns about the week, all in one transaction.
--
--   * new_accounts_from = now(): only accounts created from this instant are new.
--     To include earlier signups, UPDATE that one column.
--   * basic_t1_a off: no 9.80 seat was ever sold (basic_offer_seats is empty at
--     2026-11-02). Basic returns to its list price on /pricing within a minute.
--   * product_docs.pricing is guarded by md5, so an edit made since this was
--     written fails the migration instead of being overwritten.
--
-- Rollback:
--   UPDATE public.free_trial_campaign SET enabled = false;
--   UPDATE public.promo_batches SET enabled = true WHERE batch_id = 'basic_t1_a';

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $launch$
BEGIN
    IF (SELECT md5(d.content_md) FROM public.product_docs d WHERE d.doc_key = 'pricing')
       IS DISTINCT FROM '1b98aee381e678d2ad2170ed5b9ca36c' THEN
        RAISE EXCEPTION '205b: product_docs.pricing changed since this was written';
    END IF;
END;
$launch$;

UPDATE public.free_trial_campaign
   SET enabled = true,
       new_accounts_from = now(),
       updated_at = now();

UPDATE public.promo_batches
   SET enabled = false,
       updated_at = now()
 WHERE batch_id = 'basic_t1_a';

UPDATE public.product_docs
   SET content_md = replace(
           content_md,
           '**المجانية** — للتجربة: بحث معمّق واحد كل 30 يوماً، ضمن رصيد نقاط محدود للفترة نفسها.',
           '**المجانية** — للتجربة: بحث معمّق واحد كل 30 يوماً، ضمن رصيد نقاط محدود للفترة نفسها. وعند انتهاء هذا الرصيد يظهر للحساب الجديد عرض أسبوع مجاني مرة واحدة، بلا بطاقة ولا أي خصم، ثم يعود الحساب إلى الباقة المجانية.'),
       updated_at = now()
 WHERE doc_key = 'pricing';

DO $verify$
BEGIN
    IF NOT (SELECT c.enabled FROM public.free_trial_campaign c WHERE c.id) THEN
        RAISE EXCEPTION '205b: the trial is not on';
    END IF;
    IF (SELECT b.enabled FROM public.promo_batches b WHERE b.batch_id = 'basic_t1_a') THEN
        RAISE EXCEPTION '205b: the 9.80 batch is still on';
    END IF;
    IF (SELECT position('أسبوع مجاني' IN d.content_md) FROM public.product_docs d WHERE d.doc_key = 'pricing') = 0 THEN
        RAISE EXCEPTION '205b: the pricing doc was not updated';
    END IF;
    IF EXISTS (SELECT 1 FROM public.promo_public_quote() q WHERE q.plan_id = 'basic') THEN
        RAISE EXCEPTION '205b: Basic is still publicly discounted';
    END IF;
    RAISE NOTICE '205b: trial on, 9.80 off, pricing doc updated';
END;
$verify$;

COMMIT;
