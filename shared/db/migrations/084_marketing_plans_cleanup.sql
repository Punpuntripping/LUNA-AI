-- Migration 084: drop the "عرض الأفراد" promo and rename "عرض المحامين" → "ترويجي".
--
--   marketing_individual ("عرض الأفراد")  -> DELETED entirely. It had 0 active
--       subscriptions; its 5 activation codes were all unredeemed (uses_count=0),
--       so they are removed first (plan_codes.plan_id → plans.plan_id is
--       NO ACTION, so the codes must go before the plan row).
--   marketing_lawyer     ("عرض المحامين") -> name_ar becomes "ترويجي" (the single
--       generic promotional offer going forward). Limits/duration unchanged.
--
-- Forward-only, idempotent. 068 still seeds marketing_individual on a fresh DB;
-- this migration replays cleanly after it. Plan cache refreshes within ~5 min.
-- Dependencies: 068, 069 (plan_codes), 082.

-- 1. Remove the orphaned activation codes for the plan being deleted.
DELETE FROM public.plan_codes
WHERE plan_id = 'marketing_individual';

-- 2. Delete the plan row (no remaining FK references).
DELETE FROM public.plans
WHERE plan_id = 'marketing_individual';

-- 3. Rename the lawyer promo to the generic promotional label.
UPDATE public.plans SET
    name_ar    = 'ترويجي',
    updated_at = now()
WHERE plan_id = 'marketing_lawyer';
