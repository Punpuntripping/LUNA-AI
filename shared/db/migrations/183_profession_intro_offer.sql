-- ════════════════════════════════════════════════════════════════════════════
-- 183 — عرض الشهر الأول: the profession intro offer on «الاحترافية» (pro)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/profession_intro_offer.md §1, §3.1.
-- Owner decisions 2026-10-21 (reflection session):
--   * pro's FIRST month costs 1.99 for `legal`, 9.90 for `specialist` and
--     `entrepreneur`; months 2–3 are the early-adopter promo (49.90) through the
--     seat the purchase claims; list price from month 4.
--   * eligible = offer profession + not subscribed to anything (effective plan
--     free / locked) + never paid for pro or max (a basic-only past buyer IS
--     eligible) + campaign open (this switch, before ends_at, AND seats left).
--   * no code — the server decides; the first month is non-refundable.
--   * ends 2026-12-31 23:59:59 Asia/Riyadh = 2026-12-31T20:59:59Z, or earlier
--     when the early-adopter seats run out.
--   * the offer follows the FIRST profession answer (users.profession_first_
--     group, write-once, §4b) — changing the profession later can neither
--     unlock nor lose it.
--   * intro-offer members are EXEMPT from cancellation forfeiture: after a
--     lapsed cancel they buy pro again at the early-adopter price with a fresh
--     90-day seat while that campaign is open. Everyone else still forfeits
--     permanently (/promo-terms §3.1).
-- Depends on: 068 (plans), 113/117/119/132 (payment_transactions), 115
--             (users.profession_group), 120 (user_subscriptions_live), 126
--             (product_docs), 138 + 182 (early-adopter seats, 4-arg
--             effective_plan_price). ⚠ 182 MUST BE APPLIED FIRST — the guard
--             below refuses to run otherwise.
-- Idempotent: IF NOT EXISTS / ON CONFLICT DO NOTHING / CREATE OR REPLACE /
--             guarded append. Re-runnable.
--
-- ── SHIPS INERT ──────────────────────────────────────────────────────────────
-- intro_offer_campaign.enabled = false. With the switch down intro_offer_quote
-- returns zero rows for everybody, so applying this and deploying the backend
-- that reads it changes behaviour for NO user. The launch is one row:
--
--     UPDATE public.intro_offer_campaign SET enabled = true WHERE id;
--
-- and the off switch is the same row set back to false. Prices are a plain
-- UPDATE on intro_offer_prices; nothing in code names an amount.
--
-- ── WHAT THIS FILE CHANGES IN THE LIVE MONEY PATH ───────────────────────────
-- Three functions get ONE extra condition each — the forfeiture exemption —
-- and nothing else (bodies = LIVE definitions read 2026-10-21):
--   * effective_plan_price (182's 4-arg)  branch (a)
--   * claim_early_adopter_seat            the 'forfeited' early return
--   * early_adopter_status                v_forfeit  ← NOT in the plan's
--     contract; added because its own comment says it is "the third copy of the
--     forfeiture predicate" and has_seat "must mean EXACTLY what
--     effective_plan_price charges". Leaving it out would report a returning
--     intro member's NEW seat as has_seat = false (their old 'cancelled' row
--     still exists), while effective_plan_price charges them the promo.
-- All three call the same helper, is_intro_offer_member(), so the copies cannot
-- drift apart.
--
-- ── WHY payment_transactions GETS NO FK ─────────────────────────────────────
-- intro_offer_group is copied from users.profession_first_group (via
-- intro_offer_quote) at checkout and is a historical fact about that charge. A financial ledger must never depend on a
-- config table: deleting or renaming a price row must not cascade into, or be
-- blocked by, settled payments.
--
-- ── DEPLOY ORDER ────────────────────────────────────────────────────────────
-- 182 → (backend with p_at) → 183 → backend that selects intro_offer_group /
-- intro_offer_terms. The backend adds both columns to _TXN_COLUMNS; selecting
-- a missing column 42703s checkout AND /history for everybody. Migration first,
-- always.

BEGIN;

-- §4's ADD COLUMN takes an ACCESS EXCLUSIVE lock on payment_transactions until
-- COMMIT. Fail fast instead of queueing behind a long query (and making every
-- checkout and webhook queue behind US); the rest of this transaction is
-- milliseconds, so the lock is held only briefly once taken.
SET LOCAL lock_timeout = '5s';

-- ── 0. 182 must be live ─────────────────────────────────────────────────────
-- CREATE OR REPLACE on a 4-arg effective_plan_price that does not exist yet
-- would CREATE a second overload beside 138's 3-arg one — the exact PostgREST
-- ambiguity (PGRST203 → catalog price for everyone) 182 exists to prevent.
DO $$
DECLARE
    v_n INTEGER;
BEGIN
    SELECT count(*) INTO v_n
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
    IF v_n <> 1 OR to_regprocedure(
           'public.effective_plan_price(uuid, text, text, timestamptz)') IS NULL THEN
        RAISE EXCEPTION
            '183: apply 182 first — expected exactly one 4-arg effective_plan_price, found % overload(s)',
            v_n;
    END IF;
END $$;

-- ── 1. intro_offer_campaign (singleton switch + end date) ───────────────────
CREATE TABLE IF NOT EXISTS public.intro_offer_campaign (
    id         boolean     PRIMARY KEY DEFAULT true CHECK (id),
    enabled    boolean     NOT NULL DEFAULT false,
    ends_at    timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.intro_offer_campaign IS
    'Singleton switch for عرض الشهر الأول (183). enabled AND now() < ends_at AND '
    'early_adopter_open() ⇒ intro_offer_quote may return a price. Ships DISABLED; '
    'launch = UPDATE … SET enabled = true. Never exposed to clients — neither the '
    'switch nor ends_at (the offer never shows an end date). Service-role only.';
COMMENT ON COLUMN public.intro_offer_campaign.ends_at IS
    'Last instant a NEW offer quote can be issued. 2026-12-31T20:59:59Z = '
    '23:59:59 Asia/Riyadh. A quote issued before it and paid after it is honoured '
    '(the row''s amount_sar is authoritative). Existing offer holders keep months '
    '2–3 through their seat regardless.';

INSERT INTO public.intro_offer_campaign (id, enabled, ends_at)
VALUES (true, false, '2026-12-31T20:59:59Z')
ON CONFLICT (id) DO NOTHING;

-- ── 2. intro_offer_prices (first-month price per profession) ────────────────
CREATE TABLE IF NOT EXISTS public.intro_offer_prices (
    profession_group text          PRIMARY KEY,
    first_month_sar  numeric(10,2) NOT NULL CHECK (first_month_sar >= 1.00),
    updated_at       timestamptz   NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.intro_offer_prices IS
    'First-month price of «الاحترافية» (pro) per users.profession_first_group under '
    'عرض الشهر الأول (183). A group with no row gets no offer (individual, '
    'declined). 1.00 floor = Moyasar''s minimum charge (MIN_CHARGE_SAR). Months '
    '2–3 are NOT priced here — they are the early-adopter seat''s promo price. '
    'Service-role only.';

INSERT INTO public.intro_offer_prices (profession_group, first_month_sar) VALUES
    ('legal',        1.99),
    ('specialist',   9.90),
    ('entrepreneur', 9.90)
ON CONFLICT (profession_group) DO NOTHING;

-- ── 3. both tables: RLS on, no policies, service_role only ──────────────────
ALTER TABLE public.intro_offer_campaign ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intro_offer_prices   ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.intro_offer_campaign FROM anon;
REVOKE ALL ON TABLE public.intro_offer_campaign FROM authenticated;
GRANT  ALL ON TABLE public.intro_offer_campaign TO service_role;

REVOKE ALL ON TABLE public.intro_offer_prices FROM anon;
REVOKE ALL ON TABLE public.intro_offer_prices FROM authenticated;
GRANT  ALL ON TABLE public.intro_offer_prices TO service_role;

-- ── 4. payment_transactions: stamp the offer on the row it priced ───────────
ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS intro_offer_group text  NULL,
    ADD COLUMN IF NOT EXISTS intro_offer_terms jsonb NULL;

COMMENT ON COLUMN public.payment_transactions.intro_offer_group IS
    'Non-NULL ⇒ this row is the FIRST MONTH of عرض الشهر الأول (183), priced for '
    'this profession_group at checkout ⇒ NON-REFUNDABLE (refund_payment refuses, '
    'transaction_summary reports refundable=false). Copied from '
    'users.profession_first_group (via intro_offer_quote) at checkout; deliberately no FK to intro_offer_prices '
    '— a ledger never depends on a config table. Renewal rows never carry it. '
    'Also the evidence is_intro_offer_member() reads (status = ''paid'').';
COMMENT ON COLUMN public.payment_transactions.intro_offer_terms IS
    'The schedule shown at checkout, stamped once: {"first_month_sar", '
    '"then_sar", "then_months", "list_sar"} as 2-dp strings / integer. The '
    'recurring-consent disclosure (v3-intro) is rebuilt from THIS, never from the '
    'live catalog, so the hashed text a customer agreed to can always be '
    'reproduced. NULL on every non-offer row.';

-- ── 4b. users.profession_first_group — the offer follows the FIRST answer ───
-- Owner, 2026-10-21 (after the build): changing the profession later — the
-- sidebar «تغيير المهنة» — must never gain OR lose the offer. Eligibility reads
-- the first REAL answer, written once:
--   * NULL (never asked) or 'declined' → picking a group IS the first answer
--     (so the one re-ask of declined users can still reach the offer);
--   * after that the column is WRITE-ONCE: the trigger restores OLD on any
--     UPDATE, service role included. An operator correcting it must
--     `ALTER TABLE public.users DISABLE TRIGGER trg_users_profession_first_group`
--     around the fix — deliberately awkward.
-- Backfill = today's profession_group for everyone who has a real one. History
-- before this migration was never recorded (the old sidebar tour could change
-- the answer), so the current value is the best "first" there is.
-- ⚠ The backfill UPDATE fires trg_users_updated_at, bumping users.updated_at on
-- those rows. Accepted (nothing keys a sync on it — re-check if that changes).
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS profession_first_group text NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'users_profession_first_group_check'
                      AND conrelid = 'public.users'::regclass) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_profession_first_group_check CHECK (
                profession_first_group IS NULL
                OR profession_first_group IN
                   ('legal', 'entrepreneur', 'specialist', 'individual'));
    END IF;
END $$;

COMMENT ON COLUMN public.users.profession_first_group IS
    'The FIRST real answer to the profession step (never ''declined''), written '
    'once by trg_users_profession_first_group and never changed afterwards. '
    'عرض الشهر الأول (183) prices and gates on THIS, not profession_group, so '
    'changing the profession later can neither unlock nor lose the offer.';

UPDATE public.users
   SET profession_first_group = profession_group
 WHERE profession_first_group IS NULL
   AND profession_group IS NOT NULL
   AND profession_group <> 'declined';

CREATE OR REPLACE FUNCTION public.stamp_profession_first_group()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public'
AS $$
BEGIN
    -- write-once: an existing first answer survives every UPDATE
    IF TG_OP = 'UPDATE' AND OLD.profession_first_group IS NOT NULL THEN
        NEW.profession_first_group := OLD.profession_first_group;
        RETURN NEW;
    END IF;
    IF NEW.profession_first_group IS NULL
       AND NEW.profession_group IS NOT NULL
       AND NEW.profession_group <> 'declined' THEN
        NEW.profession_first_group := NEW.profession_group;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.stamp_profession_first_group() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_users_profession_first_group ON public.users;
CREATE TRIGGER trg_users_profession_first_group
    BEFORE INSERT OR UPDATE OF profession_group, profession_first_group ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.stamp_profession_first_group();

-- ── 5. is_intro_offer_member(user) ──────────────────────────────────────────
-- After §4: a SQL-language body is validated at CREATE, so the column must exist.
CREATE OR REPLACE FUNCTION public.is_intro_offer_member(p_user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.payment_transactions t
         WHERE t.user_id = p_user_id
           AND t.intro_offer_group IS NOT NULL
           AND t.status = 'paid'
    );
$$;

COMMENT ON FUNCTION public.is_intro_offer_member(uuid) IS
    'TRUE once the user has a PAID first month of عرض الشهر الأول (183). Such a '
    'user is exempt from cancellation forfeiture: effective_plan_price branch (a), '
    'claim_early_adopter_seat''s ''forfeited'' return and early_adopter_status''s '
    'v_forfeit all skip them, so after a lapsed cancel they re-buy pro at the '
    'early-adopter price with a fresh 90-day seat while that campaign is open. '
    'An operator-refunded first month (status ''refunded'') is NOT membership. '
    'NULL p_user_id → false. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.is_intro_offer_member(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.is_intro_offer_member(uuid) TO service_role;

-- ── 6. intro_offer_quote(user) — zero rows = not eligible ───────────────────
CREATE OR REPLACE FUNCTION public.intro_offer_quote(p_user_id uuid)
RETURNS TABLE(profession_group text, first_month_sar numeric)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    -- the FIRST answer (§4b), never the current one — «تغيير المهنة» can
    -- neither unlock nor lose the offer
    SELECT u.profession_first_group,
           round(ip.first_month_sar, 2)
      FROM public.users u
      JOIN public.intro_offer_prices   ip ON ip.profession_group = u.profession_first_group
      JOIN public.intro_offer_campaign c  ON c.id
     WHERE u.user_id = p_user_id
       -- the campaign: this switch, before its end, AND early-adopter seats left
       -- (every offer purchase claims one; months 2–3 ARE the seat's price)
       AND c.enabled
       AND now() < c.ends_at
       AND public.early_adopter_open()
       -- not subscribed to anything: the effective plan is free or locked (NULL),
       -- or there is no subscription row at all. The view folds expiry → free;
       -- never re-derive it here.
       AND NOT EXISTS (
            SELECT 1
              FROM public.user_subscriptions_live l
             WHERE l.user_id = p_user_id
               AND l.effective_plan_id IS NOT NULL
               AND l.effective_plan_id <> 'free')
       -- never paid for pro or max (a refund still counts as having bought it);
       -- a basic-only past buyer stays eligible
       AND NOT EXISTS (
            SELECT 1
              FROM public.payment_transactions t
             WHERE t.user_id = p_user_id
               AND t.plan_id IN ('pro', 'max')
               AND t.status IN ('paid', 'refunded'))
       -- implied by the line above (a seat needs a pro/max payment); explicit so
       -- a hand-inserted seat can never re-open the offer
       AND NOT EXISTS (
            SELECT 1
              FROM public.early_adopter_seats s
             WHERE s.user_id = p_user_id
               AND s.release_reason = 'cancelled');
$$;

COMMENT ON FUNCTION public.intro_offer_quote(uuid) IS
    'عرض الشهر الأول (183): one row (profession_group, first_month_sar) when this '
    'user may buy pro''s first month at the offer price RIGHT NOW, else zero rows. '
    'All of: intro_offer_campaign.enabled, now() < ends_at, early_adopter_open(); '
    'users.profession_FIRST_group (the write-once first answer, NOT the current '
    'profession_group) has an intro_offer_prices row; effective plan free / '
    'locked / no subscription; never paid (or refunded) a pro or max payment; no '
    'cancelled seat. create_checkout prices pro with it and stamps '
    'payment_transactions.intro_offer_group; GET /payments/intro-offer reports '
    'eligibility from it. Callers must FAIL CLOSED (error ⇒ not eligible). '
    'Service-role only.';

REVOKE EXECUTE ON FUNCTION public.intro_offer_quote(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.intro_offer_quote(uuid) TO service_role;

-- ── 7. effective_plan_price — forfeiture exemption in branch (a) ────────────
-- 182's body, with ONE change: `AND NOT public.is_intro_offer_member(p_user_id)`.
-- Same signature ⇒ grants survive CREATE OR REPLACE; the COMMENT is refreshed.
CREATE OR REPLACE FUNCTION public.effective_plan_price(
    p_user_id uuid,
    p_plan_id text,
    p_context text DEFAULT 'purchase',
    p_at      timestamptz DEFAULT NULL
)
RETURNS numeric
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_plan RECORD;
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

    IF v_plan.price_sar IS NULL OR v_plan.promo_price_sar IS NULL THEN
        RETURN round(v_plan.price_sar, 2);
    END IF;

    IF v_plan.billing_cycle = 'recurring_30d' THEN
        -- (a) forfeiture — except for intro-offer members (183), who may come
        -- back at the promo price after a lapsed cancellation.
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.release_reason = 'cancelled')
           AND NOT public.is_intro_offer_member(p_user_id) THEN
            RETURN round(v_plan.price_sar, 2);
        END IF;

        -- (b) THE PROMISE — priced at the instant being charged FOR (182).
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
$$;

COMMENT ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz) IS
    'THE price of a plan FOR A GIVEN USER (138, p_at added by 182, forfeiture '
    'exemption by 183). p_context: ''purchase'' (default) = what this plan would '
    'cost if bought now (create_checkout); ''current'' = what this user is already '
    'being charged (renewal job, _upgrade_credit pricing the OLD plan). DO NOT '
    'COLLAPSE THE TWO (see 138). p_at = the instant being priced; NULL = now(). '
    'The renewal job passes the term''s expires_at so a seat holder pays the promo '
    'for exactly the periods that BEGIN inside the 90-day window. Returns '
    'price_sar for a user with a seat released ''cancelled'' (§1 rule 6) UNLESS '
    'is_intro_offer_member(user) (183 — they may come back at the promo). Does '
    'NOT know the intro first-month price: that is intro_offer_quote, applied by '
    'create_checkout. NULL p_user_id = no seat, no forfeiture. Unknown p_context '
    'RAISES 22023. Rounded to 2dp. Service-role only. ⚠ ONE SIGNATURE ONLY — a '
    'second overload makes PostgREST named-arg calls ambiguous (PGRST203).';

-- ── 8. claim_early_adopter_seat — same exemption on 'forfeited' ─────────────
-- LIVE body (2026-10-21), ONE change in the FORFEITURE block. The promo-quote
-- gate already admits the offer's first month: v_gross = 1.99 / 9.90 is not
-- > promo + 0.01, so it claims a seat with promo_ends_at = paid_at + 90d like
-- any 49.90 purchase. A returning member's fresh seat is the same code path.
CREATE OR REPLACE FUNCTION public.claim_early_adopter_seat(p_user_id uuid, p_payment_id uuid)
 RETURNS TABLE(seat_id uuid, promo_ends_at timestamp with time zone, over_capacity boolean, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_pay   RECORD;
    v_plan  RECORD;
    v_camp  RECORD;
    v_seat  RECORD;
    v_live  INTEGER;
    v_over  BOOLEAN := false;
    v_gross NUMERIC;
    v_at    TIMESTAMPTZ;
    v_ends  TIMESTAMPTZ;
    v_id    UUID;
BEGIN
    -- ⚠ FIRST. hashtext() is int4 and the cast to bigint is explicit only so the
    -- overload resolved is never in doubt; the KEY VALUE is unchanged.
    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    SELECT t.user_id, t.plan_id, t.paid_at, t.amount_sar, t.upgrade_credit_sar
      INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id;

    IF NOT FOUND THEN
        RAISE NOTICE 'claim_early_adopter_seat: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'payment_not_found'::text;
        RETURN;
    END IF;

    -- Idempotency key 1: this exact payment. Checked BEFORE the campaign switch
    -- so that a replay arriving after the kill switch was flipped still reports
    -- the truth ('already_claimed') instead of 'campaign_disabled'.
    SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.payment_id = p_payment_id;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    -- user_id is nullable on this table, so THIS is the guard that keeps a ghost
    -- seat — one nobody can hold and nobody can release — from consuming capacity.
    IF p_user_id IS NULL OR (v_pay.user_id IS NOT NULL AND v_pay.user_id <> p_user_id) THEN
        RAISE NOTICE 'claim_early_adopter_seat: user mismatch (arg=%, payment=%)',
                     p_user_id, v_pay.user_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'user_mismatch'::text;
        RETURN;
    END IF;

    -- Idempotency key 2: the user already holds a live seat. §1 rule 8 — an
    -- upgrade carries the window over: no reset, no new window, and the second
    -- payment does NOT become the seat's payment_id.
    SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    -- Seat-bearing plan? (billing_cycle, not a hard-coded id list.) basic buyers
    -- reach here on every purchase and leave here.
    SELECT p.promo_price_sar AS promo, p.billing_cycle AS cycle
      INTO v_plan
      FROM public.plans p
     WHERE p.plan_id = v_pay.plan_id;

    IF NOT FOUND
       OR v_plan.promo IS NULL
       OR v_plan.cycle IS DISTINCT FROM 'recurring_30d' THEN
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'plan_not_eligible'::text;
        RETURN;
    END IF;

    -- FORFEITURE (§1 rule 6) — THE SAME PREDICATE effective_plan_price USES, and
    -- it must stay that way: this user was quoted the LIST price, so they get no
    -- seat, and the two facts agree. Checked after plan eligibility so a `basic`
    -- payment still reads as 'plan_not_eligible'.
    --
    -- An undone cancellation is NOT a forfeiture: restore_early_adopter_seat
    -- clears released_at and release_reason together, so no row survives to
    -- match. A 'refund' release is not a forfeiture either (§1 rule 5).
    --
    -- 183: nor is it one for an intro-offer member (is_intro_offer_member) —
    -- effective_plan_price quoted them the promo, so they get a NEW seat with a
    -- fresh 90-day window, and the two facts still agree.
    IF EXISTS (SELECT 1
                 FROM public.early_adopter_seats s
                WHERE s.user_id = p_user_id
                  AND s.release_reason = 'cancelled')
       AND NOT public.is_intro_offer_member(p_user_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'forfeited'::text;
        RETURN;
    END IF;

    -- ⚠ THE PROMO-QUOTE GATE — THE INVARIANT, AND IT GATES EVERY CLAIM.
    --
    --     A SEAT HOLDER IS PRECISELY SOMEONE WHO WAS CHARGED THE PROMO PRICE.
    --
    -- gross reconstructs the plan price that was on the screen when the quote was
    -- made: create_checkout stores charge = q2(price - credit) in amount_sar and
    -- credit in upgrade_credit_sar, so the sum is the price it quoted — prorated
    -- upgrades included. One cent of tolerance covers the q2 rounding.
    --
    -- WHY IT IS HERE AND NOT INSIDE THE CAPACITY BRANCH (fixed 2026-08-16 — the
    -- campaign has TWO edges, and this used to be checked at only one of them).
    -- A quote created BEFORE the campaign opened is priced at 89.90; if it settles
    -- DURING the open campaign it would otherwise claim a seat, handing someone
    -- who paid full price 90 days of 49.90 renewals they never bought into AND
    -- burning capacity meant for a campaign buyer.
    --
    -- NULL amount_sar fails the gate: the invariant is a statement about what the
    -- customer was CHARGED, and a row that cannot say what it charged cannot
    -- prove it. Fail closed — a malformed row must not mint a seat.
    --
    -- 183: the intro offer's first month (1.99 / 9.90) is BELOW the promo, so it
    -- passes this gate unchanged — "charged the promo price or less".
    v_gross := v_pay.amount_sar + COALESCE(v_pay.upgrade_credit_sar, 0);

    IF v_gross IS NULL OR v_gross > v_plan.promo + 0.01 THEN
        RAISE NOTICE
            'claim_early_adopter_seat: payment % was quoted % (promo is %) — '
            'no seat. This is normal for a pre-campaign quote settling after the '
            'campaign opened.',
            p_payment_id, v_gross, v_plan.promo;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'not_promo_priced'::text;
        RETURN;
    END IF;

    -- ── PAST THE GATE, NOTHING REFUSES A SEAT ────────────────────────────────
    -- ⚠ `is_on`, not `on`: ON is a reserved word and `AS on` is a syntax error.
    SELECT c.seat_limit AS lim, c.promo_days AS days, c.enabled AS is_on
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;

    -- 'campaign_disabled' now means ONE thing: there is no campaign row. That is
    -- a broken install, not a policy decision — and it is the only case left in
    -- which a promo-priced payment goes unseated.
    IF NOT FOUND THEN
        RAISE WARNING
            'claim_early_adopter_seat: NO early_adopter_campaign ROW — payment % '
            'was charged the promo price and cannot be seated. Restore the row '
            '(migration 138 §3) and re-run the claim.',
            p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'campaign_disabled'::text;
        RETURN;
    END IF;

    -- ⚠ enabled = false DOES NOT REFUSE THIS PAYMENT (owner, 2026-08-17). The
    -- decision was made at checkout: this customer was quoted and charged 49.90.
    -- Refusing here would take the money and withhold the 90 days it bought, then
    -- step them up to 89.90 at the next renewal. The switch is still a complete
    -- stop, because it works one layer up: effective_plan_price branch (c) reads
    -- early_adopter_open(), so with the flag down nothing new is ever QUOTED at
    -- the promo price and the supply of claimable payments dries up by itself.
    IF NOT v_camp.is_on THEN
        RAISE NOTICE
            'claim_early_adopter_seat: campaign switch is OFF but payment % was '
            'charged the promo price — seating it anyway (a quote outstanding at '
            'the flip). No new promo quotes can be issued while the flag is down.',
            p_payment_id;
    END IF;

    -- The count, under the lock. Past the limit the seat is granted anyway and
    -- STAMPED — §3.5.
    SELECT count(*) INTO v_live
      FROM public.early_adopter_seats s
     WHERE s.released_at IS NULL;

    IF v_live >= v_camp.lim THEN
        v_over := true;
        RAISE NOTICE
            'claim_early_adopter_seat: OVER CAPACITY — payment % (gross %) '
            'was quoted the promo price; granting seat % of %',
            p_payment_id, v_gross, v_live + 1, v_camp.lim;
    END IF;

    -- ANCHOR ON paid_at, NEVER now() (137's lesson: idempotent BY VALUE, not
    -- merely blocked). The UNIQUE payment_id already stops the second insert;
    -- this makes the window the same instant regardless of WHICH of the two
    -- confirmation paths gets there first.
    v_at   := COALESCE(v_pay.paid_at, now());
    v_ends := v_at + make_interval(days => v_camp.days);

    BEGIN
        INSERT INTO public.early_adopter_seats AS s
            (user_id, payment_id, claimed_at, promo_ends_at, over_capacity)
        VALUES (p_user_id, p_payment_id, v_at, v_ends, v_over)
        RETURNING s.seat_id, s.promo_ends_at, s.over_capacity
             INTO v_id, v_ends, v_over;
    EXCEPTION WHEN unique_violation THEN
        -- Unreachable while every writer takes the lock; kept because a manual
        -- INSERT in the SQL editor does not, and a 23505 escaping to the webhook
        -- would burn a retry over bookkeeping that has already happened.
        SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
          INTO v_seat
          FROM public.early_adopter_seats s
         WHERE s.payment_id = p_payment_id
            OR (s.user_id = p_user_id AND s.released_at IS NULL)
         ORDER BY s.claimed_at DESC
         LIMIT 1;

        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END;

    RETURN QUERY SELECT v_id, v_ends, v_over, 'claimed'::text;
END;
$function$;

-- ── 9. early_adopter_status — the third copy of the predicate ───────────────
-- LIVE body (2026-10-21), ONE change: v_forfeit honours the exemption, so a
-- returning intro member's fresh seat reports has_seat = true — exactly what
-- effective_plan_price charges them.
CREATE OR REPLACE FUNCTION public.early_adopter_status(p_user_id uuid)
 RETURNS TABLE(campaign_open boolean, has_seat boolean, promo_ends_at timestamp with time zone)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_ends    TIMESTAMPTZ;
    v_forfeit BOOLEAN;
BEGIN
    -- early_adopter_seats_one_live makes this at most one row; the ORDER BY is
    -- belt and braces for hand-inserted data.
    SELECT s.promo_ends_at
      INTO v_ends
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL
     ORDER BY s.claimed_at DESC
     LIMIT 1;

    -- The third copy of the forfeiture predicate, and the reason it is here:
    -- has_seat must mean EXACTLY what effective_plan_price charges.
    -- 183: intro-offer members are exempt, as in the other two copies.
    v_forfeit := EXISTS (SELECT 1
                           FROM public.early_adopter_seats s
                          WHERE s.user_id = p_user_id
                            AND s.release_reason = 'cancelled')
                 AND NOT public.is_intro_offer_member(p_user_id);

    RETURN QUERY
        SELECT public.early_adopter_open(),
               (v_ends IS NOT NULL AND v_ends > now() AND NOT v_forfeit),
               v_ends;
END;
$function$;

-- ── 10. product_docs.pricing — the router may know the offer EXISTS ─────────
-- Same rule as 140: the router reads this row before answering anything about
-- ريحان itself, and it must never quote an amount, an end date or a seat count
-- (a model with a number in context eventually says the number), nor confirm
-- whether THIS user is eligible (it cannot see the server's answer). The
-- section is written with NO digit at all, so the 1b check below is absolute.
-- It deliberately avoids the phrase «المشتركون الأوائل» so 140's own n_marker
-- post-check keeps reading 2.
--
-- ⚠ THE ROW IS THE ONLY COPY (126 has no seeder). Before applying, save it:
--     SELECT content_md FROM public.product_docs WHERE doc_key = 'pricing';
-- The UPDATE only appends.
--
-- ⚠ md5 ASSERTION: this file reaches prod by transcription through the MCP
--   boundary; a mangled Arabic word passes every structural check. The section
--   is hashed BEFORE the UPDATE and the migration aborts on a mismatch.
DO $$
DECLARE
    v_n      INTEGER;
    v_marker TEXT := '## عرض الشهر الأول';
    v_md5    TEXT := 'f877f30b4a088ccaffb119ccfdc64920';
    v_section TEXT :=
        '## عرض الشهر الأول'
        || E'\n\n'
        || 'قد يظهر داخل ريحان، لبعض المستخدمين بحسب المهنة التي اختاروها في '
        || 'حسابهم، عرضٌ ترويجي على الشهر الأول من باقة «الاحترافية»، يليه سعر '
        || 'ترويجي لفترة محدودة ثم السعر المعتاد، مع التجديد التلقائي كأي اشتراك '
        || 'في هذه الباقة. يظهر العرض تلقائياً داخل التطبيق لمن يستحقه فقط ولا '
        || 'يحتاج إلى رمز، والشهر الأول فيه غير قابل للاسترداد. عند أي سؤال عنه: '
        || 'لا تؤكّد أنّ المستخدم مؤهّل له أو غير مؤهّل، ولا أنّ العرض ساري الآن أو '
        || 'انتهى، ولا تذكر أي مبلغ ولا تاريخ انتهاء ولا عدد مقاعد، وأحِل المستخدم '
        || 'إلى شروط العروض الترويجية https://rayhanai.com/promo-terms — وإن كان '
        || 'مؤهّلاً فسيظهر له العرض داخل التطبيق.'
        || E'\n';
BEGIN
    IF md5(v_section) <> v_md5 THEN
        RAISE EXCEPTION
            '183: product_docs section md5 is %, expected % — the Arabic text was '
            'altered in transit. Nothing has been written.',
            md5(v_section), v_md5;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.product_docs d
                    WHERE d.doc_key = 'pricing' AND d.catalog = 'about') THEN
        RAISE WARNING
            '183: no product_docs row doc_key=''pricing'' AND catalog=''about'' — '
            'NOTHING APPENDED. Paste the section by hand.';
        RETURN;
    END IF;

    UPDATE public.product_docs d
       SET content_md = rtrim(d.content_md, E' \t\r\n') || E'\n\n' || v_section
     WHERE d.doc_key = 'pricing'
       AND d.catalog = 'about'
       AND position(v_marker IN d.content_md) = 0;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RAISE NOTICE '183: pricing doc already has %, nothing appended (re-run).', v_marker;
    ELSE
        RAISE NOTICE '183: appended عرض الشهر الأول to product_docs.pricing (live within the 600s cache TTL).';
    END IF;
END $$;

-- ── 11. verification — any failure aborts the whole migration ───────────────
DO $$
DECLARE
    v_t      TEXT;
    v_n      INTEGER;
    v_row    RECORD;
    v_fn     TEXT;
BEGIN
    -- tables: exist, RLS on, zero client grants
    FOREACH v_t IN ARRAY ARRAY['intro_offer_campaign', 'intro_offer_prices'] LOOP
        IF to_regclass('public.' || v_t) IS NULL THEN
            RAISE EXCEPTION '183: table % missing', v_t;
        END IF;
        IF NOT (SELECT c.relrowsecurity FROM pg_class c
                 WHERE c.oid = to_regclass('public.' || v_t)) THEN
            RAISE EXCEPTION '183: RLS is OFF on %', v_t;
        END IF;
        -- has_table_privilege, not information_schema.role_table_grants: the
        -- latter only lists grants visible to the CURRENT role and can read
        -- empty — a vacuous pass — under the migration runner.
        IF has_table_privilege('anon', 'public.' || v_t, 'SELECT, INSERT, UPDATE, DELETE')
           OR has_table_privilege('authenticated', 'public.' || v_t,
                                  'SELECT, INSERT, UPDATE, DELETE') THEN
            RAISE EXCEPTION '183: % is readable/writable by anon or authenticated', v_t;
        END IF;
        SELECT count(*) INTO v_n FROM pg_policies p
         WHERE p.schemaname = 'public' AND p.tablename = v_t;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '183: % has % RLS polic(ies); expected none (deny-all)', v_t, v_n;
        END IF;
    END LOOP;

    -- seeds
    SELECT count(*) INTO v_n FROM public.intro_offer_prices
     WHERE (profession_group, first_month_sar) IN
           (('legal', 1.99), ('specialist', 9.90), ('entrepreneur', 9.90));
    IF v_n <> 3 THEN
        RAISE EXCEPTION '183: expected the 3 seeded intro_offer_prices rows, found %', v_n;
    END IF;

    SELECT * INTO v_row FROM public.intro_offer_campaign WHERE id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '183: intro_offer_campaign row missing';
    END IF;
    -- The seed is (enabled=false) and ON CONFLICT DO NOTHING never flips an
    -- existing row, so a first apply always ships disabled and a re-run after
    -- launch leaves the operator's switch alone. Report, don't judge.
    RAISE NOTICE '183: intro_offer_campaign enabled=% ends_at=%', v_row.enabled, v_row.ends_at;

    -- columns
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'payment_transactions'
       AND column_name IN ('intro_offer_group', 'intro_offer_terms');
    IF v_n <> 2 THEN
        RAISE EXCEPTION '183: payment_transactions offer columns missing (found % of 2)', v_n;
    END IF;

    -- first-answer column: present, backfilled, trigger installed
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'users'
                      AND column_name = 'profession_first_group') THEN
        RAISE EXCEPTION '183: users.profession_first_group missing';
    END IF;
    SELECT count(*) INTO v_n FROM public.users u
     WHERE u.profession_group IS NOT NULL
       AND u.profession_group <> 'declined'
       AND u.profession_first_group IS NULL;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '183: % users have a real profession but no profession_first_group', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
                    WHERE t.tgrelid = 'public.users'::regclass
                      AND t.tgname = 'trg_users_profession_first_group'
                      AND NOT t.tgisinternal) THEN
        RAISE EXCEPTION '183: trg_users_profession_first_group missing';
    END IF;

    -- function grants: service_role only
    FOREACH v_fn IN ARRAY ARRAY[
        'public.intro_offer_quote(uuid)',
        'public.is_intro_offer_member(uuid)',
        'public.effective_plan_price(uuid, text, text, timestamptz)',
        'public.claim_early_adopter_seat(uuid, uuid)',
        'public.early_adopter_status(uuid)'
    ] LOOP
        IF has_function_privilege('anon', v_fn, 'EXECUTE')
           OR has_function_privilege('authenticated', v_fn, 'EXECUTE') THEN
            RAISE EXCEPTION '183: anon/authenticated can EXECUTE %', v_fn;
        END IF;
        IF NOT has_function_privilege('service_role', v_fn, 'EXECUTE') THEN
            RAISE EXCEPTION '183: service_role cannot EXECUTE %', v_fn;
        END IF;
    END LOOP;

    -- still exactly one effective_plan_price
    SELECT count(*) INTO v_n
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
    IF v_n <> 1 THEN
        RAISE EXCEPTION '183: % effective_plan_price overloads (expected 1)', v_n;
    END IF;

    -- inert: with the switch down nobody is quoted. ONE user, not all of them:
    -- each call evaluates user_subscriptions_live (get_user_quota_state) and
    -- this transaction is holding the payment_transactions lock.
    IF NOT v_row.enabled THEN
        SELECT count(*) INTO v_n
          FROM (SELECT u.user_id FROM public.users u
                 WHERE u.profession_group = 'legal' LIMIT 1) x,
               LATERAL public.intro_offer_quote(x.user_id) q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '183: switch is OFF but intro_offer_quote returned % row(s)', v_n;
        END IF;
    END IF;

    RAISE NOTICE '183: OK — tables, seeds, columns, grants, single effective_plan_price, inert';
END $$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY CHECKS — run manually
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. Who WOULD be eligible if the switch were on (dry run — reimplements the
-- --    switch-independent conditions; EXPECT roughly the free users in the
-- --    three groups minus prior pro/max payers, ≈ 45–50 on 2026-10-21).
-- SELECT u.profession_first_group, count(*)
--   FROM public.users u
--   JOIN public.intro_offer_prices ip ON ip.profession_group = u.profession_first_group
--  WHERE NOT EXISTS (SELECT 1 FROM public.user_subscriptions_live l
--                     WHERE l.user_id = u.user_id
--                       AND l.effective_plan_id IS NOT NULL
--                       AND l.effective_plan_id <> 'free')
--    AND NOT EXISTS (SELECT 1 FROM public.payment_transactions t
--                     WHERE t.user_id = u.user_id AND t.plan_id IN ('pro','max')
--                       AND t.status IN ('paid','refunded'))
--  GROUP BY 1;
--
-- -- 1b. NO-NUMBERS check on the appended doc section. EXPECT all false.
-- WITH tail AS (
--     SELECT substr(content_md, position('## عرض الشهر الأول' IN content_md)) AS t
--       FROM public.product_docs WHERE doc_key = 'pricing'
-- )
-- SELECT t ~ '[0-9٠-٩]' AS has_any_digit, t ILIKE '%ريال%' AS mentions_riyals,
--        t ILIKE '%مقعد%'  AS mentions_seat_count
--   FROM tail;
--
-- -- 2. 140's marker count is untouched. EXPECT n_marker = 2.
-- SELECT (length(content_md) - length(replace(content_md, 'المشتركون الأوائل', '')))
--        / length('المشتركون الأوائل') AS n_marker
--   FROM public.product_docs WHERE doc_key = 'pricing';
--
-- -- 3. LAUNCH (after the backend + frontend are live and a test purchase passed):
-- -- UPDATE public.intro_offer_campaign SET enabled = true, updated_at = now() WHERE id;
-- -- OFF SWITCH:
-- -- UPDATE public.intro_offer_campaign SET enabled = false, updated_at = now() WHERE id;
--
-- ── ROLLBACK ─────────────────────────────────────────────────────────────────
-- -- Backend first (it selects the new columns), then:
-- --   re-run 182 §2 (effective_plan_price without the exemption) and the 138 /
-- --   live bodies of claim_early_adopter_seat + early_adopter_status;
-- --   DROP FUNCTION public.intro_offer_quote(uuid);
-- --   DROP FUNCTION public.is_intro_offer_member(uuid);
-- --   DROP TABLE public.intro_offer_prices; DROP TABLE public.intro_offer_campaign;
-- --   (keep the payment_transactions columns — they may hold settled history);
-- --   strip the doc section: content_md = rtrim(left(content_md,
-- --     position('## عرض الشهر الأول' IN content_md) - 1), E' \t\r\n').
