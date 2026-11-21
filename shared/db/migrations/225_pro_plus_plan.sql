-- ════════════════════════════════════════════════════════════════════════════
-- 225 — the Pro Plus plan («الاحترافية بلس»), its offer, the new Max offer, and
--       D12 ("an upgrade inside the first month does not use the offer")
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/pro_plus_tier_and_downgrade.md §0, §1 (D1–D12), §3.1,
--       §3.1b, §8 (build contract); pricing_campaigns_v2.md §1 (the rules this
--       extends). Owner decisions 2026-11-20.
-- Depends on: 192 (promo_batches, seats, promo_* functions, ladder_v2), 204
--             (basic offer), 209 (trial rule), 224 (max list 219.90 — the rank
--             order below needs pro < pro_plus 149.90 < max; checked in §0).
-- Function bodies are taken from pg_get_functiondef LIVE on 2026-11-20, never
-- from the repo files (they drifted). §0 refuses to run if any of them changed
-- in prod since (md5 of prosrc), so nothing is silently reverted.
--
-- ── SHIPS DARK ──────────────────────────────────────────────────────────────
--   * plans 'pro_plus' has price_sar NULL = not purchasable. Checkout refuses a
--     NULL price, promo_quote and promo_public_quote say nothing for it (§3, §5),
--     the quota-wall upgrade ladder skips it.
--   * promo_batches 'pro_plus_t1_a' (99.90 × 2) and 'max_t2_c' (169.90 × 2) are
--     inserted DISABLED. 'max_t1_a', 'max_t2_a', 'max_t2_b' are NOT touched.
--   * Every rewritten function answers exactly as before for every existing
--     plan: §11 re-prices a sample of real users (every payer, seat holder,
--     paid / trial plan, plus the 50 newest) for basic / pro / max before and
--     after the rewrite and refuses any difference. D12 changes nothing today
--     (no 'upgraded' seat exists on 2026-11-20).
--
-- ── WHAT CHANGES ────────────────────────────────────────────────────────────
--   promo_quote          + pro_plus; "higher / lower plan" is now ranked by
--                        plans.price_sar (pro 94.90 < pro_plus 149.90 < max
--                        219.90), so a future plan needs no rewrite; Pro Plus
--                        tier 1 = everyone (like Max); a plan with no list price
--                        gets no offer; NEW trailing p_as_new boolean DEFAULT
--                        false (contract §8-1). DROP + CREATE (a new argument),
--                        ACL re-applied exactly.
--   promo_checkout_price NEW trailing p_as_new boolean DEFAULT false, passed to
--                        promo_quote (contract §8-2). DROP + CREATE, same ACL.
--   promo_public_quote   + a pro_plus row (tier 1, else tier 2) once it has a
--                        list price. Max unchanged (tier 1, else tier 2), so the
--                        launch switch turns the public Max price into max_t2_c.
--   promo_tier_used      + D12 (contract §8-5). The O1 refund rule is unchanged.
--   intro_offer_quote    flag-OFF branch: a past pro_plus payer is a past
--                        subscriber too. Flag-ON branch unchanged.
--   redeem_plan_code     a running pro_plus term blocks a code like pro / max.
--   claim_early_adopter_seat — NOT here. It already releases ANY superseded live
--                        seat 'upgraded', so pro → pro_plus and pro_plus → max
--                        work as pro → max does (§10 proves it end to end). The
--                        'downgraded' release needs 226's CHECK, so 226 owns it.
--
-- ── WHERE THIS FILE HAD TO INTERPRET THE CONTRACT ──────────────────────────
-- (P1) p_as_new skips the "on this plan now" check, the "lower than the
--      current plan" check and the LEGACY-seat part of the live-seat block
--      (I2) — §8-1. It also leaves v_eff NULL, so the "no Pro tier 1 while Basic
--      runs" rule (which reads the current plan) does not apply either. Tier
--      history, the trial rule and the profession rule still apply — and Pro
--      tier 1 (the 19.90 profession offer) is NEVER quoted with p_as_new
--      (owner 2026-11-20, review #7 option b): a downgrade to Pro gets tier 2
--      if unused, else list.
--      NARROWER THAN §8-1's wording, on purpose: a live BATCH seat on the ASKED
--      plan still blocks the quote. A batch seat only ever blocks its own plan,
--      never the plan being left, so this never stops a downgrade being quoted;
--      it only keeps that seat's frozen price (effective_plan_price b1, which
--      also prices every later renewal) instead of quoting over it. This is
--      what the backend's release_seat_for_downgrade assumes (it keeps a batch
--      seat on the target plan live). Reachable only by someone who upgraded at
--      LIST price while holding a lower-plan seat.
-- (P2) promo_checkout_price with p_as_new and NO quote: the own-plan batch-seat
--      fallback (b1) still applies (P1); the LEGACY-seat fallback (b2 →
--      promo_price_sar) is skipped — the legacy seat is released at the switch,
--      so a fresh buyer does not hold it (D7: never better than cancel-and-
--      rebuy). Else list. With ladder_v2 OFF p_as_new is ignored
--      (effective_plan_price, as today).
-- (P3) p_as_new NULL is treated as false (never as "skip the guards").
-- (P4) A plan whose list price is NULL gets no promo_quote row and no public row
--      — defence in depth for the dark launch (the batch could be switched on
--      before the price). No existing plan is affected: basic / pro / max are
--      all priced.
-- (P5) Rank ties and NULLs: a NULL list price on EITHER side never makes a plan
--      "lower" (COALESCE … false). So a user on a priceless plan (free, dev,
--      marketing_*, or pro_plus while dark) is never blocked as a downgrade.
--
-- Idempotent: DROP … IF EXISTS / CREATE OR REPLACE / ON CONFLICT DO NOTHING;
-- §0 accepts a body already carrying this file's "(225)" marker.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ════════════════════════════════════════════════════════════════════════════
-- 0. Guards: live bodies unchanged since 2026-11-20, exact ACLs, rank order
-- ════════════════════════════════════════════════════════════════════════════
DO $guard$
DECLARE
    r       RECORD;
    v_oid   OID;
    v_src   TEXT;
    v_acl   TEXT;
    v_pro   NUMERIC;
    v_max   NUMERIC;
BEGIN
    FOR r IN
        SELECT *
          FROM (VALUES
                ('public.promo_quote(uuid,text)',              'public.promo_quote(uuid,text,boolean)',          '6f10fa306b5fba56335c7cbd45164a78'),
                ('public.promo_checkout_price(uuid,text)',     'public.promo_checkout_price(uuid,text,boolean)', '27585f0b7ab4c94496ae1c8285353dc7'),
                ('public.promo_public_quote()',                NULL,                                             '89fc694622290d61f8d5e5c48bff1457'),
                ('public.promo_tier_used(uuid,text,smallint)', NULL,                                             'bf1c3a2fa6ffe629c1bb2f541b9032ea'),
                ('public.intro_offer_quote(uuid)',             NULL,                                             'ca099f19d289ea912acbf8d1eaf9972d'),
                ('public.redeem_plan_code(text,uuid)',         NULL,                                             '30c927f9c1509fe0138d44bfed6d5154')
               ) v(old_sig, new_sig, old_md5)
    LOOP
        v_oid := to_regprocedure(r.old_sig);
        IF v_oid IS NULL THEN
            v_oid := to_regprocedure(r.new_sig);         -- re-run: already 3-arg
        ELSIF r.new_sig IS NOT NULL AND to_regprocedure(r.new_sig) IS NOT NULL THEN
            RAISE EXCEPTION '225: both % and % exist — a half-applied state; look before applying',
                r.old_sig, r.new_sig;
        END IF;
        IF v_oid IS NULL THEN
            RAISE EXCEPTION '225: % is missing (192 not applied?)', r.old_sig;
        END IF;

        SELECT p.prosrc, p.proacl::text INTO v_src, v_acl FROM pg_proc p WHERE p.oid = v_oid;
        IF md5(v_src) IS DISTINCT FROM r.old_md5 AND position('(225)' IN v_src) = 0 THEN
            RAISE EXCEPTION '225: % changed in prod since this file was written (md5 % <> %). '
                            'Rebuild its new body from pg_get_functiondef before applying.',
                v_oid::regprocedure, md5(v_src), r.old_md5;
        END IF;
        -- the GRANTs below reproduce exactly this ACL
        IF v_acl IS DISTINCT FROM '{postgres=X/postgres,service_role=X/postgres}' THEN
            RAISE EXCEPTION '225: % has ACL %, expected {postgres=X/postgres,service_role=X/postgres}',
                v_oid::regprocedure, v_acl;
        END IF;
    END LOOP;

    -- The rank order every "higher / lower plan" rule below relies on.
    SELECT p.price_sar INTO v_pro FROM public.plans p WHERE p.plan_id = 'pro';
    SELECT p.price_sar INTO v_max FROM public.plans p WHERE p.plan_id = 'max';
    IF NOT COALESCE(v_pro < 149.90 AND 149.90 < v_max, false) THEN
        RAISE EXCEPTION '225: list prices pro % / max % do not bracket pro_plus 149.90', v_pro, v_max;
    END IF;
    IF v_max IS DISTINCT FROM 219.90 THEN
        RAISE WARNING '225: max list price is % (224 sets 219.90) — apply 224 first unless this is deliberate', v_max;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.early_adopter_campaign c WHERE c.id) THEN
        RAISE EXCEPTION '225: early_adopter_campaign row missing';
    END IF;
END
$guard$;

-- ── 0b. snapshots for §11 (taken with the OLD functions) ───────────────────
-- Every user whose price could differ: payers, seat holders, paid / trial /
-- dev plans, trial claimers, Basic-offer seats — plus the 50 newest accounts.
DROP TABLE IF EXISTS pg_temp._m225_sample, pg_temp._m225_before, pg_temp._m225_public, pg_temp._m225_batches0;
CREATE TEMP TABLE _m225_sample ON COMMIT DROP AS
SELECT u.user_id
  FROM public.users u
 WHERE EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.user_id = u.user_id)
    OR EXISTS (SELECT 1 FROM public.payment_transactions t
                WHERE t.user_id = u.user_id AND t.status IN ('paid', 'refunded'))
    OR EXISTS (SELECT 1 FROM public.user_subscriptions s
                WHERE s.user_id = u.user_id AND s.plan_id IS DISTINCT FROM 'free')
    OR EXISTS (SELECT 1 FROM public.free_trial_claims f WHERE f.user_id = u.user_id)
    OR EXISTS (SELECT 1 FROM public.basic_offer_seats o WHERE o.user_id = u.user_id)
UNION
SELECT x.user_id
  FROM (SELECT u.user_id FROM public.users u ORDER BY u.created_at DESC LIMIT 50) x;

CREATE TEMP TABLE _m225_before ON COMMIT DROP AS
SELECT s.user_id,
       v.plan_id,
       (SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id,
                                 c.price_tier, c.months, c.intro_group), ';')
          FROM public.promo_checkout_price(s.user_id, v.plan_id) c)            AS checkout,
       format('%s|%s|%s|%s',
              public.promo_tier_used(s.user_id, 'pro', 1::smallint),
              public.promo_tier_used(s.user_id, 'pro', 2::smallint),
              public.promo_tier_used(s.user_id, 'max', 1::smallint),
              public.promo_tier_used(s.user_id, 'max', 2::smallint))            AS used
  FROM _m225_sample s
 CROSS JOIN (VALUES ('basic'), ('pro'), ('max')) v(plan_id);

CREATE TEMP TABLE _m225_public ON COMMIT DROP AS
SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id) AS q
  FROM public.promo_public_quote() q;

CREATE TEMP TABLE _m225_batches0 ON COMMIT DROP AS
SELECT b.* FROM public.promo_batches b;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. The plan row — DARK (price_sar NULL)
-- ════════════════════════════════════════════════════════════════════════════
-- §0 values. promo_price_sar NULL: the legacy column is never used for a new
-- plan. points_monthly NULL = no 30-day window (paid plans since 129).
INSERT INTO public.plans (
    plan_id, name_ar, name_en,
    price_sar, promo_price_sar, billing_cycle, duration_days,
    points_session, points_weekly, points_monthly,
    ocr_pages_monthly, web_calls_monthly, max_parallel_runs, library_unlocks_period,
    deep_search_min_points, deep_search_per_window
) VALUES (
    'pro_plus', 'الاحترافية بلس', 'Pro Plus',
    NULL, NULL, 'recurring_30d', 30,
    50, 250, NULL,
    500, 0, 1, 500,
    NULL, NULL
)
ON CONFLICT (plan_id) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- 2. The two new batches — DISABLED (the launch switch turns them on)
-- ════════════════════════════════════════════════════════════════════════════
-- D3: Pro Plus 99.90 × 2 months, tier 1 (everyone), 500 seats, once per user.
-- D10: the ONE Max offer, 169.90 × 2 months, a NEW tier-2 batch (the store
--      offer code = the batch id, and an Apple offer's duration is fixed), so
--      max_t2_a / max_t2_b are retired by the switch, not repriced.
-- A clash on (plan_id, price_tier, batch_order) raises: on purpose.
INSERT INTO public.promo_batches
    (batch_id, plan_id, price_tier, batch_order, price_sar, months, seat_limit,
     profession_only, enabled)
VALUES
    ('pro_plus_t1_a', 'pro_plus', 1, 1,  99.90, 2, 500, false, false),
    ('max_t2_c',      'max',      2, 3, 169.90, 2, 500, false, false)
ON CONFLICT (batch_id) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- 3. promo_tier_used — D12 (contract §8-5)
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.promo_tier_used(p_user_id uuid, p_plan_id text, p_tier smallint)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT EXISTS (
        SELECT 1
          FROM public.early_adopter_seats s
          LEFT JOIN public.payment_transactions t ON t.payment_id = s.payment_id
         WHERE s.user_id = p_user_id
           -- (O1) owner 2026-10-27: a REFUNDED PURCHASE was never bought — a
           -- seat released 'refund' whose own claiming payment is 'refunded'
           -- does not count. A seat released 'refund' because a LATER renewal
           -- was refunded (claiming payment still 'paid') still counts, so a
           -- refunded month-2 charge cannot re-open the tier. Batch and legacy
           -- seats alike. COALESCE: a live seat (release_reason NULL) or a seat
           -- with no payment row must stay counted, never NULL out of the WHERE.
           AND NOT COALESCE(s.release_reason = 'refund' AND t.status = 'refunded', false)
           -- (225) D12, owner 2026-11-20: an UPGRADE inside the offer's first
           -- charging period (30 days from the claim) did not use the offer —
           -- the customer moved up before the first month was over. An upgrade
           -- on day 30 or later, a cancel, a downgrade ('downgraded', 226) and
           -- a refunded later renewal all still count. Batch and legacy seats
           -- alike; COALESCE for the same reason as O1.
           AND NOT COALESCE(s.release_reason = 'upgraded'
                            AND s.released_at < s.claimed_at + interval '30 days', false)
           AND (
                 -- a batch seat answers from the terms it froze
                 (s.batch_id IS NOT NULL
                  AND s.plan_id = p_plan_id
                  AND s.price_tier = p_tier)
                 -- a legacy seat maps through its claiming payment (owner Q3):
                 -- legacy pro (49.90) ⇒ pro tier 2, legacy max (99.90) ⇒ max tier 1
              OR (s.batch_id IS NULL
                  AND ((t.plan_id = 'pro' AND p_plan_id = 'pro' AND p_tier = 2)
                    OR (t.plan_id = 'max' AND p_plan_id = 'max' AND p_tier = 1)
                    -- (R1) a 183 first-month purchase (1.99 / 9.90) was the
                    -- profession offer itself: BOTH pro tiers are used
                    OR (t.intro_offer_group IS NOT NULL
                        AND p_plan_id = 'pro' AND p_tier IN (1, 2))))
               )
    );
$function$;

REVOKE ALL ON FUNCTION public.promo_tier_used(uuid, text, smallint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promo_tier_used(uuid, text, smallint) TO service_role;

COMMENT ON FUNCTION public.promo_tier_used(uuid, text, smallint) IS
'TRUE iff this user ever held a seat — live OR released (cancelled, upgraded, downgraded, refund) — at tier p_tier of plan p_plan_id (192, §1.3-7). EXCEPT (O1) a refunded purchase: a seat released ''refund'' whose own claiming payment is ''refunded'' counts as never bought (a seat released ''refund'' because a later renewal was refunded still counts); and EXCEPT (225, D12) an early upgrade: a seat released ''upgraded'' less than 30 days after its claimed_at does not count (an upgrade on day 30 or later does; ''downgraded'' and ''cancelled'' always do). A batch seat answers from its own (plan_id, price_tier); a LEGACY seat maps through its claiming payment: legacy pro ⇒ (pro, 2), legacy max ⇒ (max, 1), and a legacy seat claimed by a 183 intro-offer first month (intro_offer_group NOT NULL) ⇒ (pro, 1) AND (pro, 2). A legacy seat with no payment maps to nothing. NULL user → false. Callers pass p_tier as smallint (1::smallint) — an int literal does not resolve. Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 4. promo_quote — pro_plus, rank by list price, p_as_new (contract §8-1)
-- ════════════════════════════════════════════════════════════════════════════
-- A new argument is a new signature: CREATE OR REPLACE would add an overload
-- (PostgREST PGRST203 on every named-arg call). Callers keep working: the
-- 2-arg calls in effective_plan_price, intro_offer_quote and the backend
-- (named p_user_id / p_plan_id) resolve through the DEFAULT. Those callers are
-- plpgsql, so nothing in pg_depend pins the old one (checked 2026-11-20).
DROP FUNCTION IF EXISTS public.promo_quote(uuid, text);

CREATE OR REPLACE FUNCTION public.promo_quote(p_user_id uuid, p_plan_id text, p_as_new boolean DEFAULT false)
 RETURNS TABLE(batch_id text, price_tier smallint, price_sar numeric, months integer)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_camp    RECORD;
    v_eff     TEXT;
    v_group   TEXT;
    v_prof_ok BOOLEAN;
    v_t1_ok   BOOLEAN;
    v_tier    SMALLINT;
    v_b       public.promo_batches%ROWTYPE;
    v_list    NUMERIC;                                   -- (225) the asked plan's rank
    v_as_new  BOOLEAN := COALESCE(p_as_new, false);      -- (225) NULL = false
BEGIN
    IF p_user_id IS NULL OR p_plan_id IS NULL
       OR p_plan_id NOT IN ('basic', 'pro', 'pro_plus', 'max') THEN
        RETURN;
    END IF;

    -- (225) The asked plan's LIST price. NULL = not on sale (pro_plus ships
    -- dark with price_sar NULL until the launch switch), so no offer either,
    -- even if its batch is switched on before its price. It is also the plan's
    -- RANK in the downgrade guard and the seat block below:
    -- pro 94.90 < pro_plus 149.90 < max 219.90.
    SELECT p.price_sar INTO v_list FROM public.plans p WHERE p.plan_id = p_plan_id;
    IF v_list IS NULL THEN
        RETURN;
    END IF;

    SELECT c.enabled AS is_on, c.ladder_v2 AS v2
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;
    IF NOT FOUND OR NOT v_camp.v2 OR NOT v_camp.is_on THEN
        RETURN;
    END IF;

    -- (204) Basic: once per user, whatever they bought before. Nothing below
    -- applies to it: the Pro/Max seat rules, the profession gate and the tier
    -- ladder all describe a recurring plan.
    IF p_plan_id = 'basic' THEN
        IF EXISTS (SELECT 1
                     FROM public.basic_offer_seats s
                    WHERE s.user_id = p_user_id) THEN
            RETURN;
        END IF;

        SELECT * INTO v_b FROM public.promo_active_batch('basic', 1::smallint);
        IF FOUND THEN
            RETURN QUERY
                SELECT v_b.batch_id, v_b.price_tier, round(v_b.price_sar, 2), v_b.months;
        END IF;
        RETURN;
    END IF;

    -- (225) p_as_new = quote a FRESH buyer on the day the current term ends
    -- (plan D7: the scheduled downgrade, priced at renewal). The plan the user
    -- is on now ends at that switch, so the guard below does not apply and
    -- v_eff stays NULL, which also drops the "no Pro tier 1 while Basic runs"
    -- rule; the legacy seats are skipped in I2 (see there). The tier history
    -- (promo_tier_used) and the trial rule still apply: they are about the
    -- person, not the term. Callers pass it only for the plan the user is
    -- switching TO.
    IF NOT v_as_new THEN
        -- The plan the user is ON right now. The view folds expiry → 'free'
        -- (and locked → NULL); never re-derive it here (183). It is
        -- security_invoker: inside this SECURITY DEFINER function it runs as
        -- the owner, exactly as 183's intro_offer_quote does. One read, by
        -- user_id, so the quota-state lateral runs for this user only.
        SELECT l.effective_plan_id
          INTO v_eff
          FROM public.user_subscriptions_live l
         WHERE l.user_id = p_user_id;

        -- §1.3-1: on the asked plan now → no new seat for it; renewals are
        -- priced by the seat they hold. (225) On a HIGHER plan now (ranked by
        -- list price) → the asked one is a downgrade, never quoted here:
        -- max → pro_plus / pro, pro_plus → pro. A current plan with no list
        -- price (free, dev, marketing_*, locked) outranks nothing.
        IF v_eff = p_plan_id
           OR COALESCE(v_list < (SELECT p.price_sar
                                   FROM public.plans p
                                  WHERE p.plan_id = v_eff), false) THEN
            RETURN;
        END IF;
    END IF;

    -- (I2) A live seat still inside its window keeps pricing what it prices
    -- (effective_plan_price branch b); a quote here would contradict the
    -- charge. A BATCH seat prices only its own plan. A LEGACY seat prices
    -- EVERY plan (as before 192), so it blocks every quote — except the
    -- owner-approved switch UP (A3; 225: D5, by rank): a legacy seat whose
    -- claiming payment was for a LOWER-priced plan than the one asked
    -- (pro → pro_plus, pro → max) takes the quote and is released 'upgraded'
    -- by the claim. The COALESCE keeps a legacy seat with no payment (no plan,
    -- no rank) blocking.
    -- (225) p_as_new: legacy seats never block — the scheduled downgrade
    -- releases the held seat at the switch, so the fresh buyer does not hold
    -- it. A BATCH seat on the asked plan still blocks: it never blocks the
    -- plan being left, and its frozen price is what this plan costs this user
    -- (effective_plan_price b1, which prices every later renewal too).
    IF EXISTS (
        SELECT 1
          FROM public.early_adopter_seats s
          LEFT JOIN public.payment_transactions t ON t.payment_id = s.payment_id
          LEFT JOIN public.plans tp ON tp.plan_id = t.plan_id
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
           AND s.promo_ends_at > now()
           AND CASE WHEN s.batch_id IS NOT NULL THEN s.plan_id = p_plan_id
                    ELSE NOT v_as_new AND NOT COALESCE(tp.price_sar < v_list, false)
               END
    ) THEN
        RETURN;
    END IF;

    -- The FIRST profession answer (183 §4b), never the current one.
    SELECT u.profession_first_group
      INTO v_group
      FROM public.users u
     WHERE u.user_id = p_user_id;
    v_prof_ok := COALESCE(v_group IN ('legal', 'specialist', 'entrepreneur'), false);

    IF p_plan_id = 'pro' THEN
        -- Pro tier 1: the three professions, the intro window (§1.4-9), and
        -- NOT while a Basic term runs (§1.3-2 / A10).
        -- (225) and NEVER for a downgrade (owner 2026-11-20, review #7 option
        -- b): p_as_new is the scheduled-downgrade quote, and the profession
        -- offer is for newcomers — a downgrade to Pro falls to tier 2, else list.
        v_t1_ok := NOT v_as_new
                   AND v_prof_ok
                   AND v_eff IS DISTINCT FROM 'basic'
                   AND EXISTS (SELECT 1
                                 FROM public.intro_offer_campaign ic
                                WHERE ic.id
                                  AND ic.enabled
                                  AND now() < ic.ends_at);
    ELSE
        v_t1_ok := true;                 -- Max and (225) Pro Plus tier 1: everyone
    END IF;

    -- Tier 1, else tier 2; each once per plan; batches fill in order. A tier
    -- whose batches are all full falls through to the next (A2).
    FOREACH v_tier IN ARRAY ARRAY[1, 2]::smallint[] LOOP
        CONTINUE WHEN v_tier = 1 AND NOT v_t1_ok;
        CONTINUE WHEN public.promo_tier_used(p_user_id, p_plan_id, v_tier);

        -- (209) Taking the free trial instead of Pro tier 1 uses up the first
        -- batch: from then on the quote starts at the next one.
        IF p_plan_id = 'pro' AND v_tier = 1
           AND EXISTS (SELECT 1
                         FROM public.free_trial_claims f
                        WHERE f.user_id = p_user_id) THEN
            SELECT b.* INTO v_b
              FROM public.promo_batches b
             WHERE b.plan_id = 'pro'
               AND b.price_tier = 1
               AND b.batch_order > 1
               AND b.enabled
               AND (SELECT count(*)
                      FROM public.early_adopter_seats s
                     WHERE s.released_at IS NULL
                       AND s.batch_id = b.batch_id) < b.seat_limit
             ORDER BY b.batch_order
             LIMIT 1;
        ELSE
            SELECT * INTO v_b FROM public.promo_active_batch(p_plan_id, v_tier);
        END IF;
        CONTINUE WHEN NOT FOUND;
        CONTINUE WHEN v_b.profession_only AND NOT v_prof_ok;          -- (I7)

        RETURN QUERY
            SELECT v_b.batch_id, v_b.price_tier, round(v_b.price_sar, 2), v_b.months;
        RETURN;
    END LOOP;

    RETURN;
END;
$function$;

REVOKE ALL ON FUNCTION public.promo_quote(uuid, text, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promo_quote(uuid, text, boolean) TO service_role;

COMMENT ON FUNCTION public.promo_quote(uuid, text, boolean) IS
'The personal tier quote (192 §1.3; 225): zero or one row (batch_id, price_tier, price_sar, months). EMPTY when ladder_v2 or the campaign is off, the plan is not basic/pro/pro_plus/max, or the plan has no list price (not on sale — pro_plus while dark). ALSO empty when the user holds a live BATCH seat still inside its window on that plan (I2), and — unless p_as_new — when the user is on that plan now (user_subscriptions_live), is on a plan with a HIGHER list price (a downgrade; rank = plans.price_sar: pro < pro_plus < max), or holds a live LEGACY seat in its window (it prices every plan, except when claimed by a payment for a LOWER-priced plan — the A3/D5 switch up). p_as_new = true quotes a fresh buyer on the day the current term ends (the scheduled downgrade, plan D7): it skips the on-plan / lower-plan checks (and so the running-Basic rule) and the legacy-seat block, but NOT a batch seat on the asked plan; NULL = false. Otherwise tier 1 (pro: profession_first_group in legal/specialist/entrepreneur, intro_offer_campaign enabled and before ends_at, no running basic, and never with p_as_new — a downgrade never gets the profession offer, owner 2026-11-20; max and pro_plus: anyone) if never used and a batch is open, else tier 2 if never used and a batch is open (promo_tier_used, incl. O1 and D12). A profession_only batch is skipped outside the three groups. Checkout charges price_sar and stamps payment_transactions.promo_batch_id = batch_id; effective_plan_price(''purchase'') returns the same price. ONE overload: 2-arg calls resolve through the DEFAULT. Callers must FAIL CLOSED (error ⇒ no quote). Never send batch_id or tier to a client. Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 5. promo_checkout_price — p_as_new passed through (contract §8-2)
-- ════════════════════════════════════════════════════════════════════════════
DROP FUNCTION IF EXISTS public.promo_checkout_price(uuid, text);

CREATE OR REPLACE FUNCTION public.promo_checkout_price(p_user_id uuid, p_plan_id text, p_as_new boolean DEFAULT false)
 RETURNS TABLE(ladder_v2 boolean, price_sar numeric, batch_id text, price_tier smallint, months integer, intro_group text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_plan   RECORD;
    v_q      RECORD;
    v_price  NUMERIC;
    v_group  TEXT;
    v_as_new BOOLEAN := COALESCE(p_as_new, false);       -- (225) NULL = false
BEGIN
    SELECT p.price_sar AS lst, p.promo_price_sar AS prm, p.billing_cycle AS cyc
      INTO v_plan
      FROM public.plans p
     WHERE p.plan_id = p_plan_id;
    IF NOT FOUND THEN
        RETURN;                                          -- unknown plan: no row
    END IF;

    -- ══ flag OFF: exactly today's purchase price, nothing to stamp ══
    -- (p_as_new is not read here: there is no quote to ask "as new".)
    IF NOT COALESCE((SELECT c.ladder_v2 FROM public.early_adopter_campaign c WHERE c.id), false) THEN
        RETURN QUERY
            SELECT false,
                   public.effective_plan_price(p_user_id, p_plan_id, 'purchase'),
                   NULL::text, NULL::smallint, NULL::integer, NULL::text;
        RETURN;
    END IF;

    -- ══ flag ON: the quote, once ══
    SELECT q.batch_id AS bid, q.price_tier AS tier, q.price_sar AS price, q.months AS mon
      INTO v_q
      FROM public.promo_quote(p_user_id, p_plan_id, v_as_new) q
     LIMIT 1;

    IF FOUND THEN
        -- Pro tier 1 is the profession offer: checkout stamps intro_offer_group
        -- (non-refundable first payment) from this.
        IF p_plan_id = 'pro' AND v_q.tier = 1 THEN
            SELECT u.profession_first_group INTO v_group
              FROM public.users u
             WHERE u.user_id = p_user_id;
        END IF;
        RETURN QUERY
            SELECT true, round(v_q.price, 2), v_q.bid, v_q.tier, v_q.mon, v_group;
        RETURN;
    END IF;

    -- ══ flag ON, no quote: inline fallback, mirrors effective_plan_price ══
    -- (b1) own-plan batch seat in its window → its frozen price (promo_quote is
    -- always empty in this case — I2 — so the order vs the quote is moot);
    -- (b2) a live legacy seat in its window → promo_price_sar; else list.
    -- (225) p_as_new: (b1) still applies — promo_quote keeps a batch seat on
    -- this plan blocking even as new, so this is where its price comes from;
    -- (b2) does not — the legacy seat is released at the switch, a fresh buyer
    -- does not hold it (D7: never better than cancel-and-rebuy).
    IF v_plan.lst IS NOT NULL AND v_plan.cyc = 'recurring_30d' THEN
        SELECT s.price_sar
          INTO v_price
          FROM public.early_adopter_seats s
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
           AND s.batch_id IS NOT NULL
           AND s.plan_id = p_plan_id
           AND s.promo_ends_at > now()
         ORDER BY s.claimed_at DESC
         LIMIT 1;

        IF v_price IS NULL
           AND NOT v_as_new
           AND v_plan.prm IS NOT NULL
           AND EXISTS (SELECT 1
                         FROM public.early_adopter_seats s
                        WHERE s.user_id = p_user_id
                          AND s.released_at IS NULL
                          AND s.batch_id IS NULL
                          AND s.promo_ends_at > now()) THEN
            v_price := v_plan.prm;
        END IF;
    END IF;

    RETURN QUERY
        SELECT true, round(COALESCE(v_price, v_plan.lst), 2),
               NULL::text, NULL::smallint, NULL::integer, NULL::text;
END;
$function$;

REVOKE ALL ON FUNCTION public.promo_checkout_price(uuid, text, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promo_checkout_price(uuid, text, boolean) TO service_role;

COMMENT ON FUNCTION public.promo_checkout_price(uuid, text, boolean) IS
'The checkout quote in ONE call and ONE snapshot (192; p_as_new 225): exactly one row for a known plan, none for an unknown one. ladder_v2 OFF ⇒ (false, effective_plan_price(user, plan, ''purchase''), NULL, NULL, NULL, NULL); p_as_new ignored. ladder_v2 ON ⇒ promo_quote(user, plan, p_as_new) once: a row ⇒ (true, its price_sar, batch_id, price_tier, months, intro_group = users.profession_first_group when plan = pro and tier = 1, else NULL); no row ⇒ (true, own-plan batch seat price, else legacy-seat promo_price_sar, else list, NULL, NULL, NULL, NULL) computed inline — with p_as_new = true the legacy-seat step is skipped (that seat ends at the switch); the own-plan batch seat still prices. p_as_new = true prices the scheduled downgrade at renewal (plan D7); NULL = false; 2-arg calls resolve through the DEFAULT. Checkout charges price_sar (minus credit) and stamps batch_id into payment_transactions.promo_batch_id, price/months into intro_offer_terms, intro_group into intro_offer_group. price_sar NULL ⇒ not purchasable. Service-role only; never forward batch_id or price_tier to a client.';


-- ════════════════════════════════════════════════════════════════════════════
-- 6. promo_public_quote — a pro_plus row once it is on sale
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.promo_public_quote()
 RETURNS TABLE(plan_id text, price_sar numeric, months integer)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_camp RECORD;
    v_b    public.promo_batches%ROWTYPE;
BEGIN
    SELECT c.enabled AS is_on, c.ladder_v2 AS v2, c.promo_days AS days
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF NOT v_camp.v2 THEN
        -- today: every plan with a promo price, while seats remain, for the
        -- campaign's window in months (90 / 30 = 3)
        IF NOT public.early_adopter_open() THEN
            RETURN;
        END IF;
        RETURN QUERY
            SELECT p.plan_id, round(p.promo_price_sar, 2), (v_camp.days / 30)::integer
              FROM public.plans p
             WHERE p.promo_price_sar IS NOT NULL
             ORDER BY p.price_sar NULLS LAST, p.plan_id;
        RETURN;
    END IF;

    IF NOT v_camp.is_on THEN                                          -- (I8)
        RETURN;
    END IF;

    -- pro: the tier-2 price (tier 1 is personal and never public, §4)
    SELECT * INTO v_b FROM public.promo_active_batch('pro', 2::smallint);
    IF FOUND THEN
        RETURN QUERY SELECT v_b.plan_id, round(v_b.price_sar, 2), v_b.months;
    END IF;

    -- (225) pro_plus: tier 1 is everyone's (like max), so it is public; tier 2
    -- if one is ever added. Not while the plan has no list price: it ships
    -- dark and must not be advertised before it is on sale.
    IF EXISTS (SELECT 1
                 FROM public.plans p
                WHERE p.plan_id = 'pro_plus'
                  AND p.price_sar IS NOT NULL) THEN
        SELECT * INTO v_b FROM public.promo_active_batch('pro_plus', 1::smallint);
        IF NOT FOUND THEN
            SELECT * INTO v_b FROM public.promo_active_batch('pro_plus', 2::smallint);
        END IF;
        IF FOUND THEN
            RETURN QUERY SELECT v_b.plan_id, round(v_b.price_sar, 2), v_b.months;
        END IF;
    END IF;

    -- max: tier 1 while it sells, then tier 2 (225: the launch switch turns
    -- max_t1_a off, so the public Max price becomes max_t2_c, 169.90 × 2)
    SELECT * INTO v_b FROM public.promo_active_batch('max', 1::smallint);
    IF NOT FOUND THEN
        SELECT * INTO v_b FROM public.promo_active_batch('max', 2::smallint);
    END IF;
    IF FOUND THEN
        RETURN QUERY SELECT v_b.plan_id, round(v_b.price_sar, 2), v_b.months;
    END IF;

    -- basic: its one-time offer (204)
    SELECT * INTO v_b FROM public.promo_active_batch('basic', 1::smallint);
    IF FOUND THEN
        RETURN QUERY SELECT v_b.plan_id, round(v_b.price_sar, 2), v_b.months;
    END IF;

    RETURN;
END;
$function$;

REVOKE ALL ON FUNCTION public.promo_public_quote() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promo_public_quote() TO service_role;

COMMENT ON FUNCTION public.promo_public_quote() IS
'The general public promo price per plan (192; pro_plus 225) for /pricing and GET /payments/early-adopter: rows (plan_id, price_sar, months), a closed plan drops out. ladder_v2 ON (and campaign enabled): pro = the active pro tier-2 batch; pro_plus = the active pro_plus tier-1 batch, else tier 2 — only while plans.pro_plus has a list price; max = the active max tier-1 batch, else tier 2; basic = its active tier-1 batch. ladder_v2 OFF: today — every plan with a non-NULL promo_price_sar while early_adopter_open(), months = promo_days / 30. Never a count, a batch or a tier. Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 7. intro_offer_quote — a past pro_plus payer is a past subscriber (flag OFF)
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.intro_offer_quote(p_user_id uuid)
 RETURNS TABLE(profession_group text, first_month_sar numeric)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
BEGIN
    -- ══ ladder_v2 ON: eligible iff the personal Pro quote is tier 1 ══
    -- (225) the 2-arg call resolves to promo_quote(uuid, text, boolean DEFAULT false)
    IF COALESCE((SELECT c.ladder_v2 FROM public.early_adopter_campaign c WHERE c.id), false) THEN
        RETURN QUERY
            SELECT u.profession_first_group, round(q.price_sar, 2)
              FROM public.users u
              CROSS JOIN LATERAL public.promo_quote(u.user_id, 'pro') q
             WHERE u.user_id = p_user_id
               AND q.price_tier = 1;
        RETURN;
    END IF;

    -- ══ ladder_v2 OFF: 183's body; (225) pro_plus counts as a paid plan ══
    RETURN QUERY
        SELECT u.profession_first_group,
               round(ip.first_month_sar, 2)
          FROM public.users u
          JOIN public.intro_offer_prices   ip ON ip.profession_group = u.profession_first_group
          JOIN public.intro_offer_campaign c  ON c.id
         WHERE u.user_id = p_user_id
           AND c.enabled
           AND now() < c.ends_at
           AND public.early_adopter_open()
           AND NOT EXISTS (
                SELECT 1
                  FROM public.user_subscriptions_live l
                 WHERE l.user_id = p_user_id
                   AND l.effective_plan_id IS NOT NULL
                   AND l.effective_plan_id <> 'free')
           AND NOT EXISTS (
                SELECT 1
                  FROM public.payment_transactions t
                 WHERE t.user_id = p_user_id
                   AND t.plan_id IN ('pro', 'pro_plus', 'max')
                   AND t.status IN ('paid', 'refunded'))
           AND NOT EXISTS (
                SELECT 1
                  FROM public.early_adopter_seats s
                 WHERE s.user_id = p_user_id
                   AND s.release_reason = 'cancelled');
END;
$function$;

REVOKE ALL ON FUNCTION public.intro_offer_quote(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.intro_offer_quote(uuid) TO service_role;

COMMENT ON FUNCTION public.intro_offer_quote(uuid) IS
'Pro tier-1 / profession offer eligibility (183; tiers 192; pro_plus 225): one row (profession_group, first_month_sar) or none. ladder_v2 ON: a row iff promo_quote(user, ''pro'') returns a tier-1 row; first_month_sar is that row''s price_sar (the whole offer price, for that row''s months), profession_group the write-once users.profession_first_group. ladder_v2 OFF: 183 — intro_offer_campaign open, early_adopter_open(), effective plan free/locked, never paid or refunded pro/pro_plus/max, no cancelled seat, price from intro_offer_prices. Checkout stamps intro_offer_group from it (first payment non-refundable). Callers must FAIL CLOSED. Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 8. redeem_plan_code — a running pro_plus term blocks a code
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code text, p_user_id uuid)
 RETURNS TABLE(plan_id text, name_ar text, expires_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_norm      TEXT := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    v_cur       TEXT;
    v_curexp    TIMESTAMPTZ;
    v_plan      TEXT;
    v_max       INTEGER;
    v_used      INTEGER;
    v_redeemers UUID[];
    v_codeexp   TIMESTAMPTZ;
    v_dur       INTEGER;
    v_newexp    TIMESTAMPTZ;
    v_caller    UUID;   -- migration 118
    v_self      UUID;   -- migration 118
BEGIN
    -- 0. Caller binding (migration 118 — security review 2026-08-07 H-3).
    --    auth.uid() IS NULL for service_role, which is the only caller allowed
    --    to name an arbitrary p_user_id. A session identity must match itself.
    v_caller := auth.uid();
    IF v_caller IS NOT NULL THEN
        SELECT u.user_id INTO v_self
          FROM public.users u
         WHERE u.auth_id = v_caller;

        IF v_self IS NULL OR v_self IS DISTINCT FROM p_user_id THEN
            RAISE EXCEPTION 'caller_not_owner';
        END IF;
    END IF;

    -- 1. Downgrade guard. (225) pro_plus is a paid plan like pro / max.
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'pro_plus', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';
    END IF;

    -- 2. Lock the code row and validate existence -> shelf-life -> dedup -> capacity.
    SELECT c.plan_id, c.max_uses, c.uses_count, c.redeemed_by_users, c.expires_at
      INTO v_plan, v_max, v_used, v_redeemers, v_codeexp
      FROM public.plan_codes c
     WHERE c.code = v_norm
       FOR UPDATE;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF v_codeexp IS NOT NULL AND v_codeexp <= now() THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF p_user_id = ANY(v_redeemers) THEN
        RAISE EXCEPTION 'code_already_redeemed';
    END IF;
    IF v_used >= v_max THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;

    -- 3. Consume one slot (atomic under the row lock held above).
    UPDATE public.plan_codes c
       SET uses_count        = c.uses_count + 1,
           redeemed_by_users = array_append(c.redeemed_by_users, p_user_id),
           redeemed_by       = p_user_id,
           redeemed_at       = now()
     WHERE c.code = v_norm;

    -- 4. Grant.
    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    INSERT INTO public.user_subscriptions
        (user_id, plan_id, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, v_plan, 'code', now(), v_newexp, v_norm)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
        source        = 'code',
        started_at    = now(),
        expires_at    = EXCLUDED.expires_at,
        redeemed_code = EXCLUDED.redeemed_code,
        updated_at    = now();

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_newexp;
END;
$function$;

REVOKE ALL ON FUNCTION public.redeem_plan_code(text, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.redeem_plan_code(text, uuid) TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- 9. Static checks — shapes, ACLs, the dark seed, untouched batches
-- ════════════════════════════════════════════════════════════════════════════
DO $static$
DECLARE
    r      RECORD;
    v_n    BIGINT;
    v_s    TEXT;
    v_pl   public.plans%ROWTYPE;
    v_max  NUMERIC;
BEGIN
    -- exactly one overload each; the contract's argument lists and result shapes
    FOR r IN
        SELECT *
          FROM (VALUES
                ('promo_quote',
                 'p_user_id uuid, p_plan_id text, p_as_new boolean DEFAULT false',
                 'TABLE(batch_id text, price_tier smallint, price_sar numeric, months integer)', 's'),
                ('promo_checkout_price',
                 'p_user_id uuid, p_plan_id text, p_as_new boolean DEFAULT false',
                 'TABLE(ladder_v2 boolean, price_sar numeric, batch_id text, price_tier smallint, months integer, intro_group text)', 's'),
                ('promo_public_quote', '', 'TABLE(plan_id text, price_sar numeric, months integer)', 's'),
                ('promo_tier_used', 'p_user_id uuid, p_plan_id text, p_tier smallint', 'boolean', 's'),
                ('intro_offer_quote', 'p_user_id uuid', 'TABLE(profession_group text, first_month_sar numeric)', 's'),
                ('redeem_plan_code', 'p_code text, p_user_id uuid',
                 'TABLE(plan_id text, name_ar text, expires_at timestamp with time zone)', 'v')
               ) v(name, args, result, vol)
    LOOP
        SELECT count(*) INTO v_n
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = r.name;
        IF v_n <> 1 THEN
            RAISE EXCEPTION '225 static: % has % overloads, expected 1', r.name, v_n;
        END IF;
        SELECT format('%s ⟂ %s ⟂ %s ⟂ %s ⟂ %s ⟂ %s',
                      pg_get_function_arguments(p.oid), pg_get_function_result(p.oid),
                      p.provolatile, p.prosecdef, p.proconfig::text, p.proacl::text)
          INTO v_s
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = r.name;
        IF v_s IS DISTINCT FROM format('%s ⟂ %s ⟂ %s ⟂ %s ⟂ %s ⟂ %s', r.args, r.result, r.vol, true,
                                       '{search_path=public}',
                                       '{postgres=X/postgres,service_role=X/postgres}') THEN
            RAISE EXCEPTION '225 static: % is "%"', r.name, v_s;
        END IF;
    END LOOP;

    -- effective_plan_price keeps its ONE signature (its callers' contract)
    SELECT count(*) INTO v_n
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
    IF v_n <> 1 THEN
        RAISE EXCEPTION '225 static: effective_plan_price has % overloads', v_n;
    END IF;

    -- the plan row (full check on the first run; a re-run leaves operator edits alone)
    SELECT * INTO v_pl FROM public.plans p WHERE p.plan_id = 'pro_plus';
    IF NOT FOUND THEN
        RAISE EXCEPTION '225 static: plans row pro_plus missing';
    END IF;
    IF v_pl.created_at = now() THEN
        IF v_pl.name_ar IS DISTINCT FROM 'الاحترافية بلس'
           OR v_pl.name_en IS DISTINCT FROM 'Pro Plus'
           OR v_pl.price_sar IS NOT NULL
           OR v_pl.promo_price_sar IS NOT NULL
           OR v_pl.billing_cycle IS DISTINCT FROM 'recurring_30d'
           OR v_pl.duration_days IS DISTINCT FROM 30
           OR v_pl.points_session IS DISTINCT FROM 50
           OR v_pl.points_weekly IS DISTINCT FROM 250
           OR v_pl.points_monthly IS NOT NULL
           OR v_pl.ocr_pages_monthly IS DISTINCT FROM 500
           OR v_pl.web_calls_monthly IS DISTINCT FROM 0
           OR v_pl.max_parallel_runs IS DISTINCT FROM 1
           OR v_pl.library_unlocks_period IS DISTINCT FROM 500
           OR v_pl.deep_search_min_points IS NOT NULL
           OR v_pl.deep_search_per_window IS NOT NULL THEN
            RAISE EXCEPTION '225 static: pro_plus seeded wrong: %', to_jsonb(v_pl);
        END IF;
    ELSE
        RAISE NOTICE '225: plans row pro_plus already existed (re-run) — left as is: price %', v_pl.price_sar;
    END IF;

    -- the two batches: seed terms on the first run; disabled while dark
    SELECT count(*) INTO v_n
      FROM public.promo_batches b
     WHERE b.created_at = now()
       AND (b.batch_id, b.plan_id, b.price_tier, b.batch_order, b.price_sar, b.months,
            b.seat_limit, b.profession_only, b.enabled)
           NOT IN (('pro_plus_t1_a', 'pro_plus', 1::smallint, 1,  99.90, 2, 500, false, false),
                   ('max_t2_c',      'max',      2::smallint, 3, 169.90, 2, 500, false, false));
    IF v_n <> 0 THEN
        RAISE EXCEPTION '225 static: % new batch row(s) seeded wrong', v_n;
    END IF;
    SELECT count(*) INTO v_n
      FROM public.promo_batches b WHERE b.batch_id IN ('pro_plus_t1_a', 'max_t2_c');
    IF v_n <> 2 THEN
        RAISE EXCEPTION '225 static: expected batches pro_plus_t1_a and max_t2_c, found %', v_n;
    END IF;
    IF (SELECT p.price_sar FROM public.plans p WHERE p.plan_id = 'pro_plus') IS NULL
       AND EXISTS (SELECT 1 FROM public.promo_batches b
                    WHERE b.plan_id = 'pro_plus' AND b.enabled) THEN
        RAISE WARNING '225: a pro_plus batch is enabled while pro_plus has no list price — harmless (no quote, no public row) but the launch order is price first';
    END IF;

    -- every batch that existed before is untouched (the switch flips them, not this file)
    SELECT string_agg(b0.batch_id, ', ') INTO v_s
      FROM _m225_batches0 b0
      LEFT JOIN public.promo_batches b ON b.batch_id = b0.batch_id
     WHERE to_jsonb(b) IS DISTINCT FROM to_jsonb(b0);
    IF v_s IS NOT NULL THEN
        RAISE EXCEPTION '225 static: existing batch(es) changed: %', v_s;
    END IF;

    -- every offer is below its plan's list price (pro_plus: below the 149.90 it launches at)
    SELECT p.price_sar INTO v_max FROM public.plans p WHERE p.plan_id = 'max';
    SELECT string_agg(format('%s=%s', b.batch_id, b.price_sar), ', ') INTO v_s
      FROM public.promo_batches b
      JOIN public.plans p ON p.plan_id = b.plan_id
     WHERE b.price_sar >= COALESCE(p.price_sar, CASE b.plan_id WHEN 'pro_plus' THEN 149.90 END)
       AND (b.enabled OR b.batch_id IN ('pro_plus_t1_a', 'max_t2_c'));
    IF v_s IS NOT NULL THEN
        RAISE EXCEPTION '225 static: offer(s) at or above their list price: %', v_s;
    END IF;

    RAISE NOTICE '225 static: OK — 6 functions (one overload each, contract signatures, ACL {postgres,service_role}), pro_plus dark (price NULL), pro_plus_t1_a + max_t2_c disabled, existing batches untouched, every offer below list (max list %)', v_max;
END
$static$;


-- ════════════════════════════════════════════════════════════════════════════
-- 10. Behavioural self-tests — the launch state, fake accounts, ALL ROLLED BACK
-- ════════════════════════════════════════════════════════════════════════════
-- Pattern of 192 §9: an inner BEGIN … EXCEPTION block ends by raising a private
-- SQLSTATE (LU225) and catching it, so every test write is rolled back — fake
-- auth.users / users / user_subscriptions rows, payments, seats, the price and
-- batch switches. A real failure raises P0001, is NOT caught, aborts the file.
-- Fake accounts go through auth.users so on_auth_user_created builds them as a
-- signup does (needs the postgres role; checked).
--
--   A   legal       ON pro, live pro_t2_a seat (day 10)   → pro_plus quoted; the switch claim
--   B   legal       ON max, no seat                       → no pro_plus quote; p_as_new → quote
--   C   individual  ON pro_plus, live pro_plus_t1_a (d10) → nothing for pro_plus / pro; max quoted; claim
--   D   legal       free, pro_plus_t1_a 'cancelled'       → used: list
--   E   legal       free, nothing                         → the positive control; dark checks
--   F   legal       ON pro, LEGACY pro seat               → pro_plus and max quoted (D5 / A3 by rank)
--   G   legal       free, LEGACY max seat in window       → no pro_plus quote (lower); p_as_new → quote
--   H   legal       free, live pro_t2_a seat              → pro_plus quoted (batch seat = own plan only)
--   U10 / U35 / U30  pro_plus_t1_a released 'upgraded' on day 10 / 35 / 30 (D12)
--   R1 / R2          pro_plus_t1_a released 'refund': claiming payment refunded / still paid (O1)
--   L10 / L35        LEGACY pro seat released 'upgraded' on day 10 / 35 (D12, legacy)
--   I   legal       ON max, live pro_t2_a seat            → as new → pro: the seat's 59.90, no quote (P1)
--   J   individual  ON max, LEGACY pro seat               → pro 49.90 (b2) normally; as new → list (P2)
DO $selftest$
DECLARE
    -- residue snapshot
    v_seats0   BIGINT;
    v_pays0    BIGINT;
    v_users0   BIGINT;
    v_batches0 TEXT;
    v_plans0   TEXT;
    v_camp0    TEXT;
    v_intro0   TEXT;
    -- accounts
    v_run   TEXT := replace(gen_random_uuid()::text, '-', '');
    v_prof  TEXT[] := ARRAY['legal', 'legal', 'individual', 'legal', 'legal', 'legal', 'legal',
                            'legal', 'legal', 'legal', 'legal', 'legal', 'legal', 'legal', 'legal',
                            'legal', 'individual'];
    v_ids   UUID[] := ARRAY[]::uuid[];
    v_auth  UUID;
    v_id    UUID;
    v_i     INTEGER;
    v_a UUID; v_b UUID; v_c UUID; v_d UUID; v_e UUID; v_f UUID; v_g UUID; v_h UUID;
    v_u10 UUID; v_u35 UUID; v_u30 UUID; v_r1 UUID; v_r2 UUID; v_l10 UUID; v_l35 UUID;
    v_ii UUID; v_j UUID;
    -- state
    v_pay   UUID;
    v_aseat UUID;
    v_cseat UUID;
    v_new   UUID;
    v_act   TEXT;
    v_r     RECORD;
    v_s     TEXT;
    v_x     TEXT;
    v_p     NUMERIC;
    v_n     BIGINT;
BEGIN
    SELECT count(*) INTO v_seats0 FROM public.early_adopter_seats;
    SELECT count(*) INTO v_pays0  FROM public.payment_transactions;
    SELECT count(*) INTO v_users0 FROM public.users;
    SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), '')) INTO v_batches0 FROM public.promo_batches b;
    SELECT md5(COALESCE(string_agg(p::text, '|' ORDER BY p.plan_id), ''))  INTO v_plans0   FROM public.plans p;
    SELECT md5(c::text) INTO v_camp0  FROM public.early_adopter_campaign c WHERE c.id;
    SELECT md5(c::text) INTO v_intro0 FROM public.intro_offer_campaign c WHERE c.id;

    IF NOT has_table_privilege('auth.users', 'INSERT') THEN
        RAISE EXCEPTION '225 self-test: this role cannot INSERT into auth.users. Run the migration as postgres (SQL editor).';
    END IF;

    BEGIN
        -- ── setup ──
        UPDATE public.early_adopter_campaign c SET enabled = true, ladder_v2 = true WHERE c.id;

        FOR v_i IN 1 .. array_length(v_prof, 1) LOOP
            v_auth := gen_random_uuid();
            INSERT INTO auth.users (id, instance_id, aud, role, email,
                                    raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
            VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
                    format('selftest-225-%s-%s@selftest.invalid', v_run, v_i),
                    '{}'::jsonb, '{}'::jsonb, now(), now());
            SELECT u.user_id INTO v_id FROM public.users u WHERE u.auth_id = v_auth;
            IF v_id IS NULL THEN
                RAISE EXCEPTION '225 self-test: on_auth_user_created built no public.users row';
            END IF;
            UPDATE public.users u SET profession_group = v_prof[v_i] WHERE u.user_id = v_id;
            v_ids := v_ids || v_id;
        END LOOP;
        v_a := v_ids[1];  v_b := v_ids[2];  v_c := v_ids[3];  v_d := v_ids[4];  v_e := v_ids[5];
        v_f := v_ids[6];  v_g := v_ids[7];  v_h := v_ids[8];  v_u10 := v_ids[9]; v_u35 := v_ids[10];
        v_u30 := v_ids[11]; v_r1 := v_ids[12]; v_r2 := v_ids[13]; v_l10 := v_ids[14]; v_l35 := v_ids[15];
        v_ii := v_ids[16]; v_j := v_ids[17];

        -- subscriptions (the assignment trigger stamps expires_at from the plan)
        UPDATE public.user_subscriptions s SET plan_id = 'pro'      WHERE s.user_id IN (v_a, v_f);
        UPDATE public.user_subscriptions s SET plan_id = 'max'      WHERE s.user_id IN (v_b, v_ii, v_j);
        UPDATE public.user_subscriptions s SET plan_id = 'pro_plus' WHERE s.user_id = v_c;
        -- one read per user, by user_id (never a join: the view's quota lateral
        -- must not run for every account in prod)
        v_s := '';
        FOREACH v_id IN ARRAY ARRAY[v_a, v_b, v_c, v_e] LOOP
            SELECT l.effective_plan_id INTO v_x FROM public.user_subscriptions_live l WHERE l.user_id = v_id;
            v_s := v_s || COALESCE(v_x, '∅') || ',';
        END LOOP;
        IF v_s IS DISTINCT FROM 'pro,max,pro_plus,free,' THEN
            RAISE EXCEPTION '225 self-test setup: effective plans A,B,C,E = %', v_s;
        END IF;

        -- A: live pro_t2_a seat, claimed 10 days ago
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_a, 'pro', 59.90, 'paid', now() - interval '10 days', 'pro_t2_a')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_a, v_pay, now() - interval '10 days', now() + interval '50 days', 'pro_t2_a', 'pro', 2, 59.90)
        RETURNING seat_id INTO v_aseat;

        -- C: live pro_plus_t1_a seat, claimed 10 days ago
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_c, 'pro_plus', 99.90, 'paid', now() - interval '10 days', 'pro_plus_t1_a')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_c, v_pay, now() - interval '10 days', now() + interval '50 days', 'pro_plus_t1_a', 'pro_plus', 1, 99.90)
        RETURNING seat_id INTO v_cseat;

        -- D: used pro_plus_t1_a, released 'cancelled'
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, released_at, release_reason, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_d, now() - interval '20 days', now() + interval '40 days', now() - interval '1 day',
                'cancelled', 'pro_plus_t1_a', 'pro_plus', 1, 99.90);

        -- F: LEGACY pro seat (49.90) in its window; G: LEGACY max seat (99.90) in its window
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_f, 'pro', 49.90, 'paid', now() - interval '10 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_f, v_pay, now() - interval '10 days', now() + interval '80 days');
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_g, 'max', 99.90, 'paid', now() - interval '20 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_g, v_pay, now() - interval '20 days', now() + interval '70 days');

        -- H: lapsed, live pro_t2_a seat in its window; I: the same seat while ON max
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_h,  now() - interval '10 days', now() + interval '50 days', 'pro_t2_a', 'pro', 2, 59.90),
               (v_ii, now() - interval '10 days', now() + interval '50 days', 'pro_t2_a', 'pro', 2, 59.90);

        -- J: ON max, LEGACY pro seat (49.90) in its window
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_j, 'pro', 49.90, 'paid', now() - interval '10 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_j, v_pay, now() - interval '10 days', now() + interval '80 days');

        -- U10 / U35 / U30: pro_plus_t1_a released 'upgraded' on day 10 / 35 / exactly 30
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, released_at, release_reason, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_u10, now() - interval '40 days', now() + interval '20 days', now() - interval '30 days',
                'upgraded', 'pro_plus_t1_a', 'pro_plus', 1, 99.90),
               (v_u35, now() - interval '40 days', now() + interval '20 days', now() - interval '5 days',
                'upgraded', 'pro_plus_t1_a', 'pro_plus', 1, 99.90),
               (v_u30, now() - interval '40 days', now() + interval '20 days', now() - interval '10 days',
                'upgraded', 'pro_plus_t1_a', 'pro_plus', 1, 99.90);

        -- R1 / R2: released 'refund'; R1's claiming payment refunded, R2's still paid
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_r1, 'pro_plus', 99.90, 'refunded', now() - interval '5 days', 'pro_plus_t1_a')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_r1, v_pay, now() - interval '5 days', now() + interval '55 days', now() - interval '4 days',
                'refund', 'pro_plus_t1_a', 'pro_plus', 1, 99.90);
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_r2, 'pro_plus', 99.90, 'paid', now() - interval '35 days', 'pro_plus_t1_a')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_r2, v_pay, now() - interval '35 days', now() + interval '25 days', now() - interval '4 days',
                'refund', 'pro_plus_t1_a', 'pro_plus', 1, 99.90);

        -- L10 / L35: LEGACY pro seat (49.90 payment) released 'upgraded' on day 10 / 35
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_l10, 'pro', 49.90, 'paid', now() - interval '40 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason)
        VALUES (v_l10, v_pay, now() - interval '40 days', now() + interval '50 days', now() - interval '30 days', 'upgraded');
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_l35, 'pro', 49.90, 'paid', now() - interval '40 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason)
        VALUES (v_l35, v_pay, now() - interval '40 days', now() + interval '50 days', now() - interval '5 days', 'upgraded');

        -- ── T0. DARK: price NULL → no quote, no public row, not purchasable ──
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_e, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(v_e, 'pro_plus') c;
        IF v_s IS NOT NULL OR v_x IS DISTINCT FROM 't|||||' THEN
            RAISE EXCEPTION '225 self-test [T0 dark]: quote %, checkout % (expected none, t|||||)', v_s, v_x;
        END IF;
        -- the batch switched on BEFORE the price: still nothing (P4)
        UPDATE public.promo_batches b SET enabled = true WHERE b.batch_id = 'pro_plus_t1_a';
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_e, 'pro_plus') q;
        SELECT count(*) INTO v_n FROM public.promo_public_quote() q WHERE q.plan_id = 'pro_plus';
        IF v_s IS NOT NULL OR v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T0 batch on, no price]: quote %, public rows % (expected none)', v_s, v_n;
        END IF;
        RAISE NOTICE '225 [T0 dark]: OK — no list price ⇒ no quote, no public row, checkout price NULL (even with the batch on)';

        -- ── the LAUNCH SWITCH (plan §3.5), as it will be run ──
        UPDATE public.plans p SET price_sar = 149.90 WHERE p.plan_id = 'pro_plus';
        UPDATE public.promo_batches b
           SET enabled    = (b.batch_id IN ('pro_plus_t1_a', 'max_t2_c')),
               seat_limit = CASE WHEN b.batch_id IN ('pro_plus_t1_a', 'max_t2_c')
                                 THEN (SELECT count(*)::integer FROM public.early_adopter_seats x
                                        WHERE x.released_at IS NULL AND x.batch_id = b.batch_id) + 1000
                                 ELSE b.seat_limit END
         WHERE b.plan_id IN ('pro_plus', 'max');

        -- ── T1. public prices after the switch ──
        SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id)
          INTO v_s FROM public.promo_public_quote() q WHERE q.plan_id IN ('pro_plus', 'max');
        IF v_s IS DISTINCT FROM 'max|169.90|2,pro_plus|99.90|2' THEN
            RAISE EXCEPTION '225 self-test [T1 public]: % (expected max|169.90|2,pro_plus|99.90|2)', v_s;
        END IF;
        RAISE NOTICE '225 [T1 public]: OK — %', v_s;

        -- ── T2. quotes ──
        -- A on pro → pro_plus: the offer (D5), and checkout charges it
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_a, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(v_a, 'pro_plus') c;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' OR v_x IS DISTINCT FROM 't|99.90|pro_plus_t1_a|1|2|' THEN
            RAISE EXCEPTION '225 self-test [T2 pro → pro_plus]: quote %, checkout % (expected pro_plus_t1_a|1|99.90|2)', v_s, v_x;
        END IF;
        -- A asks its own plan: nothing (unchanged)
        SELECT count(*) INTO v_n FROM public.promo_quote(v_a, 'pro') q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T2 on pro, asks pro]: % row(s)', v_n;
        END IF;

        -- B on max: pro_plus and pro are downgrades → nothing; checkout = list
        SELECT count(*) INTO v_n FROM public.promo_quote(v_b, 'pro_plus') q;
        SELECT count(*) + v_n INTO v_n FROM public.promo_quote(v_b, 'pro') q;
        SELECT string_agg(format('%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id), ';')
          INTO v_x FROM public.promo_checkout_price(v_b, 'pro_plus') c;
        IF v_n <> 0 OR v_x IS DISTINCT FROM 't|149.90|' THEN
            RAISE EXCEPTION '225 self-test [T2 max → lower]: % quote row(s), checkout % (expected 0, t|149.90|)', v_n, v_x;
        END IF;
        -- … but quoted AS NEW (the scheduled downgrade, D7), through both functions
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_b, 'pro_plus', true) q;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(v_b, 'pro_plus', true) c;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' OR v_x IS DISTINCT FROM 't|99.90|pro_plus_t1_a|1|2|' THEN
            RAISE EXCEPTION '225 self-test [T2 max, p_as_new → pro_plus]: quote %, checkout %', v_s, v_x;
        END IF;
        -- p_as_new NULL is false (P3)
        SELECT count(*) INTO v_n FROM public.promo_quote(v_b, 'pro_plus', NULL) q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T2 p_as_new NULL]: % row(s), expected none', v_n;
        END IF;

        -- C on pro_plus: its own plan and the lower one → nothing; max → the new Max offer
        SELECT count(*) INTO v_n FROM public.promo_quote(v_c, 'pro_plus') q;
        SELECT count(*) + v_n INTO v_n FROM public.promo_quote(v_c, 'pro') q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T2 on pro_plus → pro_plus / pro]: % row(s), expected none', v_n;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_c, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t2_c|2|169.90|2' THEN
            RAISE EXCEPTION '225 self-test [T2 pro_plus → max]: % (expected max_t2_c|2|169.90|2)', v_s;
        END IF;
        -- C (individual) as new → pro: exactly what a fresh individual is quoted (tier 2)
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_c, 'pro', true) q;
        SELECT string_agg(format('%s|%s|%s|%s', b.batch_id, b.price_tier, round(b.price_sar, 2), b.months), ',')
          INTO v_x FROM public.promo_active_batch('pro', 2::smallint) b WHERE NOT b.profession_only;
        IF v_s IS DISTINCT FROM v_x THEN
            RAISE EXCEPTION '225 self-test [T2 pro_plus, p_as_new → pro]: % (expected %)', v_s, v_x;
        END IF;

        -- D used pro_plus tier 1 → no quote, list price
        SELECT count(*) INTO v_n FROM public.promo_quote(v_d, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id), ';')
          INTO v_x FROM public.promo_checkout_price(v_d, 'pro_plus') c;
        IF v_n <> 0 OR v_x IS DISTINCT FROM 't|149.90|' THEN
            RAISE EXCEPTION '225 self-test [T2 used pro_plus_t1_a]: % row(s), checkout %', v_n, v_x;
        END IF;

        -- E fresh → the offer; the 2-arg callers inside SQL still resolve
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_e, 'pro_plus') q;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' THEN
            RAISE EXCEPTION '225 self-test [T2 fresh → pro_plus]: %', v_s;
        END IF;
        IF public.effective_plan_price(v_e, 'pro_plus', 'purchase') IS DISTINCT FROM 99.90
           OR public.effective_plan_price(v_e, 'pro_plus', 'current') IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '225 self-test [T2 effective_plan_price pro_plus]: purchase %, current % (expected 99.90 / 149.90)',
                public.effective_plan_price(v_e, 'pro_plus', 'purchase'),
                public.effective_plan_price(v_e, 'pro_plus', 'current');
        END IF;

        -- F on pro with a LEGACY pro seat: pro_plus and max both quoted (switch up, by rank)
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_f, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_x FROM public.promo_quote(v_f, 'max') q;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' OR v_x IS DISTINCT FROM 'max_t2_c|2|169.90|2' THEN
            RAISE EXCEPTION '225 self-test [T2 legacy pro seat → pro_plus / max]: % / %', v_s, v_x;
        END IF;

        -- G lapsed with a LEGACY max seat in its window: pro_plus is lower → blocked; as new → quoted
        SELECT count(*) INTO v_n FROM public.promo_quote(v_g, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_g, 'pro_plus', true) q;
        IF v_n <> 0 OR v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' THEN
            RAISE EXCEPTION '225 self-test [T2 legacy max seat → pro_plus]: % row(s); as new %', v_n, v_s;
        END IF;

        -- H lapsed with a live pro_t2_a seat: blocks pro only (I2), not pro_plus
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_h, 'pro_plus') q;
        SELECT count(*) INTO v_n FROM public.promo_quote(v_h, 'pro') q;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' OR v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T2 pro batch seat]: pro_plus %, pro rows %', v_s, v_n;
        END IF;

        -- I on max with a live pro_t2_a seat, as new → pro: no quote over the seat;
        -- checkout charges the seat's frozen price (P1, b1)
        SELECT count(*) INTO v_n FROM public.promo_quote(v_ii, 'pro', true) q;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(v_ii, 'pro', true) c;
        IF v_n <> 0 OR v_x IS DISTINCT FROM 't|59.90||||' THEN
            RAISE EXCEPTION '225 self-test [T2 batch seat on the target, as new]: % row(s), checkout % (expected 0, t|59.90||||)', v_n, v_x;
        END IF;
        -- J on max with a LEGACY pro seat → pro: 49.90 from the seat normally (b2);
        -- as new the legacy seat is ignored → no tier left (individual, legacy = tier 2) → list (P2)
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_s FROM public.promo_checkout_price(v_j, 'pro') c;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(v_j, 'pro', true) c;
        IF v_s IS DISTINCT FROM 't|49.90||||' OR v_x IS DISTINCT FROM 't|94.90||||' THEN
            RAISE EXCEPTION '225 self-test [T2 legacy seat, as new]: normal %, as new % (expected t|49.90||||, t|94.90||||)', v_s, v_x;
        END IF;

        -- B (lawyer, ON max, no Pro history): a DOWNGRADE to Pro never gets the
        -- profession offer (owner 2026-11-20, review #7 b) → tier 2; the same
        -- lawyer buying Pro normally once Max lapsed → still tier 1. The Pro
        -- batches and the intro window are pinned open so the case is decided
        -- by the rule, not by today's batch state (rolled back).
        UPDATE public.intro_offer_campaign c SET enabled = true, ends_at = now() + interval '1 day' WHERE c.id;
        UPDATE public.promo_batches b
           SET enabled = true,
               seat_limit = (SELECT count(*)::integer FROM public.early_adopter_seats x
                              WHERE x.released_at IS NULL AND x.batch_id = b.batch_id) + 1000
         WHERE b.batch_id IN ('pro_t1_a', 'pro_t2_a');
        SELECT format('pro_t1_a|1|%s|%s', round(b.price_sar, 2), b.months) INTO v_s
          FROM public.promo_batches b WHERE b.batch_id = 'pro_t1_a';
        SELECT format('pro_t2_a|2|%s|%s', round(b.price_sar, 2), b.months) INTO v_x
          FROM public.promo_batches b WHERE b.batch_id = 'pro_t2_a';
        -- as new (the scheduled downgrade): tier 2, and checkout stamps no profession group
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_act FROM public.promo_quote(v_b, 'pro', true) q;
        IF v_act IS DISTINCT FROM v_x THEN
            RAISE EXCEPTION '225 self-test [T2 lawyer on max, downgrade → pro]: % (expected tier 2 %, never tier 1 %)', v_act, v_x, v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s', c.batch_id, c.price_tier, COALESCE(c.intro_group, '∅')), ';')
          INTO v_act FROM public.promo_checkout_price(v_b, 'pro', true) c;
        IF v_act IS DISTINCT FROM 'pro_t2_a|2|∅' THEN
            RAISE EXCEPTION '225 self-test [T2 downgrade checkout → pro]: % (expected pro_t2_a|2|∅)', v_act;
        END IF;
        -- Max lapses → the ordinary quote (not as new) is still the profession offer
        UPDATE public.user_subscriptions s SET expires_at = now() - interval '1 day' WHERE s.user_id = v_b;
        SELECT l.effective_plan_id INTO v_act FROM public.user_subscriptions_live l WHERE l.user_id = v_b;
        IF v_act IS DISTINCT FROM 'free' THEN
            RAISE EXCEPTION '225 self-test [T2 setup]: B should have lapsed to free, is %', v_act;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_act FROM public.promo_quote(v_b, 'pro') q;
        IF v_act IS DISTINCT FROM v_s THEN
            RAISE EXCEPTION '225 self-test [T2 lapsed lawyer → pro]: % (expected tier 1 %)', v_act, v_s;
        END IF;
        RAISE NOTICE '225 [T2 downgrade ≠ profession offer]: OK — lawyer on max, as new → pro: % (no intro group); the same lawyer after max lapsed → pro: %', v_x, v_s;

        -- a disabled batch → no quote, list price
        UPDATE public.promo_batches b SET enabled = false WHERE b.batch_id = 'pro_plus_t1_a';
        SELECT count(*) INTO v_n FROM public.promo_quote(v_e, 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id), ';')
          INTO v_x FROM public.promo_checkout_price(v_e, 'pro_plus') c;
        IF v_n <> 0 OR v_x IS DISTINCT FROM 't|149.90|' THEN
            RAISE EXCEPTION '225 self-test [T2 batch disabled]: % row(s), checkout %', v_n, v_x;
        END IF;
        UPDATE public.promo_batches b SET enabled = true WHERE b.batch_id = 'pro_plus_t1_a';
        RAISE NOTICE '225 [T2 quotes]: OK — pro→pro_plus 99.90×2; max→pro_plus none (as new: 99.90×2); pro_plus→pro none, →max 169.90×2; used/disabled → list 149.90; legacy pro seat → pro_plus & max; legacy max seat blocks pro_plus; pro batch seat blocks pro only; as new: a batch seat on the target keeps its price (59.90), a legacy seat is ignored (list 94.90, not 49.90)';

        -- ── T3. 2-arg callers (contract §8-1/2: existing calls keep working) ──
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(p_user_id => v_e, p_plan_id => 'pro_plus') q;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_x FROM public.promo_checkout_price(p_user_id => v_e, p_plan_id => 'pro_plus') c;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' OR v_x IS DISTINCT FROM 't|99.90|pro_plus_t1_a|1|2|' THEN
            RAISE EXCEPTION '225 self-test [T3 named 2-arg calls]: quote %, checkout %', v_s, v_x;
        END IF;
        IF EXISTS (SELECT 1 FROM public.intro_offer_quote(v_e))
           IS DISTINCT FROM EXISTS (SELECT 1 FROM public.promo_quote(v_e, 'pro') q WHERE q.price_tier = 1) THEN
            RAISE EXCEPTION '225 self-test [T3 intro_offer_quote]: disagrees with promo_quote(user, pro) tier 1';
        END IF;
        RAISE NOTICE '225 [T3 2-arg calls]: OK — promo_quote / promo_checkout_price (named p_user_id, p_plan_id), intro_offer_quote and effective_plan_price resolve the 3-arg functions';

        -- ── T4. D12 + O1 (promo_tier_used, and through promo_quote) ──
        IF public.promo_tier_used(v_u10, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 upgraded day 10]: counted as used';
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_u10, 'pro_plus') q;
        IF v_s IS DISTINCT FROM 'pro_plus_t1_a|1|99.90|2' THEN
            RAISE EXCEPTION '225 self-test [T4 upgraded day 10 → quoted again]: %', v_s;
        END IF;
        IF NOT public.promo_tier_used(v_u35, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 upgraded day 35]: not counted as used';
        END IF;
        SELECT count(*) INTO v_n FROM public.promo_quote(v_u35, 'pro_plus') q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '225 self-test [T4 upgraded day 35 → no quote]: % row(s)', v_n;
        END IF;
        IF NOT public.promo_tier_used(v_u30, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 upgraded on day 30 exactly]: not counted as used';
        END IF;
        IF public.promo_tier_used(v_r1, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 refund of the claiming payment]: counted as used';
        END IF;
        IF NOT public.promo_tier_used(v_r2, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 refund of a later renewal]: not counted as used';
        END IF;
        IF NOT public.promo_tier_used(v_d, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 cancelled]: not counted as used';
        END IF;
        IF public.promo_tier_used(v_l10, 'pro', 2::smallint)
           OR NOT public.promo_tier_used(v_l35, 'pro', 2::smallint) THEN
            RAISE EXCEPTION '225 self-test [T4 legacy upgraded day 10 / 35]: used % / % (expected false / true)',
                public.promo_tier_used(v_l10, 'pro', 2::smallint), public.promo_tier_used(v_l35, 'pro', 2::smallint);
        END IF;
        RAISE NOTICE '225 [T4 D12]: OK — upgraded day 10 → not used (quoted again); day 30 / 35 → used; refund of claiming payment → not used; refund of later renewal → used; cancelled → used; legacy seat day 10 / 35 → not used / used';

        -- ── T5. the claims: pro → pro_plus and pro_plus → max release 'upgraded' ──
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_a, 'pro_plus', 99.90, 'paid', now(), 'pro_plus_t1_a',
                '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay;
        SELECT c.action, c.seat_id INTO v_act, v_new FROM public.claim_early_adopter_seat(v_a, v_pay) c;
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_aseat;
        IF v_act IS DISTINCT FROM 'claimed'
           OR v_r.release_reason IS DISTINCT FROM 'upgraded'
           OR v_r.released_by_payment_id IS DISTINCT FROM v_pay
           OR NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s
                           WHERE s.seat_id = v_new AND s.batch_id = 'pro_plus_t1_a' AND s.plan_id = 'pro_plus'
                             AND s.price_tier = 1 AND s.price_sar = 99.90
                             AND s.promo_ends_at = now() + interval '60 days') THEN
            RAISE EXCEPTION '225 self-test [T5 pro → pro_plus claim]: action %, old seat %/%', v_act, v_r.release_reason, v_r.released_by_payment_id;
        END IF;
        -- D12 end to end: the Pro offer upgraded on day 10 is NOT used
        IF public.promo_tier_used(v_a, 'pro', 2::smallint)
           OR NOT public.promo_tier_used(v_a, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T5 D12 after the switch]: pro t2 used %, pro_plus t1 used %',
                public.promo_tier_used(v_a, 'pro', 2::smallint), public.promo_tier_used(v_a, 'pro_plus', 1::smallint);
        END IF;

        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_c, 'max', 169.90, 'paid', now(), 'max_t2_c',
                '{"offer_sar": "169.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay;
        SELECT c.action, c.seat_id INTO v_act, v_new FROM public.claim_early_adopter_seat(v_c, v_pay) c;
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_cseat;
        IF v_act IS DISTINCT FROM 'claimed'
           OR v_r.release_reason IS DISTINCT FROM 'upgraded'
           OR v_r.released_by_payment_id IS DISTINCT FROM v_pay
           OR NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s
                           WHERE s.seat_id = v_new AND s.batch_id = 'max_t2_c' AND s.price_sar = 169.90) THEN
            RAISE EXCEPTION '225 self-test [T5 pro_plus → max claim]: action %, old seat %/%', v_act, v_r.release_reason, v_r.released_by_payment_id;
        END IF;
        IF public.promo_tier_used(v_c, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '225 self-test [T5 D12 pro_plus → max on day 10]: pro_plus t1 still counted as used';
        END IF;
        RAISE NOTICE '225 [T5 claims]: OK — pro→pro_plus and pro_plus→max claims release the old seat ''upgraded'' (released_by = the payment); D12 leaves the day-10 offer unused';

        RAISE EXCEPTION USING ERRCODE = 'LU225', MESSAGE = '225 self-test sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU225' THEN
        RAISE NOTICE '225: behavioural self-tests T0–T5 passed; every test write rolled back';
    END;

    -- ── nothing left behind ──
    IF (SELECT count(*) FROM public.early_adopter_seats) <> v_seats0
       OR (SELECT count(*) FROM public.payment_transactions) <> v_pays0
       OR (SELECT count(*) FROM public.users) <> v_users0 THEN
        RAISE EXCEPTION '225 self-test left rows behind (seats/payments/users counts changed)';
    END IF;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-225-%') THEN
        RAISE EXCEPTION '225 self-test left fake auth.users rows behind';
    END IF;
    IF (SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), '')) FROM public.promo_batches b) IS DISTINCT FROM v_batches0
       OR (SELECT md5(COALESCE(string_agg(p::text, '|' ORDER BY p.plan_id), '')) FROM public.plans p) IS DISTINCT FROM v_plans0
       OR (SELECT md5(c::text) FROM public.early_adopter_campaign c WHERE c.id) IS DISTINCT FROM v_camp0
       OR (SELECT md5(c::text) FROM public.intro_offer_campaign c WHERE c.id) IS DISTINCT FROM v_intro0 THEN
        RAISE EXCEPTION '225 self-test did not restore plans / batches / switches';
    END IF;
END
$selftest$;


-- ════════════════════════════════════════════════════════════════════════════
-- 11. Ships dark: every sampled real user is priced exactly as before
-- ════════════════════════════════════════════════════════════════════════════
DO $dark$
DECLARE
    v_n    BIGINT;
    v_tot  BIGINT;
    v_s    TEXT;
    v_pub  TEXT;
BEGIN
    SELECT count(*) FILTER (WHERE x.before_c IS DISTINCT FROM x.after_c OR x.before_u IS DISTINCT FROM x.after_u),
           count(*),
           string_agg(format('%s/%s: %s → %s; %s → %s', x.user_id, x.plan_id, x.before_c, x.after_c,
                             x.before_u, x.after_u), ' ‖ ')
               FILTER (WHERE x.before_c IS DISTINCT FROM x.after_c OR x.before_u IS DISTINCT FROM x.after_u)
      INTO v_n, v_tot, v_s
      FROM (SELECT b.user_id, b.plan_id, b.checkout AS before_c, b.used AS before_u,
                   (SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id,
                                             c.price_tier, c.months, c.intro_group), ';')
                      FROM public.promo_checkout_price(b.user_id, b.plan_id) c) AS after_c,
                   format('%s|%s|%s|%s',
                          public.promo_tier_used(b.user_id, 'pro', 1::smallint),
                          public.promo_tier_used(b.user_id, 'pro', 2::smallint),
                          public.promo_tier_used(b.user_id, 'max', 1::smallint),
                          public.promo_tier_used(b.user_id, 'max', 2::smallint)) AS after_u
              FROM _m225_before b) x;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '225 dark: % of % (user, plan) prices / tier histories changed: %', v_n, v_tot, left(v_s, 2000);
    END IF;

    SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id)
      INTO v_pub FROM public.promo_public_quote() q;
    IF v_pub IS DISTINCT FROM (SELECT p.q FROM _m225_public p) THEN
        RAISE EXCEPTION '225 dark: promo_public_quote() changed: % → %', (SELECT p.q FROM _m225_public p), v_pub;
    END IF;

    -- pro_plus is not purchasable by anybody while it has no list price
    IF (SELECT p.price_sar FROM public.plans p WHERE p.plan_id = 'pro_plus') IS NULL THEN
        SELECT count(*) INTO v_n
          FROM _m225_sample s
         CROSS JOIN LATERAL public.promo_checkout_price(s.user_id, 'pro_plus') c
         WHERE c.price_sar IS NOT NULL OR c.batch_id IS NOT NULL;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '225 dark: % sampled user(s) could buy pro_plus while it has no list price', v_n;
        END IF;
    END IF;

    RAISE NOTICE '225 dark: OK — % (user, plan) checkout rows + tier histories identical before/after across % real users; public promo unchanged (%); pro_plus unpriced for all of them',
        v_tot, (SELECT count(*) FROM _m225_sample), v_pub;
END
$dark$;

-- PostgREST: reload the function catalogue (also fired by the ddl event trigger)
NOTIFY pgrst, 'reload schema';

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- LAUNCH SWITCH (plan §3.5) — run by hand, after 224/225 AND the backend that
-- knows pro_plus are deployed. DB first (purchasable but unseen is harmless),
-- then the web card. Purge the ISR cache for '/' and '/pricing' after.
-- ════════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- UPDATE public.plans SET price_sar = 149.90, updated_at = now() WHERE plan_id = 'pro_plus';
-- UPDATE public.promo_batches SET enabled = true  WHERE batch_id IN ('pro_plus_t1_a', 'max_t2_c');
-- UPDATE public.promo_batches SET enabled = false WHERE batch_id IN ('max_t1_a', 'max_t2_a', 'max_t2_b');
-- SELECT * FROM public.promo_public_quote();   -- pro 59.90/2, pro_plus 99.90/2, max 169.90/2
-- COMMIT;
--
-- Verify: SELECT * FROM public.promo_checkout_price('<dev on pro>', 'pro_plus');
--         -- true | 99.90 | pro_plus_t1_a | 1 | 2 | (null)
--
-- ROLLBACK OF THE SWITCH — Pro Plus. ⚠ It depends on whether anybody is ON or
-- HEADED TO Pro Plus. Both must be 0:
--   SELECT count(*) FROM public.payment_transactions WHERE plan_id = 'pro_plus';
--   SELECT count(*) FROM public.user_subscriptions
--    WHERE plan_id = 'pro_plus' OR renewal_plan_id = 'pro_plus';
--   (the second catches a manual / code grant with no payment and a pending
--   scheduled downgrade — either would lapse silently at its next renewal)
--
--   (a) BOTH 0 (nobody paid, nobody holds it, nobody is scheduled onto it) —
--       the plan can go back to dark:
--       UPDATE public.plans SET price_sar = NULL, updated_at = now() WHERE plan_id = 'pro_plus';
--       UPDATE public.promo_batches SET enabled = false WHERE batch_id = 'pro_plus_t1_a';
--
--   (b) EITHER > 0 — NEVER null the price. price_sar NULL is not "stop selling":
--       effective_plan_price(user, 'pro_plus', 'current') is then NULL for
--       every Pro Plus subscriber outside a live seat window, the renewal job
--       skips them (skipped_bad_price) and they silently lapse. (A live seat
--       still prices its own window — 226 — but not one day past it.) Stop the
--       OFFER instead and hide the web card (frontend); the list price stays, so
--       running terms renew at 149.90:
--       UPDATE public.promo_batches SET enabled = false WHERE batch_id = 'pro_plus_t1_a';
--       Held pro_plus seats keep their frozen price to the end of their window
--       (a seat never reads enabled). Note: with the price kept, the checkout
--       API still sells pro_plus at LIST to anyone who calls it directly; if
--       that must stop too, it is a backend switch, not a NULL price.
--
-- ROLLBACK OF THE SWITCH — the Max offer (reviewed: safe after sales). Turning a
-- batch off only stops NEW quotes: held max_t2_c seats keep their frozen
-- 169.90 to the end of their window, the Max list price (224) is not touched,
-- and nobody's renewal reads a batch's enabled flag:
--   UPDATE public.promo_batches SET enabled = false WHERE batch_id = 'max_t2_c';
--   UPDATE public.promo_batches SET enabled = true  WHERE batch_id IN ('max_t1_a', 'max_t2_a', 'max_t2_b');
--   (Tier history carries over: a max_t2_c buyer has used Max tier 2, so the
--   re-enabled max_t2_a / max_t2_b are not offered to them again; max_t1_a is,
--   if they never used tier 1 — exactly the pre-switch rule.)
--
-- ROLLBACK OF THIS FILE (only while no pro_plus payment, seat or subscription
-- exists, and with the backend rolled back first — it calls the 3-arg forms):
--   re-create promo_quote(uuid, text) and promo_checkout_price(uuid, text) from
--   the 2026-11-20 pg_get_functiondef (md5 in §0), DROP the 3-arg ones, re-apply
--   the same REVOKE / GRANT, and CREATE OR REPLACE promo_public_quote,
--   promo_tier_used, intro_offer_quote, redeem_plan_code from the same dump;
--   DELETE FROM public.promo_batches WHERE batch_id IN ('pro_plus_t1_a', 'max_t2_c');
--   DELETE FROM public.plans WHERE plan_id = 'pro_plus';
