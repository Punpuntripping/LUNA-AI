-- =============================================================================
-- 204: عرض الباقة الأساسية — 9.80 ر.س، مرة واحدة لكل مستخدم، 500 مقعد، بلا استرداد
-- =============================================================================
-- Owner decisions, 2026-11-01:
--   * Basic at 9.80 instead of 39.90, for 500 seats.
--   * Open to EVERYONE, including people who already bought Basic.
--   * Once per user. After that, and once the seats are gone, Basic is 39.90.
--   * No refund at this price.
--
-- SHIPS OFF. The batch row is inserted with enabled = false, so applying this
-- changes no price and no answer for anyone. Order:
--   1. apply this migration;
--   2. deploy backend + frontend (the code that claims the seat and that stops
--      /pay/basic describing a renewal Basic does not have);
--   3. UPDATE public.promo_batches SET enabled = true WHERE batch_id = 'basic_t1_a';
-- Off switch: the same UPDATE with false. A sold seat stays sold.
--
-- WHY ITS OWN LEDGER, NOT early_adopter_seats. That table allows one live seat
-- per user (early_adopter_seats_one_live) and claiming a batch releases whatever
-- the user held as 'upgraded'. A Basic seat there would destroy a lapsed Pro
-- user's promised price, an upgrade from Basic would free a Basic seat so more
-- than 500 could be sold, and early_adopter_status / the ladder-off pricing read
-- "any live seat" as a Pro/Max promise. A row here means one thing, forever:
-- this user has used the offer.
--
-- WHY THE PRICE STILL LIVES IN promo_batches. Checkout already prices from
-- promo_checkout_price and stamps promo_batch_id + intro_offer_terms from it, so
-- a batch row gives Basic all of that with no change to the checkout, and the
-- price and seat limit stay one-row UPDATEs like every other batch.
--
-- A seat is never released. There is no self-serve refund at this price, and a
-- refund an operator issues from the Moyasar dashboard leaves the seat used.
--
-- months = 1 is a formality: the column is NOT NULL and the public quote drops a
-- row without it. It never reaches a buyer as a duration and nothing recurs.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The ledger ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.basic_offer_seats (
    seat_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- SET NULL like early_adopter_seats: a deleted account's seat stays counted.
    user_id       UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    payment_id    UUID NOT NULL UNIQUE
                  REFERENCES public.payment_transactions(payment_id) ON DELETE CASCADE,
    batch_id      TEXT NOT NULL REFERENCES public.promo_batches(batch_id),
    price_sar     NUMERIC(10,2) NOT NULL CHECK (price_sar >= 1.00),
    over_capacity BOOLEAN NOT NULL DEFAULT false,
    claimed_at    TIMESTAMPTZ NOT NULL,
    CONSTRAINT basic_offer_seats_one_per_user UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_basic_offer_seats_batch
    ON public.basic_offer_seats (batch_id);

ALTER TABLE public.basic_offer_seats ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.basic_offer_seats FROM PUBLIC;
REVOKE ALL ON TABLE public.basic_offer_seats FROM anon;
REVOKE ALL ON TABLE public.basic_offer_seats FROM authenticated;
GRANT  ALL ON TABLE public.basic_offer_seats TO service_role;

COMMENT ON TABLE public.basic_offer_seats IS
    '204: one row per user who bought Basic at the offer price. Never released: the '
    'offer is once per user and non-refundable. Separate from early_adopter_seats so '
    'a Basic purchase can never touch a Pro/Max seat.';

-- ── 2. The batch, switched off ───────────────────────────────────────────────

INSERT INTO public.promo_batches
    (batch_id, plan_id, price_tier, batch_order, price_sar, months, seat_limit,
     profession_only, enabled)
VALUES
    ('basic_t1_a', 'basic', 1, 1, 9.80, 1, 500, false, false)
ON CONFLICT (batch_id) DO NOTHING;

-- ── 3. Live functions, changed only where marked (204) or in the seat count ──
--    Bodies copied from production; CREATE OR REPLACE keeps their grants.

CREATE OR REPLACE FUNCTION public.promo_active_batch(p_plan_id text, p_tier smallint)
 RETURNS SETOF promo_batches
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT b.*
      FROM public.promo_batches b
     WHERE b.plan_id = p_plan_id
       AND b.price_tier = p_tier
       AND b.enabled
       AND (SELECT count(*)
              FROM public.early_adopter_seats s
             WHERE s.released_at IS NULL
               AND s.batch_id = b.batch_id)
         + (SELECT count(*)
              FROM public.basic_offer_seats o
             WHERE o.batch_id = b.batch_id) < b.seat_limit
     ORDER BY b.batch_order
     LIMIT 1;
$function$;

CREATE OR REPLACE FUNCTION public.early_adopter_open()
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT COALESCE(
        (SELECT CASE
                    WHEN c.ladder_v2 THEN
                         -- 192: "some batch is open"
                         c.enabled
                         AND EXISTS (
                             SELECT 1
                               FROM public.promo_batches b
                              WHERE b.enabled
                                AND (SELECT count(*)
                                       FROM public.early_adopter_seats s
                                      WHERE s.released_at IS NULL
                                        AND s.batch_id = b.batch_id)
                                  + (SELECT count(*)
                                       FROM public.basic_offer_seats o
                                      WHERE o.batch_id = b.batch_id) < b.seat_limit)
                    ELSE
                         -- 138, unchanged
                         c.enabled
                         AND (SELECT count(*)
                                FROM public.early_adopter_seats s
                               WHERE s.released_at IS NULL) < c.seat_limit
                END
           FROM public.early_adopter_campaign c
          WHERE c.id),
        false);
$function$;

CREATE OR REPLACE FUNCTION public.promo_quote(p_user_id uuid, p_plan_id text)
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
BEGIN
    IF p_user_id IS NULL OR p_plan_id IS NULL OR p_plan_id NOT IN ('basic', 'pro', 'max') THEN
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

    -- The plan the user is ON right now. The view folds expiry → 'free' (and
    -- locked → NULL); never re-derive it here (183). It is security_invoker:
    -- inside this SECURITY DEFINER function it runs as the owner, exactly as
    -- 183's intro_offer_quote does. One read, by user_id, so the quota-state
    -- lateral runs for this user only.
    SELECT l.effective_plan_id
      INTO v_eff
      FROM public.user_subscriptions_live l
     WHERE l.user_id = p_user_id;

    -- §1.3-1: on Pro or Max now → no new seat for that plan; renewals are
    -- priced by the seat they hold. Max → Pro is a downgrade, never quoted.
    IF v_eff = p_plan_id OR (v_eff = 'max' AND p_plan_id = 'pro') THEN
        RETURN;
    END IF;

    -- (I2) A live seat still inside its window keeps pricing what it prices
    -- (effective_plan_price branch b); a quote here would contradict the
    -- charge. A BATCH seat prices only its own plan. A LEGACY seat prices EVERY
    -- plan (as before 192), so it blocks every quote — except the one
    -- owner-approved switch (A3): a legacy seat claimed by a PRO payment, asked
    -- for MAX, takes the Max quote and is released 'upgraded' by the claim. The
    -- COALESCE keeps a legacy seat with no payment (t.plan_id NULL) blocking.
    IF EXISTS (
        SELECT 1
          FROM public.early_adopter_seats s
          LEFT JOIN public.payment_transactions t ON t.payment_id = s.payment_id
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
           AND s.promo_ends_at > now()
           AND CASE WHEN s.batch_id IS NOT NULL THEN s.plan_id = p_plan_id
                    ELSE NOT COALESCE(t.plan_id = 'pro' AND p_plan_id = 'max', false)
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

    IF p_plan_id = 'max' THEN
        v_t1_ok := true;                                 -- Max tier 1: everyone
    ELSE
        -- Pro tier 1: the three professions, the intro window (§1.4-9), and
        -- NOT while a Basic term runs (§1.3-2 / A10).
        v_t1_ok := v_prof_ok
                   AND v_eff IS DISTINCT FROM 'basic'
                   AND EXISTS (SELECT 1
                                 FROM public.intro_offer_campaign ic
                                WHERE ic.id
                                  AND ic.enabled
                                  AND now() < ic.ends_at);
    END IF;

    -- Tier 1, else tier 2; each once per plan; batches fill in order. A tier
    -- whose batches are all full falls through to the next (A2).
    FOREACH v_tier IN ARRAY ARRAY[1, 2]::smallint[] LOOP
        CONTINUE WHEN v_tier = 1 AND NOT v_t1_ok;
        CONTINUE WHEN public.promo_tier_used(p_user_id, p_plan_id, v_tier);

        SELECT * INTO v_b FROM public.promo_active_batch(p_plan_id, v_tier);
        CONTINUE WHEN NOT FOUND;
        CONTINUE WHEN v_b.profession_only AND NOT v_prof_ok;          -- (I7)

        RETURN QUERY
            SELECT v_b.batch_id, v_b.price_tier, round(v_b.price_sar, 2), v_b.months;
        RETURN;
    END LOOP;

    RETURN;
END;
$function$;

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

    -- max: tier 1 while it sells, then tier 2
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

CREATE OR REPLACE FUNCTION public.effective_plan_price(p_user_id uuid, p_plan_id text, p_context text DEFAULT 'purchase'::text, p_at timestamp with time zone DEFAULT NULL::timestamp with time zone)
 RETURNS numeric
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_plan  RECORD;
    v_price NUMERIC;
BEGIN
    IF p_context IS NULL OR p_context NOT IN ('purchase', 'current') THEN
        RAISE EXCEPTION
            'effective_plan_price: unknown context % (expected ''purchase'' or ''current'')',
            p_context
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.price_sar, p.promo_price_sar, p.billing_cycle
      INTO v_plan
      FROM public.plans p
     WHERE p.plan_id = p_plan_id;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    -- (b1) THE BATCH PROMISE (192) — flag-independent. Only for the seat's own
    -- plan; priced at the instant being charged FOR (182's p_at).
    IF v_plan.price_sar IS NOT NULL AND v_plan.billing_cycle = 'recurring_30d' THEN
        SELECT s.price_sar
          INTO v_price
          FROM public.early_adopter_seats s
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
           AND s.batch_id IS NOT NULL
           AND s.plan_id = p_plan_id
           AND s.promo_ends_at > COALESCE(p_at, now())
         ORDER BY s.claimed_at DESC
         LIMIT 1;
        IF FOUND THEN
            RETURN round(v_price, 2);
        END IF;
    END IF;

    -- ══ ladder_v2 ON (192) ══
    IF COALESCE((SELECT c.ladder_v2 FROM public.early_adopter_campaign c WHERE c.id), false) THEN
        IF v_plan.price_sar IS NULL THEN
            RETURN NULL;
        END IF;

        IF v_plan.billing_cycle = 'recurring_30d' THEN
            -- (c) 'purchase' only — the checkout about to claim a batch seat.
            -- 'current' never quotes: a renewal is priced by the seat or list.
            IF p_context = 'purchase' THEN
                SELECT q.price_sar
                  INTO v_price
                  FROM public.promo_quote(p_user_id, p_plan_id) q
                 LIMIT 1;
                IF FOUND THEN
                    RETURN round(v_price, 2);
                END IF;
            END IF;

            -- (b2) THE LEGACY PROMISE — today's rule, any plan.
            IF v_plan.promo_price_sar IS NOT NULL
               AND EXISTS (SELECT 1
                             FROM public.early_adopter_seats s
                            WHERE s.user_id = p_user_id
                              AND s.released_at IS NULL
                              AND s.batch_id IS NULL
                              AND s.promo_ends_at > COALESCE(p_at, now())) THEN
                RETURN round(v_plan.promo_price_sar, 2);
            END IF;

        -- (204) a one-time plan: 'purchase' answers what checkout will charge.
        -- 'current' stays the list price; no seat ever prices a one-time plan.
        ELSIF p_context = 'purchase' THEN
            SELECT q.price_sar
              INTO v_price
              FROM public.promo_quote(p_user_id, p_plan_id) q
             LIMIT 1;
            IF FOUND THEN
                RETURN round(v_price, 2);
            END IF;
        END IF;

        -- Every other plan: list.
        RETURN round(v_plan.price_sar, 2);
    END IF;

    -- ══ ladder_v2 OFF — 183's body ══
    IF v_plan.price_sar IS NULL OR v_plan.promo_price_sar IS NULL THEN
        RETURN round(v_plan.price_sar, 2);
    END IF;

    IF v_plan.billing_cycle = 'recurring_30d' THEN
        -- (a) forfeiture — except intro-offer members (183), and (V4) except a
        -- user holding a live batch seat inside its window: that seat's
        -- carry-over below must survive a rollback even beside an old
        -- 'cancelled' legacy row (none exists before the first flip, so this is
        -- 183's predicate exactly until then). 'upgraded' / 'refund' never match.
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.release_reason = 'cancelled')
           AND NOT public.is_intro_offer_member(p_user_id)
           AND NOT EXISTS (SELECT 1
                             FROM public.early_adopter_seats s
                            WHERE s.user_id = p_user_id
                              AND s.released_at IS NULL
                              AND s.batch_id IS NOT NULL
                              AND s.promo_ends_at > COALESCE(p_at, now())) THEN
            RETURN round(v_plan.price_sar, 2);
        END IF;

        -- (b) the promise (182), restored to "any live seat in its window"
        -- (V4): a legacy seat, or a BATCH seat of the OTHER plan, carries over
        -- at promo_price_sar exactly as every seat did before 192. A batch seat
        -- of THIS plan inside its window already returned its own price in (b1).
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.released_at IS NULL
                      AND s.promo_ends_at > COALESCE(p_at, now())) THEN
            RETURN round(v_plan.promo_price_sar, 2);
        END IF;

        -- (c) 'purchase' only, while seats remain.
        IF p_context = 'purchase' AND public.early_adopter_open() THEN
            RETURN round(v_plan.promo_price_sar, 2);
        END IF;

        RETURN round(v_plan.price_sar, 2);
    END IF;

    IF p_context = 'purchase' AND public.early_adopter_open() THEN
        RETURN round(v_plan.promo_price_sar, 2);
    END IF;

    RETURN round(v_plan.price_sar, 2);
END;
$function$;

-- ── 4. The claim ─────────────────────────────────────────────────────────────
-- Called beside grant_plan after a Basic batch sale settles. Separate from
-- claim_early_adopter_seat so that function, and every Pro/Max seat, is untouched.

CREATE OR REPLACE FUNCTION public.claim_basic_offer_seat(p_user_id uuid, p_payment_id uuid)
 RETURNS TABLE(seat_id uuid, over_capacity boolean, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_pay   RECORD;
    v_batch RECORD;
    v_seat  RECORD;
    v_raw   TEXT;
    v_price NUMERIC;
    v_gross NUMERIC;
    v_live  INTEGER;
    v_over  BOOLEAN := false;
    v_id    UUID;
BEGIN
    -- Every writer of the ledger takes this, so the capacity count cannot race.
    PERFORM pg_advisory_xact_lock(hashtext('basic_offer_seats')::bigint);

    SELECT t.user_id, t.plan_id, t.status, t.paid_at, t.amount_sar,
           t.upgrade_credit_sar, t.promo_batch_id, t.intro_offer_terms
      INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id;

    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, false, 'payment_not_found'::text;
        RETURN;
    END IF;

    -- Idempotent on the payment: the webhook and /verify both run this.
    SELECT s.seat_id AS id, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.basic_offer_seats s
     WHERE s.payment_id = p_payment_id;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    IF p_user_id IS NULL OR (v_pay.user_id IS NOT NULL AND v_pay.user_id <> p_user_id) THEN
        RETURN QUERY SELECT NULL::uuid, false, 'user_mismatch'::text;
        RETURN;
    END IF;

    IF v_pay.plan_id IS DISTINCT FROM 'basic' OR v_pay.promo_batch_id IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, false, 'plan_not_eligible'::text;
        RETURN;
    END IF;

    IF v_pay.status IS DISTINCT FROM 'paid' THEN
        RETURN QUERY SELECT NULL::uuid, false, 'not_paid'::text;
        RETURN;
    END IF;

    SELECT b.batch_id AS bid, b.plan_id AS bplan, b.price_sar AS bprice, b.seat_limit AS blimit
      INTO v_batch
      FROM public.promo_batches b
     WHERE b.batch_id = v_pay.promo_batch_id;

    IF NOT FOUND OR v_batch.bplan IS DISTINCT FROM 'basic' THEN
        RAISE WARNING
            'claim_basic_offer_seat: payment % carries batch % which is not a Basic '
            'batch — no seat', p_payment_id, v_pay.promo_batch_id;
        RETURN QUERY SELECT NULL::uuid, false, 'plan_not_eligible'::text;
        RETURN;
    END IF;

    -- The QUOTED price, not the batch row's current one: an operator may edit the
    -- batch between checkout and settlement. Regex and cast in separate
    -- statements, because a cast error here would 500 a webhook with the money in.
    v_raw := v_pay.intro_offer_terms ->> 'offer_sar';
    IF v_raw ~ '^[0-9]+(\.[0-9]+)?$' THEN
        v_price := round(v_raw::numeric, 2);
    ELSE
        v_price := v_batch.bprice;
    END IF;

    -- One cent covers rounding; NULL fails closed.
    v_gross := v_pay.amount_sar + COALESCE(v_pay.upgrade_credit_sar, 0);
    IF v_gross IS NULL OR v_gross > v_price + 0.01 THEN
        RETURN QUERY SELECT NULL::uuid, false, 'not_promo_priced'::text;
        RETURN;
    END IF;

    -- Once per user. A second offer-priced payment can only get here if two
    -- checkouts settled together; it earns no second seat.
    SELECT s.seat_id AS id, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.basic_offer_seats s
     WHERE s.user_id = p_user_id;

    IF FOUND THEN
        RAISE WARNING
            'claim_basic_offer_seat: user % already used the offer; payment % was '
            'still charged the offer price', p_user_id, p_payment_id;
        RETURN QUERY SELECT v_seat.id, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    -- A quote that was open when the last seat sold is honoured and stamped: the
    -- buyer was shown this price and has paid it.
    SELECT count(*) INTO v_live
      FROM public.basic_offer_seats s
     WHERE s.batch_id = v_batch.bid;

    v_over := v_live >= v_batch.blimit;

    BEGIN
        INSERT INTO public.basic_offer_seats AS s
            (user_id, payment_id, batch_id, price_sar, over_capacity, claimed_at)
        VALUES
            (p_user_id, p_payment_id, v_batch.bid, v_price, v_over,
             COALESCE(v_pay.paid_at, now()))
        RETURNING s.seat_id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        SELECT s.seat_id AS id, s.over_capacity AS over_cap
          INTO v_seat
          FROM public.basic_offer_seats s
         WHERE s.payment_id = p_payment_id
            OR s.user_id = p_user_id
         LIMIT 1;

        RETURN QUERY SELECT v_seat.id, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END;

    RETURN QUERY SELECT v_id, v_over, 'claimed'::text;
END;
$function$;

REVOKE EXECUTE ON FUNCTION public.claim_basic_offer_seat(uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.claim_basic_offer_seat(uuid, uuid) TO service_role;

-- ── 5. Self-test ─────────────────────────────────────────────────────────────
-- Runs against the real functions, then undoes all of it: the closing RAISE
-- rolls this block back, and any failed check aborts the whole migration.
-- Test payments are INSERTed as 'paid', so trg_payment_receipt_no (UPDATE only)
-- never burns a receipt number.

DO $selftest$
DECLARE
    v_u1      UUID;
    v_u2      UUID;
    v_past    UUID;
    v_holder  UUID;
    v_pay     UUID;
    v_list    NUMERIC;
    v_terms   JSONB := jsonb_build_object('offer_sar', '9.80', 'offer_months', 1, 'list_sar', '39.90');
    v_r       RECORD;
    v_n       INTEGER;
    v_price   NUMERIC;
    v_open    BOOLEAN;
    v_seat    UUID;
    v_pro_was NUMERIC;
    v_pro_now NUMERIC;
BEGIN
    IF NOT COALESCE((SELECT c.ladder_v2 AND c.enabled
                       FROM public.early_adopter_campaign c WHERE c.id), false) THEN
        RAISE NOTICE '204 self-test: skipped, the campaign is not running here';
        RETURN;
    END IF;

    SELECT p.price_sar INTO v_list FROM public.plans p WHERE p.plan_id = 'basic';

    SELECT u.user_id INTO v_u1 FROM public.users u
     WHERE NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.user_id = u.user_id)
     ORDER BY u.user_id LIMIT 1;
    SELECT u.user_id INTO v_u2 FROM public.users u
     WHERE NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.user_id = u.user_id)
       AND u.user_id <> v_u1
     ORDER BY u.user_id LIMIT 1;

    IF v_u2 IS NULL THEN
        RAISE NOTICE '204 self-test: skipped, needs two users';
        RETURN;
    END IF;

    SELECT t.user_id INTO v_past FROM public.payment_transactions t
     WHERE t.plan_id = 'basic' AND t.status = 'paid' AND t.user_id IS NOT NULL
       AND t.user_id NOT IN (v_u1, v_u2)
     LIMIT 1;

    SELECT s.user_id, s.seat_id INTO v_holder, v_seat FROM public.early_adopter_seats s
     WHERE s.released_at IS NULL AND s.user_id IS NOT NULL
     LIMIT 1;

    BEGIN
        -- Grants and RLS
        IF has_table_privilege('anon', 'public.basic_offer_seats', 'SELECT')
           OR has_table_privilege('authenticated', 'public.basic_offer_seats', 'SELECT') THEN
            RAISE EXCEPTION '204 self-test [clients must not read the ledger]';
        END IF;
        IF has_function_privilege('authenticated', 'public.claim_basic_offer_seat(uuid, uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION '204 self-test [clients must not claim]';
        END IF;
        IF NOT (SELECT c.relrowsecurity FROM pg_class c WHERE c.oid = 'public.basic_offer_seats'::regclass) THEN
            RAISE EXCEPTION '204 self-test [RLS must be on]';
        END IF;

        -- A. Switched off: nothing changes for anyone
        SELECT * INTO v_r FROM public.promo_checkout_price(v_u1, 'basic');
        IF v_r.price_sar IS DISTINCT FROM round(v_list, 2) OR v_r.batch_id IS NOT NULL THEN
            RAISE EXCEPTION '204 self-test [off: list price, no batch]: % %', v_r.price_sar, v_r.batch_id;
        END IF;
        SELECT count(*) INTO v_n FROM public.promo_quote(v_u1, 'basic');
        IF v_n <> 0 THEN
            RAISE EXCEPTION '204 self-test [off: no quote]';
        END IF;
        SELECT count(*) INTO v_n FROM public.promo_public_quote() q WHERE q.plan_id = 'basic';
        IF v_n <> 0 THEN
            RAISE EXCEPTION '204 self-test [off: not public]';
        END IF;

        v_open := public.early_adopter_open();
        SELECT q.price_sar INTO v_pro_was FROM public.promo_quote(v_u1, 'pro') q;

        UPDATE public.promo_batches SET enabled = true WHERE batch_id = 'basic_t1_a';

        -- B. Switched on: 9.80 for a first-time buyer, on all three reads
        SELECT * INTO v_r FROM public.promo_checkout_price(v_u1, 'basic');
        IF v_r.price_sar IS DISTINCT FROM 9.80 OR v_r.batch_id IS DISTINCT FROM 'basic_t1_a'
           OR v_r.intro_group IS NOT NULL THEN
            RAISE EXCEPTION '204 self-test [on: checkout]: % % %', v_r.price_sar, v_r.batch_id, v_r.intro_group;
        END IF;
        IF public.effective_plan_price(v_u1, 'basic', 'purchase') IS DISTINCT FROM 9.80 THEN
            RAISE EXCEPTION '204 self-test [on: purchase price]';
        END IF;
        IF public.effective_plan_price(v_u1, 'basic', 'current') IS DISTINCT FROM round(v_list, 2) THEN
            RAISE EXCEPTION '204 self-test [on: current stays list]';
        END IF;
        SELECT q.price_sar INTO v_price FROM public.promo_public_quote() q WHERE q.plan_id = 'basic';
        IF v_price IS DISTINCT FROM 9.80 THEN
            RAISE EXCEPTION '204 self-test [on: public quote]: %', v_price;
        END IF;

        -- C. A past Basic buyer qualifies too
        IF v_past IS NOT NULL THEN
            SELECT q.price_sar INTO v_price FROM public.promo_quote(v_past, 'basic') q;
            IF v_price IS DISTINCT FROM 9.80 THEN
                RAISE EXCEPTION '204 self-test [past buyer]: %', v_price;
            END IF;
        END IF;

        -- D. Claim, idempotently, and the offer is gone for that user
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_u1, 'basic', 9.80, 'paid', now(), 'basic_t1_a', v_terms)
        RETURNING payment_id INTO v_pay;

        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u1, v_pay);
        IF v_r.action IS DISTINCT FROM 'claimed' OR v_r.over_capacity THEN
            RAISE EXCEPTION '204 self-test [claim]: % %', v_r.action, v_r.over_capacity;
        END IF;
        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u1, v_pay);
        IF v_r.action IS DISTINCT FROM 'already_claimed' THEN
            RAISE EXCEPTION '204 self-test [claim twice]: %', v_r.action;
        END IF;

        SELECT * INTO v_r FROM public.promo_checkout_price(v_u1, 'basic');
        IF v_r.price_sar IS DISTINCT FROM round(v_list, 2) OR v_r.batch_id IS NOT NULL THEN
            RAISE EXCEPTION '204 self-test [once only]: % %', v_r.price_sar, v_r.batch_id;
        END IF;
        IF public.effective_plan_price(v_u1, 'basic', 'purchase') IS DISTINCT FROM round(v_list, 2) THEN
            RAISE EXCEPTION '204 self-test [once only, purchase price]';
        END IF;

        -- E. A second offer payment earns no second seat
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_u1, 'basic', 9.80, 'paid', now(), 'basic_t1_a', v_terms)
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u1, v_pay);
        SELECT count(*) INTO v_n FROM public.basic_offer_seats s WHERE s.user_id = v_u1;
        IF v_r.action IS DISTINCT FROM 'already_claimed' OR v_n <> 1 THEN
            RAISE EXCEPTION '204 self-test [one seat per user]: % %', v_r.action, v_n;
        END IF;

        -- F. Pro is untouched by a Basic seat
        SELECT q.price_sar INTO v_pro_now FROM public.promo_quote(v_u1, 'pro') q;
        IF v_pro_now IS DISTINCT FROM v_pro_was THEN
            RAISE EXCEPTION '204 self-test [pro quote moved]: % -> %', v_pro_was, v_pro_now;
        END IF;

        -- G. A live Pro/Max seat survives its holder buying the offer
        IF v_holder IS NOT NULL THEN
            INSERT INTO public.payment_transactions
                (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
            VALUES (v_holder, 'basic', 9.80, 'paid', now(), 'basic_t1_a', v_terms)
            RETURNING payment_id INTO v_pay;
            SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_holder, v_pay);
            SELECT count(*) INTO v_n FROM public.early_adopter_seats s
             WHERE s.seat_id = v_seat AND s.released_at IS NULL;
            IF v_r.action IS DISTINCT FROM 'claimed' OR v_n <> 1 THEN
                RAISE EXCEPTION '204 self-test [pro seat must survive]: % %', v_r.action, v_n;
            END IF;
            DELETE FROM public.basic_offer_seats s WHERE s.user_id = v_holder;
        END IF;

        -- H. A list-priced or unstamped Basic payment earns nothing
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_u2, 'basic', 39.90, 'paid', now(), 'basic_t1_a', v_terms)
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u2, v_pay);
        IF v_r.action IS DISTINCT FROM 'not_promo_priced' THEN
            RAISE EXCEPTION '204 self-test [list price, stamped]: %', v_r.action;
        END IF;

        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_u2, 'basic', 39.90, 'paid', now())
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u2, v_pay);
        IF v_r.action IS DISTINCT FROM 'plan_not_eligible' THEN
            RAISE EXCEPTION '204 self-test [list price, unstamped]: %', v_r.action;
        END IF;

        -- I. Full: no quote, not public, a Pro/Max campaign unaffected
        UPDATE public.promo_batches SET seat_limit = 1 WHERE batch_id = 'basic_t1_a';

        SELECT count(*) INTO v_n FROM public.promo_quote(v_u2, 'basic');
        IF v_n <> 0 THEN
            RAISE EXCEPTION '204 self-test [full: no quote]';
        END IF;
        SELECT count(*) INTO v_n FROM public.promo_public_quote() q WHERE q.plan_id = 'basic';
        IF v_n <> 0 THEN
            RAISE EXCEPTION '204 self-test [full: not public]';
        END IF;
        IF public.early_adopter_open() IS DISTINCT FROM v_open THEN
            RAISE EXCEPTION '204 self-test [full: campaign state moved]';
        END IF;

        -- J. A quote left open when the seats ran out is honoured and stamped
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_u2, 'basic', 9.80, 'paid', now(), 'basic_t1_a', v_terms)
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_r FROM public.claim_basic_offer_seat(v_u2, v_pay);
        IF v_r.action IS DISTINCT FROM 'claimed' OR NOT v_r.over_capacity THEN
            RAISE EXCEPTION '204 self-test [over capacity]: % %', v_r.action, v_r.over_capacity;
        END IF;

        RAISE EXCEPTION 'rollback' USING ERRCODE = 'LN204';
    EXCEPTION WHEN SQLSTATE 'LN204' THEN
        RAISE NOTICE '204 self-test: all checks passed, test rows rolled back';
    END;
END;
$selftest$;

COMMIT;

-- Rollback, once applied:
--   UPDATE public.promo_batches SET enabled = false WHERE batch_id = 'basic_t1_a';
-- That is the whole off switch. The function changes are inert with no enabled
-- Basic batch, and basic_offer_seats is the record of who paid 9.80, so keep it.
