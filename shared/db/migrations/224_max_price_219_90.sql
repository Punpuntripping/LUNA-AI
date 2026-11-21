-- ════════════════════════════════════════════════════════════════════════════
-- 224 — Max list price 289.90 → 219.90 (Phase A of the Pro Plus plan)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/pro_plus_tier_and_downgrade.md §0, §2 (Phase A), §8.
--       Owner decision 2026-11-20 (after weighing 239.90 / 229.90).
--
-- WHAT CHANGES: plans.price_sar for 'max', and nothing else.
--
-- NOT TOUCHED: plans.promo_price_sar (max 99.90). It is FROZEN (192 §5,
-- pricing_campaigns_v2.md §1.4-4): legacy early-adopter seats are priced from
-- it. The self-test below asserts it did not move.
--
-- WHO IS AFFECTED
--   * A new Max checkout with no offer pays 219.90 (promo_checkout_price falls
--     through to the list price).
--   * Running Max subscribers with no live seat renew at 219.90: the renewal
--     job prices with effective_plan_price(user, 'max', 'current'), which is the
--     list price for a seatless user. A price CUT needs no consent (plan D9).
--   * Held Max seats keep their frozen prices (batch 149.90, legacy 99.90),
--     all below the new list price.
--   * Every Max batch (149.90 / 189.90, and 225's 169.90) stays below 219.90,
--     so an offer is still an offer.
--
-- ORDER: a price CUT goes DB first, then the web (frontend/lib/pricing.ts).
-- For a few minutes the card says 289.90 and checkout charges 219.90, which is
-- harmless. The reverse order is not.
--
-- Idempotent: re-running at 219.90 is a no-op. The guard refuses any starting
-- price other than 289.90 / 219.90 (someone repriced Max by hand: look first).
--
-- ROLLBACK: UPDATE public.plans SET price_sar = 289.90, updated_at = now()
--           WHERE plan_id = 'max';
--   That is a price RAISE for running subscribers, so the web goes first and
--   subscribers must be told before their next renewal.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 0. guard + snapshot ─────────────────────────────────────────────────────
DO $guard$
DECLARE
    v_price NUMERIC;
BEGIN
    SELECT p.price_sar INTO v_price FROM public.plans p WHERE p.plan_id = 'max';
    IF NOT FOUND THEN
        RAISE EXCEPTION '224: plans row ''max'' is missing';
    END IF;
    IF v_price IS NULL OR v_price NOT IN (289.90, 219.90) THEN
        RAISE EXCEPTION '224: max list price is %, expected 289.90 (or 219.90 on a re-run). '
                        'Someone repriced Max by hand: check before applying.', v_price;
    END IF;
END
$guard$;

DROP TABLE IF EXISTS pg_temp._m224_plans;
CREATE TEMP TABLE _m224_plans ON COMMIT DROP AS
SELECT p.* FROM public.plans p;

-- ── 1. the change ───────────────────────────────────────────────────────────
UPDATE public.plans p
   SET price_sar  = 219.90,
       updated_at = now()
 WHERE p.plan_id = 'max'
   AND p.price_sar IS DISTINCT FROM 219.90;

-- ── 2. self-tests ───────────────────────────────────────────────────────────
DO $selftest$
DECLARE
    v_list   NUMERIC;
    v_promo  NUMERIC;
    v_promo0 NUMERIC;
    v_bad    TEXT;
    v_n      BIGINT;
    v_subs   BIGINT;
    v_p      NUMERIC;
BEGIN
    SELECT p.price_sar, p.promo_price_sar INTO v_list, v_promo
      FROM public.plans p WHERE p.plan_id = 'max';
    SELECT b.promo_price_sar INTO v_promo0 FROM _m224_plans b WHERE b.plan_id = 'max';

    IF v_list IS DISTINCT FROM 219.90 THEN
        RAISE EXCEPTION '224 self-test: max price_sar = %, expected 219.90', v_list;
    END IF;

    -- promo_price_sar is FROZEN for legacy seats: untouched, and still 99.90
    IF v_promo IS DISTINCT FROM v_promo0 OR v_promo IS DISTINCT FROM 99.90 THEN
        RAISE EXCEPTION '224 self-test: max promo_price_sar moved (% → %), it is FROZEN at 99.90',
            v_promo0, v_promo;
    END IF;

    -- no other plan row changed, and no other column of the max row
    SELECT string_agg(x.plan_id, ', ') INTO v_bad
      FROM (SELECT b.plan_id
              FROM _m224_plans b
              JOIN public.plans p ON p.plan_id = b.plan_id
             WHERE (to_jsonb(b) - 'price_sar' - 'updated_at')
                   IS DISTINCT FROM (to_jsonb(p) - 'price_sar' - 'updated_at')
                OR (b.plan_id <> 'max' AND (b.price_sar IS DISTINCT FROM p.price_sar
                                            OR b.updated_at IS DISTINCT FROM p.updated_at))
            UNION ALL
            SELECT b.plan_id FROM _m224_plans b
             WHERE NOT EXISTS (SELECT 1 FROM public.plans p WHERE p.plan_id = b.plan_id)
            UNION ALL
            SELECT p.plan_id FROM public.plans p
             WHERE NOT EXISTS (SELECT 1 FROM _m224_plans b WHERE b.plan_id = p.plan_id)) x;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '224 self-test: plans rows changed beyond max.price_sar: %', v_bad;
    END IF;

    -- every ENABLED Max batch is still below the list price (an offer stays an offer)
    SELECT string_agg(format('%s=%s', b.batch_id, b.price_sar), ', ' ORDER BY b.batch_id)
      INTO v_bad
      FROM public.promo_batches b
     WHERE b.plan_id = 'max' AND b.enabled AND b.price_sar >= v_list;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '224 self-test: enabled Max batch(es) not below the new list price %: %',
            v_list, v_bad;
    END IF;
    -- a disabled one could be switched back on later: say so, do not fail
    SELECT string_agg(format('%s=%s', b.batch_id, b.price_sar), ', ' ORDER BY b.batch_id)
      INTO v_bad
      FROM public.promo_batches b
     WHERE b.plan_id = 'max' AND NOT b.enabled AND b.price_sar >= v_list;
    IF v_bad IS NOT NULL THEN
        RAISE WARNING '224: DISABLED Max batch(es) priced at or above the new list price %: % — never re-enable them as they are',
            v_list, v_bad;
    END IF;

    -- every live Max seat prices below the new list (frozen seat prices)
    SELECT count(*) INTO v_n
      FROM public.early_adopter_seats s
     WHERE s.released_at IS NULL
       AND s.plan_id = 'max'
       AND s.price_sar >= v_list;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '224 self-test: % live Max batch seat(s) priced at or above the new list price', v_n;
    END IF;

    -- a seatless renewal is the list price (the renewal job's read)
    v_p := public.effective_plan_price(gen_random_uuid(), 'max', 'current');
    IF v_p IS DISTINCT FROM 219.90 THEN
        RAISE EXCEPTION '224 self-test: effective_plan_price(seatless, max, current) = %, expected 219.90', v_p;
    END IF;

    SELECT count(*) INTO v_subs
      FROM public.user_subscriptions s
     WHERE s.plan_id = 'max' AND s.expires_at > now();
    SELECT count(*) INTO v_n
      FROM public.promo_batches b
     WHERE b.plan_id = 'max' AND b.enabled;

    RAISE NOTICE '224: OK — max list 219.90 (promo_price_sar frozen at 99.90); % enabled Max batch(es) below it; % running Max term(s) renew at the new price unless a live seat prices them',
        v_n, v_subs;
END
$selftest$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY CHECKS — run manually
-- ════════════════════════════════════════════════════════════════════════════
-- SELECT plan_id, price_sar, promo_price_sar, updated_at FROM public.plans WHERE plan_id = 'max';
--   -- 219.90 | 99.90
-- SELECT public.effective_plan_price('<dev account on max, no seat>', 'max', 'current');  -- 219.90
-- Then deploy frontend/lib/pricing.ts (max price "219.90") and purge the ISR
-- cache for '/' and '/pricing'.
