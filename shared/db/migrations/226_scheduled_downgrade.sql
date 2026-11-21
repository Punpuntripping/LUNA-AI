-- ════════════════════════════════════════════════════════════════════════════
-- 226 — scheduled downgrade: user_subscriptions.renewal_plan_id, and the
--       'downgraded' seat release
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/pro_plus_tier_and_downgrade.md §1 (D6–D8, D12), §4
--       (Phase C), §8 (build contract items 3, 4, 6, 7). Owner 2026-11-20.
-- Depends on: 192 (seats, released_by_payment_id, V5 give-back), 225 (the
--             pro_plus plan; promo_quote / promo_checkout_price p_as_new).
-- Function bodies are taken from pg_get_functiondef LIVE on 2026-11-20; §0
-- refuses to run if either changed in prod since (md5 of prosrc).
--
-- ── WHAT CHANGES ────────────────────────────────────────────────────────────
--   user_subscriptions  + renewal_plan_id text NULL → plans(plan_id)
--                       + renewal_plan_set_at timestamptz NULL      (§8-6)
--                       + THE PROMISE (owner 2026-11-20, coordinator follow-up):
--                         renewal_plan_price_sar numeric(10,2) NULL — the first
--                           charge promised when the downgrade was scheduled;
--                         renewal_plan_batch_id text NULL → promo_batches —
--                           the offer batch promised (NULL = list price);
--                         renewal_plan_promo_months integer NULL — its months.
--                         CHECK user_subscriptions_renewal_plan_terms_check:
--                         renewal_plan_id NULL ⇒ all three NULL; NOT NULL ⇒
--                         price NOT NULL and > 0, and months NOT NULL ⇔ batch
--                         NOT NULL (months 1–12, as promo_batches allows).
--                         The renewal charges that promise, or the renewal-day
--                         fresh price if lower, even if the plan is off sale or
--                         the batch is full / disabled by then (backend).
--                       No grant, no policy, no view change: the table keeps
--                       its one self-row SELECT policy for authenticated (the
--                       table-level grants already cover new columns, writes
--                       stay RLS-blocked for clients) and the backend writes
--                       with the service role. user_subscriptions_live is NOT
--                       rebuilt (§8-6): the backend reads the table.
--   early_adopter_seats release_reason CHECK + 'downgraded' (§8-4);
--                       released_by_check: released_by_payment_id is allowed
--                       for 'upgraded' AND 'downgraded' (the claim stamps the
--                       superseding payment on both, see below).
--   claim_early_adopter_seat  a superseded live seat on a plan with a HIGHER
--                       list price than the new payment's plan is released
--                       'downgraded' instead of 'upgraded' (§8-3). Both keep
--                       released_by_payment_id = the new payment.
--   release_early_adopter_seat accepts 'downgraded' (§8-4); the V5 give-back on
--                       a refund now also returns a 'downgraded' seat.
--                       Review item 3: + trailing p_payment_id uuid DEFAULT
--                       NULL (DROP + CREATE, same ACL): with 'downgraded' it
--                       stamps released_by_payment_id, and a 'refund' that
--                       releases no live seat gives back the seat a now-refunded
--                       payment superseded — the list-priced downgrade path.
--   claim_early_adopter_seat (review item 1): a RENEWAL payment's seat window
--                       is anchored on period_start, not paid_at (a late grace
--                       charge must not reach the next period). Checkout: paid_at.
--   payment_transactions (review item 2): + UNIQUE uniq_payment_renewal_period_any_plan
--                       (user_id, period_start) WHERE renewal AND status <>
--                       'failed' — one charge per period across plans. LAST.
--   effective_plan_price (coordinator, owner principle "the promised price is
--                       honoured"): branch (b1) — a live batch seat for this
--                       plan covering the priced instant returns its frozen
--                       price — no longer requires plans.price_sar. Nothing else
--                       changes: after the window an unpriced plan is still
--                       NULL (off-sale plans do not renew). §0c re-prices real
--                       users before / after and refuses any difference.
--
-- ── WHERE THIS FILE HAD TO INTERPRET THE CONTRACT ──────────────────────────
-- (S1) 'downgraded' carries released_by_payment_id when the CLAIM writes it, so
--      refunding the downgrade payment gives the higher seat back (V5), exactly
--      as refunding an upgrade does. When release_early_adopter_seat writes it
--      (a list-priced downgrade, no claim), it carries the payment passed as
--      p_payment_id (review item 3) — NULL only when the caller passes none —
--      and a refund of that payment gives the seat back too.
-- (S2) release_early_adopter_seat(user, 'downgraded') leaves alone a live BATCH
--      seat on the plan the user is on NOW (user_subscriptions.plan_id) and
--      returns FALSE + NOTICE; call it AFTER grant_plan(new plan). After the
--      grant that seat is on the TARGET plan: either the new term's own seat
--      (a claim already ran — releasing it would throw away the offer just
--      bought) or a seat that still prices the target (225 P1). The backend's
--      release_seat_for_downgrade applies the same rule in Python; this makes
--      the RPC safe whatever the call order. Legacy seats and batch seats of
--      any other plan are released as asked.
-- (S3) Rank = plans.price_sar, as in 225. When either plan has NO list price
--      (off sale) the claim ranks by points_weekly instead (pro 100 < pro_plus
--      250 < max 500): a promised downgrade may now be charged after its target
--      went off sale (owner 2026-11-20), and must still release 'downgraded'.
--      Same plan, or nothing to compare → 'upgraded' (today's rule).
-- (S4) THE PROMISE IS HONOURED BY THE CLAIM AS IT STANDS. The v2 claim reads
--      the stamped batch row only for its plan / months / limit fallback and
--      NEVER reads promo_batches.enabled; a full batch is seated anyway with
--      over_capacity (§1.4-5, "the quote is the promise"); the seat price and
--      window come from the payment's intro_offer_terms (V1), not the batch
--      row. So a renewal payment stamped with a batch that is now disabled,
--      full or repriced is seated on its promised terms with no code change —
--      §5 D8/D9 prove it. Checkout is not widened: promo_quote still offers
--      only enabled batches with free seats (225 unchanged). grant_plan reads
--      only duration_days, so it grants a plan with no list price (§5 D8/D10).
--
-- ── LOCKS ───────────────────────────────────────────────────────────────────
-- ALTER TABLE takes ACCESS EXCLUSIVE on early_adopter_seats (small) and on
-- user_subscriptions (read by every quota check). user_subscriptions is altered
-- near the END so it is held only for the final self-tests, well under a
-- second; the payment_transactions index (SHARE: blocks payment writes, not
-- reads) is built last of all.
-- lock_timeout fails fast instead of queueing every request behind us.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS / DROP + ADD CONSTRAINT / CREATE OR
-- REPLACE; §0 accepts bodies already carrying this file's "(226)" marker.
--
-- ROLLBACK (backend rolled back first; only while no row uses 'downgraded'):
--   ALTER TABLE public.user_subscriptions
--     DROP CONSTRAINT user_subscriptions_renewal_plan_terms_check,
--     DROP COLUMN renewal_plan_id, DROP COLUMN renewal_plan_set_at,
--     DROP COLUMN renewal_plan_price_sar, DROP COLUMN renewal_plan_batch_id,
--     DROP COLUMN renewal_plan_promo_months;
--   DROP INDEX public.uniq_payment_renewal_period_any_plan;
--   DROP FUNCTION public.release_early_adopter_seat(uuid, text, uuid), then
--   re-create release_early_adopter_seat(uuid, text) / claim_early_adopter_seat /
--   effective_plan_price from the 2026-11-20 pg_get_functiondef (md5 in §0),
--   with the same REVOKE / GRANT;
--   restore both CHECKs to their §0 "old" definitions.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ════════════════════════════════════════════════════════════════════════════
-- 0. Guards
-- ════════════════════════════════════════════════════════════════════════════
DO $guard$
DECLARE
    r     RECORD;
    v_oid OID;
    v_src TEXT;
    v_acl TEXT;
    v_def TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.plans p WHERE p.plan_id = 'pro_plus')
       OR to_regprocedure('public.promo_quote(uuid,text,boolean)') IS NULL THEN
        RAISE EXCEPTION '226: apply 225 first (plans.pro_plus / promo_quote(uuid,text,boolean) missing)';
    END IF;

    FOR r IN
        SELECT *
          FROM (VALUES
                ('public.claim_early_adopter_seat(uuid,uuid)',  NULL::text,
                 '58de370d259f1833dbdaae98925da07b'),
                ('public.release_early_adopter_seat(uuid,text)', 'public.release_early_adopter_seat(uuid,text,uuid)',
                 '11c9fecceb34989c12b3c8505f6d88c1'),
                ('public.effective_plan_price(uuid,text,text,timestamptz)', NULL::text,
                 '07a7fa4b84a023236537fa9859b9cd55')
               ) v(sig, new_sig, old_md5)
    LOOP
        v_oid := to_regprocedure(r.sig);
        IF v_oid IS NULL THEN
            v_oid := to_regprocedure(r.new_sig);         -- re-run: already the new signature
        ELSIF r.new_sig IS NOT NULL AND to_regprocedure(r.new_sig) IS NOT NULL THEN
            RAISE EXCEPTION '226: both % and % exist — a half-applied state; look before applying',
                r.sig, r.new_sig;
        END IF;
        IF v_oid IS NULL THEN
            RAISE EXCEPTION '226: % is missing', r.sig;
        END IF;
        SELECT p.prosrc, p.proacl::text INTO v_src, v_acl
          FROM pg_proc p WHERE p.oid = v_oid;
        IF md5(v_src) IS DISTINCT FROM r.old_md5 AND position('(226)' IN v_src) = 0 THEN
            RAISE EXCEPTION '226: % changed in prod since this file was written (md5 % <> %). '
                            'Rebuild its new body from pg_get_functiondef before applying.',
                v_oid::regprocedure, md5(v_src), r.old_md5;
        END IF;
        -- the GRANTs below reproduce exactly this ACL
        IF v_acl IS DISTINCT FROM '{postgres=X/postgres,service_role=X/postgres}' THEN
            RAISE EXCEPTION '226: % has ACL %, expected {postgres=X/postgres,service_role=X/postgres}',
                v_oid::regprocedure, v_acl;
        END IF;
    END LOOP;

    -- the two CHECKs are 192's (or already this file's): nothing else gets dropped
    SELECT pg_get_constraintdef(c.oid) INTO v_def
      FROM pg_constraint c
     WHERE c.conrelid = 'public.early_adopter_seats'::regclass
       AND c.conname = 'early_adopter_seats_release_reason_check';
    IF v_def IS DISTINCT FROM 'CHECK (((release_reason IS NULL) OR (release_reason = ANY (ARRAY[''refund''::text, ''cancelled''::text, ''upgraded''::text]))))'
       AND position('''downgraded''' IN COALESCE(v_def, '')) = 0 THEN
        RAISE EXCEPTION '226: early_adopter_seats_release_reason_check is "%", not 192''s', v_def;
    END IF;
    SELECT pg_get_constraintdef(c.oid) INTO v_def
      FROM pg_constraint c
     WHERE c.conrelid = 'public.early_adopter_seats'::regclass
       AND c.conname = 'early_adopter_seats_released_by_check';
    IF v_def IS DISTINCT FROM 'CHECK (((released_by_payment_id IS NULL) OR (release_reason = ''upgraded''::text)))'
       AND position('''downgraded''' IN COALESCE(v_def, '')) = 0 THEN
        RAISE EXCEPTION '226: early_adopter_seats_released_by_check is "%", not 192''s', v_def;
    END IF;
END
$guard$;

-- snapshot of everything on user_subscriptions that must NOT change
DROP TABLE IF EXISTS pg_temp._m226_us0;
CREATE TEMP TABLE _m226_us0 ON COMMIT DROP AS
SELECT c.relacl::text      AS acl,
       c.relrowsecurity    AS rls,
       c.relforcerowsecurity AS force_rls,
       (SELECT string_agg(format('%s|%s|%s|%s|%s', p.polname, p.polcmd,
                                 pg_get_expr(p.polqual, p.polrelid),
                                 pg_get_expr(p.polwithcheck, p.polrelid),
                                 p.polroles::regrole[]::text), ';' ORDER BY p.polname)
          FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,
       md5(pg_get_viewdef('public.user_subscriptions_live'::regclass)) AS view_md5,
       (SELECT count(*) FROM information_schema.columns ic
         WHERE ic.table_schema = 'public' AND ic.table_name = 'user_subscriptions_live') AS view_cols
  FROM pg_class c
 WHERE c.oid = 'public.user_subscriptions'::regclass;

-- effective_plan_price before §0c, for the real-user dark check: every payer,
-- seat holder, paid / trial / dev plan, trial claimer, Basic-offer seat, plus
-- the 50 newest accounts; four plans; purchase now, current now, current at a
-- renewal 30 days out.
DROP TABLE IF EXISTS pg_temp._m226_sample, pg_temp._m226_epp0;
CREATE TEMP TABLE _m226_sample ON COMMIT DROP AS
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

CREATE TEMP TABLE _m226_epp0 ON COMMIT DROP AS
SELECT s.user_id, v.plan_id, c.ctx, c.at_offset,
       public.effective_plan_price(s.user_id, v.plan_id, c.ctx,
                                   CASE WHEN c.at_offset IS NULL THEN NULL
                                        ELSE now() + c.at_offset END) AS price
  FROM _m226_sample s
 CROSS JOIN (VALUES ('basic'), ('pro'), ('pro_plus'), ('max')) v(plan_id)
 CROSS JOIN (VALUES ('purchase', NULL::interval),
                    ('current',  NULL::interval),
                    ('current',  interval '30 days')) c(ctx, at_offset);


-- ════════════════════════════════════════════════════════════════════════════
-- 0c. effective_plan_price — a promised seat outlives the shelf
-- ════════════════════════════════════════════════════════════════════════════
-- Owner principle 2026-11-20, "the promised price is honoured": month 2 of a
-- promised offer is charged at the seat price even if the plan went off sale
-- (price_sar NULL) since. The ONE change: branch (b1) no longer requires a list
-- price. Everything else is the live body: after the seat window a plan with
-- no list price still returns NULL (an off-sale plan does not renew — existing
-- policy), and for every plan WITH a list price nothing can differ (§0c's dark
-- check proves it on real users). Done before §1 so the check runs before the
-- seat table is locked.
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
    -- (226) and list-price-independent: a seat promised while the plan was on
    -- sale keeps its price to the end of its window even if the plan has gone
    -- off sale since (price_sar NULL). After the window, the branches below
    -- answer exactly as before (NULL for a plan with no list price).
    IF v_plan.billing_cycle = 'recurring_30d' THEN
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

REVOKE ALL ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz) TO service_role;

COMMENT ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz) IS
'THE price of a plan FOR A GIVEN USER (138; p_at 182; intro exemption 183; tiers 192; 226). p_context ''purchase'' = what it costs if bought now (create_checkout); ''current'' = what this user''s running term costs (renewal job, _upgrade_credit). DO NOT COLLAPSE THE TWO. p_at = the instant priced (renewals pass the new period''s start); NULL = now(). IN EVERY MODE: a live batch seat for THIS plan inside its window returns its frozen price_sar — (226) even if the plan has no list price any more (a promise outlives the shelf). ladder_v2 ON: a plan with no list price is NULL otherwise (an off-sale plan does not renew); no forfeiture; ''purchase'' returns promo_quote''s price when it has a row; then a live legacy seat inside its window returns promo_price_sar (any plan, as today); else list. ladder_v2 OFF: 183''s rules (forfeiture on a ''cancelled'' row unless intro member or a live batch seat is held; any other live seat in its window — legacy, or a batch seat of the other plan — ⇒ promo_price_sar; ''purchase'' while early_adopter_open() ⇒ promo). NULL p_user_id = no seat. Unknown p_context RAISES 22023. Rounded to 2dp. Service-role only. ⚠ ONE SIGNATURE ONLY — a second overload makes PostgREST named-arg calls ambiguous (PGRST203) and the backend charges the catalog.';

DO $epp$
DECLARE
    v_n   BIGINT;
    v_tot BIGINT;
    v_s   TEXT;
BEGIN
    SELECT format('%s ⟂ %s ⟂ %s ⟂ %s ⟂ %s ⟂ %s',
                  pg_get_function_arguments(p.oid), pg_get_function_result(p.oid),
                  p.provolatile, p.prosecdef, p.proconfig::text, p.proacl::text),
           (SELECT count(*) FROM pg_proc x JOIN pg_namespace n ON n.oid = x.pronamespace
             WHERE n.nspname = 'public' AND x.proname = 'effective_plan_price')
      INTO v_s, v_n
      FROM pg_proc p
     WHERE p.oid = 'public.effective_plan_price(uuid,text,text,timestamptz)'::regprocedure;
    IF v_n <> 1
       OR v_s IS DISTINCT FROM 'p_user_id uuid, p_plan_id text, p_context text DEFAULT ''purchase''::text, p_at timestamp with time zone DEFAULT NULL::timestamp with time zone ⟂ numeric ⟂ s ⟂ t ⟂ {search_path=public} ⟂ {postgres=X/postgres,service_role=X/postgres}' THEN
        RAISE EXCEPTION '226 epp: % overload(s), "%"', v_n, v_s;
    END IF;

    -- dark check: every sampled real user, four plans, three (context, instant) pairs
    SELECT count(*) FILTER (WHERE b.price IS DISTINCT FROM
                                  public.effective_plan_price(b.user_id, b.plan_id, b.ctx,
                                      CASE WHEN b.at_offset IS NULL THEN NULL ELSE now() + b.at_offset END)),
           count(*)
      INTO v_n, v_tot
      FROM _m226_epp0 b;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '226 epp dark: % of % (user, plan, context, instant) prices changed', v_n, v_tot;
    END IF;

    RAISE NOTICE '226 epp: OK — one signature, ACL {postgres,service_role}, STABLE SECURITY DEFINER; dark: % prices identical before/after across % real users × basic/pro/pro_plus/max × purchase-now / current-now / current+30d',
        v_tot, (SELECT count(*) FROM _m226_sample);
END
$epp$;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. early_adopter_seats — 'downgraded' (§8-4)
-- ════════════════════════════════════════════════════════════════════════════
-- 'downgraded' counts as using the tier (D12): promo_tier_used excludes only an
-- early 'upgraded' and a refunded purchase. It never forfeits (the forfeiture
-- predicate and restore_early_adopter_seat read only 'cancelled').
ALTER TABLE public.early_adopter_seats
    DROP CONSTRAINT IF EXISTS early_adopter_seats_release_reason_check,
    ADD  CONSTRAINT early_adopter_seats_release_reason_check
         CHECK (release_reason IS NULL
                OR release_reason IN ('refund', 'cancelled', 'upgraded', 'downgraded')),
    DROP CONSTRAINT IF EXISTS early_adopter_seats_released_by_check,
    ADD  CONSTRAINT early_adopter_seats_released_by_check
         CHECK (released_by_payment_id IS NULL
                OR release_reason IN ('upgraded', 'downgraded'));


-- ════════════════════════════════════════════════════════════════════════════
-- 2. release_early_adopter_seat — 'downgraded', + p_payment_id (review item 3)
-- ════════════════════════════════════════════════════════════════════════════
-- A new trailing argument is a new signature: DROP + CREATE (CREATE OR REPLACE
-- would add an overload — PGRST203 on every named-arg call). The backend's
-- 2-arg named calls (p_user_id, p_reason) resolve through the DEFAULT. No SQL
-- function calls it and nothing in pg_depend pins it (checked 2026-11-20).
DROP FUNCTION IF EXISTS public.release_early_adopter_seat(uuid, text);

CREATE OR REPLACE FUNCTION public.release_early_adopter_seat(p_user_id uuid, p_reason text, p_payment_id uuid DEFAULT NULL)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_seat  RECORD;
    v_prev  RECORD;
    v_live  INTEGER;
    v_limit INTEGER;
    v_n     INTEGER;
    v_by    UUID;                                        -- (226)
    v_found BOOLEAN := false;                            -- (226)
BEGIN
    IF p_user_id IS NULL THEN
        RETURN false;
    END IF;

    -- The CHECK constraint would raise; the refund path must not 500 over a
    -- typo'd literal. 'upgraded' is written only by the claim, never here.
    -- (226) 'downgraded': a scheduled downgrade took effect at renewal with no
    -- seat claimed for the new term (list price) — the seat of the plan being
    -- LEFT ends. (A promo-priced downgrade is released by the claim instead.)
    IF p_reason IS NULL OR p_reason NOT IN ('refund', 'cancelled', 'downgraded') THEN
        RAISE NOTICE 'release_early_adopter_seat: refusing unknown reason %', p_reason;
        RETURN false;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    SELECT s.seat_id AS id, s.payment_id AS pay,
           s.batch_id AS batch, s.plan_id AS plan                    -- (226)
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL
     ORDER BY s.claimed_at DESC
     LIMIT 1;

    -- (226) 'downgraded' is for the plan being LEFT; call it after
    -- grant_plan(new plan). A live BATCH seat on the plan the user is on NOW is
    -- on the target plan: the new term's own seat (a claim already released
    -- the old one) or one that still prices the target (225 P1). Never here.
    IF p_reason = 'downgraded'
       AND v_seat.batch IS NOT NULL
       AND v_seat.plan = (SELECT us.plan_id
                            FROM public.user_subscriptions us
                           WHERE us.user_id = p_user_id) THEN
        RAISE NOTICE 'release_early_adopter_seat: user % holds a live % seat and is on % now — '
                     'the new term''s own seat, not released as ''downgraded''',
                     p_user_id, v_seat.plan, v_seat.plan;
        RETURN false;
    END IF;

    -- (226) p_payment_id: the renewal payment that moved the user off the seat's
    -- plan (a LIST-priced downgrade — nothing was claimed, so no claim recorded
    -- it). Stamped as released_by_payment_id so refunding that payment gives
    -- the seat back (V5 below), exactly as for a promo-priced switch. Only for
    -- 'downgraded' and only a payment of this user; otherwise ignored (the
    -- released_by CHECK allows a payment id on 'upgraded' / 'downgraded' only).
    IF p_payment_id IS NOT NULL THEN
        IF p_reason = 'downgraded'
           AND EXISTS (SELECT 1
                         FROM public.payment_transactions t
                        WHERE t.payment_id = p_payment_id
                          AND t.user_id = p_user_id) THEN
            v_by := p_payment_id;
        ELSE
            RAISE NOTICE 'release_early_adopter_seat: p_payment_id % ignored (reason %, or not a payment of user %)',
                         p_payment_id, p_reason, p_user_id;
        END IF;
    END IF;

    UPDATE public.early_adopter_seats s
       SET released_at            = now(),
           release_reason         = p_reason,
           released_by_payment_id = v_by                    -- (226) NULL unless stamped above
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 AND p_reason <> 'refund' THEN
        RETURN false;
    END IF;

    -- (V5) the refunded payment claimed this seat through a switch → give the
    -- superseded seat back (the one-live index is free: we just released).
    -- (226) a switch either way: 'upgraded' or 'downgraded' by that payment.
    -- (226) and when the refund released NO live seat — the refunded payment
    -- claimed none: a list-priced downgrade, stamped through p_payment_id —
    -- the seat that a now-refunded payment superseded comes back. No live seat
    -- exists then (one-live index), so the give-back cannot collide.
    IF p_reason = 'refund' THEN
        BEGIN
            IF v_n > 0 THEN
                IF v_seat.pay IS NOT NULL
                   AND EXISTS (SELECT 1
                                 FROM public.payment_transactions t
                                WHERE t.payment_id = v_seat.pay
                                  AND t.status = 'refunded') THEN
                    SELECT s.seat_id AS id, s.batch_id AS batch, s.released_by_payment_id AS payby
                      INTO v_prev
                      FROM public.early_adopter_seats s
                     WHERE s.user_id = p_user_id
                       AND s.release_reason IN ('upgraded', 'downgraded')
                       AND s.released_by_payment_id = v_seat.pay
                       AND s.promo_ends_at > now()
                     ORDER BY s.released_at DESC
                     LIMIT 1;
                    v_found := FOUND;
                END IF;
            ELSE
                SELECT s.seat_id AS id, s.batch_id AS batch, s.released_by_payment_id AS payby
                  INTO v_prev
                  FROM public.early_adopter_seats s
                  JOIN public.payment_transactions t ON t.payment_id = s.released_by_payment_id
                 WHERE s.user_id = p_user_id
                   AND s.release_reason IN ('upgraded', 'downgraded')
                   AND t.status = 'refunded'
                   AND s.promo_ends_at > now()
                 ORDER BY s.released_at DESC
                 LIMIT 1;
                v_found := FOUND;
            END IF;

            IF v_found THEN
                -- capacity is our problem, not theirs: restore anyway and stamp
                -- over_capacity (sticky) against the seat's own pool, as
                -- restore_early_adopter_seat does
                IF v_prev.batch IS NOT NULL THEN
                    SELECT count(*) INTO v_live
                      FROM public.early_adopter_seats s
                     WHERE s.released_at IS NULL AND s.batch_id = v_prev.batch;
                    SELECT b.seat_limit INTO v_limit
                      FROM public.promo_batches b WHERE b.batch_id = v_prev.batch;
                ELSE
                    SELECT count(*) INTO v_live
                      FROM public.early_adopter_seats s
                     WHERE s.released_at IS NULL;
                    SELECT c.seat_limit INTO v_limit
                      FROM public.early_adopter_campaign c WHERE c.id;
                END IF;

                UPDATE public.early_adopter_seats s
                   SET released_at            = NULL,
                       release_reason         = NULL,
                       released_by_payment_id = NULL,
                       over_capacity          = s.over_capacity
                                                OR (v_limit IS NOT NULL AND v_live >= v_limit)
                 WHERE s.seat_id = v_prev.id;

                RAISE NOTICE
                    'release_early_adopter_seat: refund of payment % gave back seat % '
                    '(the seat that payment had superseded)', v_prev.payby, v_prev.id;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING
                'release_early_adopter_seat: refund for user % (live seat payment %) '
                'could not give back the superseded seat: % (%) — restore it by hand',
                p_user_id, v_seat.pay, SQLERRM, SQLSTATE;
        END;
    END IF;

    RETURN v_n > 0;
END;
$function$;

REVOKE ALL ON FUNCTION public.release_early_adopter_seat(uuid, text, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_early_adopter_seat(uuid, text, uuid) TO service_role;

COMMENT ON FUNCTION public.release_early_adopter_seat(uuid, text, uuid) IS
'Give a seat back to the pool (138; 192; 226). reason = ''refund'' (status void, NOT restorable, NOT a forfeiture), ''cancelled'' (forfeiture record while ladder_v2 is off; restorable by restore_early_adopter_seat) or (226) ''downgraded'' (a scheduled downgrade took effect at renewal with no seat claimed for the new term; counts as using the tier; not restorable). ''downgraded'' must be called AFTER grant_plan(new plan): a live BATCH seat on the plan the user is on now (the target) is left alone (FALSE + NOTICE) — it is the new term''s own seat, or one that still prices the target; legacy seats are always released. p_payment_id (226, optional): with ''downgraded'', the renewal payment that moved the user off the plan (a list-priced downgrade claims no seat, so nothing else records it) — stamped as released_by_payment_id when it is this user''s payment; ignored otherwise and for other reasons. 2-arg calls resolve through the DEFAULT. Takes the claim''s advisory lock. On a ''refund'' whose released seat was claimed by a payment now ''refunded'', gives back the seat that payment superseded (release_reason ''upgraded'' or ''downgraded'', released_by_payment_id = it) if its window is still open — refunding a switch returns the previous seat; (226) a ''refund'' that releases NO live seat gives back the seat a now-''refunded'' payment superseded (the list-priced downgrade). Returns TRUE only if a live seat was released; FALSE is informational. Never fails the refund over the give-back (WARNING instead). Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 3. claim_early_adopter_seat — 'downgraded' when the superseded seat ranks higher
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.claim_early_adopter_seat(p_user_id uuid, p_payment_id uuid)
 RETURNS TABLE(seat_id uuid, promo_ends_at timestamp with time zone, over_capacity boolean, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_pay      RECORD;
    v_plan     RECORD;
    v_camp     RECORD;
    v_seat     RECORD;
    v_batch    RECORD;
    v_held     RECORD;
    v_has_held BOOLEAN := false;
    v_live     INTEGER;
    v_over     BOOLEAN := false;
    v_gross    NUMERIC;
    v_at       TIMESTAMPTZ;
    v_ends     TIMESTAMPTZ;
    v_id       UUID;
    v_v2       BOOLEAN;
    v_v2_at    TIMESTAMPTZ;
    v_price    NUMERIC;
    v_months   INTEGER;
    v_raw_sar  TEXT;
    v_raw_mon  TEXT;
    v_reason   TEXT;                                     -- (226)
BEGIN
    -- ⚠ FIRST, same key as 138 — every writer of the seat table takes it.
    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    SELECT t.user_id, t.plan_id, t.paid_at, t.amount_sar, t.upgrade_credit_sar,
           t.promo_batch_id, t.created_at, t.intro_offer_terms,
           t.initiated_by, t.period_start                                  -- (226)
      INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id;

    IF NOT FOUND THEN
        RAISE NOTICE 'claim_early_adopter_seat: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'payment_not_found'::text;
        RETURN;
    END IF;

    -- Idempotency key 1: this exact payment (both paths).
    SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.payment_id = p_payment_id;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    IF p_user_id IS NULL OR (v_pay.user_id IS NOT NULL AND v_pay.user_id <> p_user_id) THEN
        RAISE NOTICE 'claim_early_adopter_seat: user mismatch (arg=%, payment=%)',
                     p_user_id, v_pay.user_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'user_mismatch'::text;
        RETURN;
    END IF;

    -- ══ v2: the payment was priced from a batch (192) ══════════════════════
    IF v_pay.promo_batch_id IS NOT NULL THEN
        SELECT b.batch_id AS bid, b.plan_id AS bplan, b.price_tier AS btier,
               b.price_sar AS bprice, b.months AS bmonths, b.seat_limit AS blimit
          INTO v_batch
          FROM public.promo_batches b
         WHERE b.batch_id = v_pay.promo_batch_id;

        IF NOT FOUND THEN                     -- unreachable while the FK stands
            RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'plan_not_eligible'::text;
            RETURN;
        END IF;
        IF v_batch.bplan IS DISTINCT FROM v_pay.plan_id THEN
            RAISE WARNING
                'claim_early_adopter_seat: payment % is for plan % but carries batch % '
                '(plan %) — no seat. Checkout bug: the stamped batch must be the one '
                'promo_quote returned for this plan.',
                p_payment_id, v_pay.plan_id, v_batch.bid, v_batch.bplan;
            RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'plan_not_eligible'::text;
            RETURN;
        END IF;

        -- (V1) THE QUOTED TERMS, not the batch row's current ones. Checkout
        -- stamps intro_offer_terms {"offer_sar", "offer_months"} on every batch
        -- sale; an operator may edit the batch between checkout and settlement
        -- (the plan's whole point is that batches change by UPDATE), and the
        -- customer bought what was on the screen. The batch row is only the
        -- fallback for a payment that carries no terms. Parsed defensively — a
        -- cast error here would 500 the webhook with the money already in.
        v_raw_sar := v_pay.intro_offer_terms ->> 'offer_sar';
        v_raw_mon := v_pay.intro_offer_terms ->> 'offer_months';

        IF v_raw_sar ~ '^[0-9]+(\.[0-9]+)?$' THEN
            v_price := round(v_raw_sar::numeric, 2);
        ELSE
            IF v_raw_sar IS NOT NULL THEN
                RAISE WARNING
                    'claim_early_adopter_seat: payment % has unreadable offer_sar % — '
                    'using batch % price %', p_payment_id, v_raw_sar, v_batch.bid, v_batch.bprice;
            END IF;
            v_price := v_batch.bprice;
        END IF;

        -- regex and cast in separate statements: SQL does not promise to
        -- evaluate the left side of an AND first
        v_months := NULL;
        IF v_raw_mon ~ '^[0-9]{1,2}$' THEN
            v_months := v_raw_mon::integer;
            IF v_months < 1 OR v_months > 12 THEN
                v_months := NULL;
            END IF;
        END IF;
        IF v_months IS NULL THEN
            IF v_raw_mon IS NOT NULL THEN
                RAISE WARNING
                    'claim_early_adopter_seat: payment % has unreadable offer_months % — '
                    'using batch % months %', p_payment_id, v_raw_mon, v_batch.bid, v_batch.bmonths;
            END IF;
            v_months := v_batch.bmonths;
        END IF;

        -- THE INVARIANT, per quote: a batch seat holder is someone charged the
        -- quoted price (or less). gross = amount + credit = the price that was
        -- on the screen; one cent covers q2 rounding; NULL fails closed (138).
        v_gross := v_pay.amount_sar + COALESCE(v_pay.upgrade_credit_sar, 0);
        IF v_gross IS NULL OR v_gross > v_price + 0.01 THEN
            RAISE NOTICE
                'claim_early_adopter_seat: payment % was quoted % (offer % in batch %) — no seat',
                p_payment_id, v_gross, v_price, v_batch.bid;
            RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'not_promo_priced'::text;
            RETURN;
        END IF;

        -- The user's live seat, if any (early_adopter_seats_one_live: ≤ 1).
        -- (226) with its plan: a batch seat's own, a legacy seat's claiming
        -- payment's (NULL when it has none).
        SELECT s.seat_id AS hid, s.batch_id AS hbatch, s.promo_ends_at AS hends,
               s.over_capacity AS hover, COALESCE(s.plan_id, t.plan_id) AS hplan
          INTO v_held
          FROM public.early_adopter_seats s
          LEFT JOIN public.payment_transactions t ON t.payment_id = s.payment_id
         WHERE s.user_id = p_user_id
           AND s.released_at IS NULL
         ORDER BY s.claimed_at DESC
         LIMIT 1;
        v_has_held := FOUND;

        IF v_has_held THEN
            IF v_held.hbatch IS NOT DISTINCT FROM v_batch.bid THEN
                -- already seated in this very batch (another payment of the
                -- same quote): keep the seat and its window
                RETURN QUERY SELECT v_held.hid, v_held.hends, v_held.hover, 'already_claimed'::text;
                RETURN;
            END IF;
        END IF;

        -- Capacity of THIS batch, under the lock, before the insert. Past the
        -- limit the seat is granted anyway and stamped (§1.4-5, 138 §3.5).
        SELECT count(*) INTO v_live
          FROM public.early_adopter_seats s
         WHERE s.released_at IS NULL
           AND s.batch_id = v_batch.bid;

        IF v_live >= v_batch.blimit THEN
            v_over := true;
            RAISE NOTICE
                'claim_early_adopter_seat: OVER CAPACITY — payment % was quoted batch % '
                'before it filled; granting seat % of %',
                p_payment_id, v_batch.bid, v_live + 1, v_batch.blimit;
        END IF;

        -- ANCHOR ON paid_at, NEVER now() (137/138: idempotent BY VALUE — both
        -- confirmation paths compute the same window).
        -- (226) A RENEWAL anchors on the period it pays FOR (period_start = the
        -- old expiry), not on when it settled: the backend starts the new term
        -- at the old expiry, so a charge paid late on a grace rung (E + 12h)
        -- would otherwise end its window at E + 60d + 12h and still cover the
        -- period starting at E + 60d — a third offer month. Checkout claims keep
        -- paid_at. Both are fixed values, so the claim stays idempotent.
        IF v_pay.initiated_by = 'renewal' AND v_pay.period_start IS NOT NULL THEN
            v_at := v_pay.period_start;
        ELSE
            v_at := COALESCE(v_pay.paid_at, now());
        END IF;
        v_ends := v_at + make_interval(days => 30 * v_months);           -- (V1)

        -- Release + insert in ONE subtransaction: a unique_violation (a manual
        -- INSERT that skipped the lock) rolls the release back too.
        BEGIN
            IF v_has_held THEN
                -- (I4) legacy seat, other plan, or a spent / other batch of
                -- this plan: superseded by this purchase. Counts as used,
                -- never forfeits. (V5) remembers WHICH payment superseded it,
                -- so refunding that payment gives this seat back.
                -- (226) contract §8-3: superseded by a payment for a plan with
                -- a LOWER list price than the seat's own plan → 'downgraded'
                -- (a lapsed max seat buying pro_plus, the scheduled downgrade
                -- priced as new at renewal). It always counts as used (D12).
                -- Otherwise 'upgraded' exactly as before (a switch up, the same
                -- plan, a legacy seat of a lower plan). Rank = list price; when
                -- either plan has none (off sale — a PROMISED downgrade can be
                -- charged after its target left the shelf) rank = weekly
                -- points. Same plan, or no plan to compare (a legacy seat with
                -- no payment) → 'upgraded'.
                v_reason := CASE
                    WHEN v_held.hplan IS NOT DISTINCT FROM v_batch.bplan THEN 'upgraded'
                    WHEN COALESCE((SELECT CASE WHEN hp.price_sar IS NOT NULL AND np.price_sar IS NOT NULL
                                               THEN hp.price_sar > np.price_sar
                                               ELSE hp.points_weekly > np.points_weekly
                                          END
                                     FROM public.plans hp, public.plans np
                                    WHERE hp.plan_id = v_held.hplan
                                      AND np.plan_id = v_batch.bplan),
                                  false)
                        THEN 'downgraded'
                    ELSE 'upgraded'
                END;

                UPDATE public.early_adopter_seats s
                   SET released_at            = now(),
                       release_reason         = v_reason,
                       released_by_payment_id = p_payment_id
                 WHERE s.seat_id = v_held.hid;
            END IF;

            INSERT INTO public.early_adopter_seats AS s
                (user_id, payment_id, claimed_at, promo_ends_at, over_capacity,
                 batch_id, plan_id, price_tier, price_sar)
            VALUES (p_user_id, p_payment_id, v_at, v_ends, v_over,
                    v_batch.bid, v_batch.bplan, v_batch.btier, v_price)          -- (V1)
            RETURNING s.seat_id, s.promo_ends_at, s.over_capacity
                 INTO v_id, v_ends, v_over;
        EXCEPTION WHEN unique_violation THEN
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
        RETURN;
    END IF;

    -- ══ legacy path — 183's body from here ═════════════════════════════════
    -- Idempotency key 2: the user already holds a live seat (an upgrade
    -- carries the window over; 138 §1 rule 8).
    SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

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

    SELECT c.ladder_v2, c.ladder_v2_at
      INTO v_v2, v_v2_at
      FROM public.early_adopter_campaign c
     WHERE c.id;

    -- FORFEITURE — the same predicate effective_plan_price (a) uses, and like
    -- it switched off while ladder_v2 is on (192). 'upgraded' (and 226's
    -- 'downgraded') never match.
    IF NOT COALESCE(v_v2, false)
       AND EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.release_reason = 'cancelled')
       AND NOT public.is_intro_offer_member(p_user_id) THEN
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'forfeited'::text;
        RETURN;
    END IF;

    -- THE PROMO-QUOTE GATE (138): charged the legacy promo price or less.
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

    -- (R2) NO NEW LEGACY SEATS AFTER THE FLIP. While ladder_v2 is on, every
    -- promo checkout is stamped with a batch, so an UNSTAMPED payment created at
    -- or after the flip that is nonetheless priced at or below the legacy promo
    -- is a checkout bug (or the old backend): it must not mint a 90-day legacy
    -- seat. Checked after the gate on purpose — a list-priced sale has already
    -- answered 'not_promo_priced' above, exactly as today, so only the anomaly
    -- lands here, on the backend's ERROR branch. A payment created BEFORE the
    -- flip keeps today's claim: its quote was the legacy promo (the quote is the
    -- promise). With the flag back off, the legacy claim applies again.
    IF COALESCE(v_v2, false)
       AND v_v2_at IS NOT NULL
       AND v_pay.created_at >= v_v2_at THEN
        RAISE WARNING
            'claim_early_adopter_seat: payment % (plan %, gross %) was created after '
            'the ladder_v2 flip (%) with NO promo_batch_id yet at or below the legacy '
            'promo % — no seat. Checkout bug: every promo sale after the flip must be '
            'stamped with the batch promo_quote returned.',
            p_payment_id, v_pay.plan_id, v_gross, v_v2_at, v_plan.promo;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'plan_not_eligible'::text;
        RETURN;
    END IF;

    SELECT c.seat_limit AS lim, c.promo_days AS days, c.enabled AS is_on
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;

    IF NOT FOUND THEN
        RAISE WARNING
            'claim_early_adopter_seat: NO early_adopter_campaign ROW — payment % '
            'was charged the promo price and cannot be seated. Restore the row '
            '(migration 138 §3) and re-run the claim.',
            p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'campaign_disabled'::text;
        RETURN;
    END IF;

    IF NOT v_camp.is_on THEN
        RAISE NOTICE
            'claim_early_adopter_seat: campaign switch is OFF but payment % was '
            'charged the promo price — seating it anyway (a quote outstanding at '
            'the flip). No new promo quotes can be issued while the flag is down.',
            p_payment_id;
    END IF;

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

    v_at   := COALESCE(v_pay.paid_at, now());
    v_ends := v_at + make_interval(days => v_camp.days);

    BEGIN
        INSERT INTO public.early_adopter_seats AS s
            (user_id, payment_id, claimed_at, promo_ends_at, over_capacity)
        VALUES (p_user_id, p_payment_id, v_at, v_ends, v_over)
        RETURNING s.seat_id, s.promo_ends_at, s.over_capacity
             INTO v_id, v_ends, v_over;
    EXCEPTION WHEN unique_violation THEN
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

REVOKE ALL ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) TO service_role;

COMMENT ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) IS
'Seat a paid pro/pro_plus/max purchase (138; intro exemption 183; batches 192; downgrade 226). Called AFTER grant_plan, under pg_advisory_xact_lock(hashtext(''early_adopter_seats'')). Idempotent on payment_id. V2 PATH — the payment carries promo_batch_id (honoured even if ladder_v2 was turned off since): plan must match the batch; the QUOTED terms (intro_offer_terms offer_sar / offer_months; the batch row only if absent) set the gate amount_sar + upgrade_credit_sar <= offer + 0.01, the seat price_sar and promo_ends_at = anchor + 30 × offer_months days, where anchor (= claimed_at) is paid_at for a checkout and (226) period_start for a renewal payment (initiated_by = ''renewal''), so a late-settled renewal never stretches the window into another period; any live seat of the user not in this batch is released with released_by_payment_id = this payment — ''downgraded'' when that seat''s plan (a batch seat''s plan_id, a legacy seat''s claiming payment''s plan) ranks HIGHER than this payment''s plan (226: list price; points_weekly when either plan has no list price), else ''upgraded'' (a switch up, the same plan, a legacy seat, a spent window); the stamped batch is honoured whether it is enabled, disabled or full (the quote — or a scheduled downgrade''s promise — is the promise); the new seat copies batch_id/plan_id/price_tier; over_capacity when the batch already had seat_limit live seats. LEGACY PATH (no promo_batch_id): 183''s body — a live seat carries over, gate against plans.promo_price_sar, window promo_days — with the ''cancelled'' forfeiture skipped while ladder_v2 is on, and (R2) while ladder_v2 is on a payment created at or after early_adopter_campaign.ladder_v2_at that passes the legacy gate gets NO seat (plan_not_eligible + WARNING). Actions unchanged: claimed / already_claimed / not_promo_priced / campaign_disabled / forfeited / plan_not_eligible / payment_not_found / user_mismatch; seat_id IS NULL ⇔ no seat. Never raises on a missing payment. Service-role only.';


-- ════════════════════════════════════════════════════════════════════════════
-- 4. Static checks
-- ════════════════════════════════════════════════════════════════════════════
DO $static$
DECLARE
    r    RECORD;
    v_n  BIGINT;
    v_s  TEXT;
BEGIN
    FOR r IN
        SELECT *
          FROM (VALUES
                ('claim_early_adopter_seat', 'p_user_id uuid, p_payment_id uuid',
                 'TABLE(seat_id uuid, promo_ends_at timestamp with time zone, over_capacity boolean, action text)'),
                ('release_early_adopter_seat', 'p_user_id uuid, p_reason text, p_payment_id uuid DEFAULT NULL::uuid', 'boolean')
               ) v(name, args, result)
    LOOP
        SELECT count(*) INTO v_n
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = r.name;
        IF v_n <> 1 THEN
            RAISE EXCEPTION '226 static: % has % overloads, expected 1', r.name, v_n;
        END IF;
        SELECT format('%s ⟂ %s ⟂ %s ⟂ %s ⟂ %s ⟂ %s',
                      pg_get_function_arguments(p.oid), pg_get_function_result(p.oid),
                      p.provolatile, p.prosecdef, p.proconfig::text, p.proacl::text)
          INTO v_s
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = r.name;
        IF v_s IS DISTINCT FROM format('%s ⟂ %s ⟂ v ⟂ t ⟂ {search_path=public} ⟂ {postgres=X/postgres,service_role=X/postgres}',
                                       r.args, r.result) THEN
            RAISE EXCEPTION '226 static: % is "%"', r.name, v_s;
        END IF;
    END LOOP;

    SELECT count(*) INTO v_n
      FROM pg_constraint c
     WHERE c.conrelid = 'public.early_adopter_seats'::regclass
       AND c.conname IN ('early_adopter_seats_release_reason_check', 'early_adopter_seats_released_by_check')
       AND c.convalidated
       AND position('''downgraded''' IN pg_get_constraintdef(c.oid)) > 0;
    IF v_n <> 2 THEN
        RAISE EXCEPTION '226 static: the two seat CHECKs do not both allow ''downgraded'' (% of 2)', v_n;
    END IF;

    RAISE NOTICE '226 static: OK — claim / release keep their signatures, ACL {postgres,service_role}, SECURITY DEFINER, search_path; both seat CHECKs allow ''downgraded''';
END
$static$;


-- ════════════════════════════════════════════════════════════════════════════
-- 5. Behavioural self-tests — seats and claims, ALL ROLLED BACK (192 §9 pattern)
-- ════════════════════════════════════════════════════════════════════════════
--   M1  free, live max_t2_c seat (day 10)        pays pro_plus 99.90 → 'downgraded' (+ payment);
--                                                 still used (D12); refund gives the max seat back (V5)
--   M2  ON max, LEGACY max seat                   downgrade priced as new → pro_plus → 'downgraded'
--   P1  ON pro, live pro_t2_a seat (day 10)       pays pro_plus → 'upgraded' (unchanged), not used (D12)
--   P2  free, live pro_plus_t1_a seat (day 10)    pays pro 59.90 → 'downgraded', used
--   S1  free, pro_t1_a seat, window spent         pays pro_t2_a → 'upgraded' (same plan, unchanged)
--   X1  ON pro, live max_t2_c seat                release(…, 'downgraded') → released, used, not restorable
--   X2  ON pro_plus, live pro_plus_t1_a seat      release(…, 'downgraded') → left alone (S2)
--   X3  ON pro, LEGACY pro seat                   release(…, 'downgraded') → released (legacy: always)
--   Q1  ON max; promised pro_plus 99.90 × 2; at renewal pro_plus is OFF SALE and the batch is
--       DISABLED and repriced → grant_plan works, the claim seats it on the promised terms (S4)
--   Q2  ON max; the same promise, batch now FULL → seated, over_capacity (S4)
--   Q3  ON max with a live max_t2_c seat; pro_plus off sale → the claim still says 'downgraded' (S3)
--   Q4  grant_plan(…, 'pro_plus') with no list price, no payment → granted
--   W1  ON max; promised pro_plus offer; the renewal for period E is PAID LATE (E + 12h)
--       → seat window E … E + 60d (anchored on period_start): the E + 60d renewal is list
--   C2  a CHECKOUT claim → window still anchored on paid_at
--   L1  ON max with a live max_t2_c seat; LIST-priced downgrade to pro_plus; release
--       ('downgraded', payment) stamps it; refunding that payment gives the max seat back
DO $selftest$
DECLARE
    v_seats0   BIGINT;
    v_pays0    BIGINT;
    v_users0   BIGINT;
    v_batches0 TEXT;
    v_plans0   TEXT;
    v_camp0    TEXT;
    v_run  TEXT := replace(gen_random_uuid()::text, '-', '');
    v_ids  UUID[] := ARRAY[]::uuid[];
    v_auth UUID;
    v_id   UUID;
    v_i    INTEGER;
    v_m1 UUID; v_m2 UUID; v_p1 UUID; v_p2 UUID; v_s1 UUID; v_x1 UUID; v_x2 UUID; v_x3 UUID;
    v_q1 UUID; v_q2 UUID; v_q3 UUID; v_q4 UUID; v_w1 UUID; v_c2 UUID; v_l1 UUID;
    v_e     TIMESTAMPTZ;
    v_pay   UUID;
    v_pay2  UUID;
    v_old   UUID;
    v_new   UUID;
    v_act   TEXT;
    v_r     RECORD;
    v_s     TEXT;
    v_ok    BOOLEAN;
    v_n     BIGINT;
    v_p     NUMERIC;
    v_over  BOOLEAN;
BEGIN
    SELECT count(*) INTO v_seats0 FROM public.early_adopter_seats;
    SELECT count(*) INTO v_pays0  FROM public.payment_transactions;
    SELECT count(*) INTO v_users0 FROM public.users;
    SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), '')) INTO v_batches0 FROM public.promo_batches b;
    SELECT md5(COALESCE(string_agg(p::text, '|' ORDER BY p.plan_id), ''))  INTO v_plans0   FROM public.plans p;
    SELECT md5(c::text) INTO v_camp0 FROM public.early_adopter_campaign c WHERE c.id;

    IF NOT has_table_privilege('auth.users', 'INSERT') THEN
        RAISE EXCEPTION '226 self-test: this role cannot INSERT into auth.users. Run the migration as postgres (SQL editor).';
    END IF;

    BEGIN
        -- the launch state (225's switch): pro_plus priced, its offer on
        UPDATE public.early_adopter_campaign c SET enabled = true, ladder_v2 = true WHERE c.id;
        UPDATE public.plans p SET price_sar = 149.90 WHERE p.plan_id = 'pro_plus';
        UPDATE public.promo_batches b
           SET enabled = true,
               seat_limit = (SELECT count(*)::integer FROM public.early_adopter_seats x
                              WHERE x.released_at IS NULL AND x.batch_id = b.batch_id) + 1000
         WHERE b.batch_id IN ('pro_plus_t1_a', 'max_t2_c');

        FOR v_i IN 1 .. 15 LOOP
            v_auth := gen_random_uuid();
            INSERT INTO auth.users (id, instance_id, aud, role, email,
                                    raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
            VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
                    format('selftest-226-%s-%s@selftest.invalid', v_run, v_i),
                    '{}'::jsonb, '{}'::jsonb, now(), now());
            SELECT u.user_id INTO v_id FROM public.users u WHERE u.auth_id = v_auth;
            IF v_id IS NULL THEN
                RAISE EXCEPTION '226 self-test: on_auth_user_created built no public.users row';
            END IF;
            UPDATE public.users u SET profession_group = 'legal' WHERE u.user_id = v_id;
            v_ids := v_ids || v_id;
        END LOOP;
        v_m1 := v_ids[1]; v_m2 := v_ids[2]; v_p1 := v_ids[3]; v_p2 := v_ids[4];
        v_s1 := v_ids[5]; v_x1 := v_ids[6]; v_x2 := v_ids[7]; v_x3 := v_ids[8];
        v_q1 := v_ids[9]; v_q2 := v_ids[10]; v_q3 := v_ids[11]; v_q4 := v_ids[12];
        v_w1 := v_ids[13]; v_c2 := v_ids[14]; v_l1 := v_ids[15];

        UPDATE public.user_subscriptions s SET plan_id = 'max'      WHERE s.user_id IN (v_m2, v_q1, v_q2, v_q3, v_w1, v_l1);
        UPDATE public.user_subscriptions s SET plan_id = 'pro'      WHERE s.user_id IN (v_p1, v_x1, v_x3);
        UPDATE public.user_subscriptions s SET plan_id = 'pro_plus' WHERE s.user_id = v_x2;

        -- ── D1. M1: lapsed max seat holder buys pro_plus at the offer ──
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_m1, 'max', 169.90, 'paid', now() - interval '10 days', 'max_t2_c') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_m1, v_pay, now() - interval '10 days', now() + interval '50 days', 'max_t2_c', 'max', 2, 169.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_m1, 'pro_plus', 99.90, 'paid', now(), 'pro_plus_t1_a',
                '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action, c.seat_id INTO v_act, v_new FROM public.claim_early_adopter_seat(v_m1, v_pay2) c;
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed'
           OR v_r.release_reason IS DISTINCT FROM 'downgraded'
           OR v_r.released_by_payment_id IS DISTINCT FROM v_pay2 THEN
            RAISE EXCEPTION '226 self-test [D1 max seat → pro_plus claim]: action %, old seat %/% (expected claimed, downgraded/the payment)',
                v_act, v_r.release_reason, v_r.released_by_payment_id;
        END IF;
        -- D12: a downgrade on day 10 still USES the Max offer (only an early upgrade does not)
        IF NOT public.promo_tier_used(v_m1, 'max', 2::smallint) THEN
            RAISE EXCEPTION '226 self-test [D1 D12]: max tier 2 not counted as used after a day-10 downgrade';
        END IF;
        -- refunding the downgrade payment: the pro_plus seat ends, the max seat comes back (V5),
        -- and the refunded purchase never used pro_plus tier 1 (O1)
        UPDATE public.payment_transactions t SET status = 'refunded' WHERE t.payment_id = v_pay2;
        v_ok := public.release_early_adopter_seat(v_m1, 'refund');
        SELECT s.released_at, s.release_reason, s.released_by_payment_id INTO v_r
          FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF NOT v_ok
           OR v_r.released_at IS NOT NULL OR v_r.release_reason IS NOT NULL OR v_r.released_by_payment_id IS NOT NULL
           OR NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.seat_id = v_new AND s.release_reason = 'refund')
           OR public.promo_tier_used(v_m1, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '226 self-test [D1 refund of the downgrade]: released %, max seat %/%/%, pro_plus t1 used %',
                v_ok, v_r.released_at, v_r.release_reason, v_r.released_by_payment_id,
                public.promo_tier_used(v_m1, 'pro_plus', 1::smallint);
        END IF;
        RAISE NOTICE '226 [D1]: OK — lapsed max seat → pro_plus claim releases it ''downgraded'' (released_by = the payment); max tier still used; refunding the payment gives the max seat back and pro_plus t1 is unused';

        -- ── D2. M2 on max with a LEGACY max seat: the scheduled downgrade priced as new ──
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_m2, 'max', 99.90, 'paid', now() - interval '20 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_m2, v_pay, now() - interval '20 days', now() + interval '70 days') RETURNING seat_id INTO v_old;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id, c.price_tier, c.months, c.intro_group), ';')
          INTO v_s FROM public.promo_checkout_price(v_m2, 'pro_plus', true) c;
        IF v_s IS DISTINCT FROM 't|99.90|pro_plus_t1_a|1|2|' THEN
            RAISE EXCEPTION '226 self-test [D2 downgrade price, as new]: %', v_s;
        END IF;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_m2, 'pro_plus', 99.90, 'paid', now(), 'pro_plus_t1_a',
                '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_m2, v_pay2) c;
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed' OR v_r.release_reason IS DISTINCT FROM 'downgraded'
           OR v_r.released_by_payment_id IS DISTINCT FROM v_pay2 THEN
            RAISE EXCEPTION '226 self-test [D2 legacy max seat → pro_plus]: action %, old seat %/%', v_act, v_r.release_reason, v_r.released_by_payment_id;
        END IF;
        RAISE NOTICE '226 [D2]: OK — max → pro_plus priced as new (99.90 × 2); the legacy max seat is released ''downgraded''';

        -- ── D3. P1 on pro → pro_plus: still 'upgraded', and D12 leaves the Pro offer unused ──
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_p1, 'pro', 59.90, 'paid', now() - interval '10 days', 'pro_t2_a') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats
            (user_id, payment_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_p1, v_pay, now() - interval '10 days', now() + interval '50 days', 'pro_t2_a', 'pro', 2, 59.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_p1, 'pro_plus', 99.90, 'paid', now(), 'pro_plus_t1_a',
                '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_p1, v_pay2) c;
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed' OR v_r.release_reason IS DISTINCT FROM 'upgraded'
           OR v_r.released_by_payment_id IS DISTINCT FROM v_pay2
           OR public.promo_tier_used(v_p1, 'pro', 2::smallint) THEN
            RAISE EXCEPTION '226 self-test [D3 pro → pro_plus]: action %, old seat %/%, pro t2 used %',
                v_act, v_r.release_reason, v_r.released_by_payment_id, public.promo_tier_used(v_p1, 'pro', 2::smallint);
        END IF;

        -- ── D4. P2 lapsed pro_plus seat → pro offer: 'downgraded', used ──
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_p2, now() - interval '10 days', now() + interval '50 days', 'pro_plus_t1_a', 'pro_plus', 1, 99.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_p2, 'pro', 59.90, 'paid', now(), 'pro_t2_a', '{"offer_sar": "59.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_p2, v_pay2) c;
        SELECT s.release_reason INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed' OR v_s IS DISTINCT FROM 'downgraded'
           OR NOT public.promo_tier_used(v_p2, 'pro_plus', 1::smallint) THEN
            RAISE EXCEPTION '226 self-test [D4 pro_plus seat → pro]: action %, old seat %, pro_plus t1 used %',
                v_act, v_s, public.promo_tier_used(v_p2, 'pro_plus', 1::smallint);
        END IF;

        -- ── D5. S1 same plan, spent window → 'upgraded' (unchanged) ──
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_s1, now() - interval '70 days', now() - interval '10 days', 'pro_t1_a', 'pro', 1, 19.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_s1, 'pro', 59.90, 'paid', now(), 'pro_t2_a', '{"offer_sar": "59.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_s1, v_pay2) c;
        SELECT s.release_reason INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed' OR v_s IS DISTINCT FROM 'upgraded'
           OR NOT public.promo_tier_used(v_s1, 'pro', 1::smallint) THEN
            RAISE EXCEPTION '226 self-test [D5 same plan]: action %, old seat %, pro t1 used %',
                v_act, v_s, public.promo_tier_used(v_s1, 'pro', 1::smallint);
        END IF;
        RAISE NOTICE '226 [D3–D5]: OK — pro → pro_plus ''upgraded'' (Pro offer unused, D12); lapsed pro_plus seat → pro ''downgraded'' (used); same plan, spent window → ''upgraded'' (used)';

        -- ── D6. release_early_adopter_seat(…, 'downgraded') ──
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_x1, now() - interval '10 days', now() + interval '50 days', 'max_t2_c', 'max', 2, 169.90)
        RETURNING seat_id INTO v_old;
        IF public.release_early_adopter_seat(v_x1, 'bogus') THEN
            RAISE EXCEPTION '226 self-test [D6 unknown reason]: released';
        END IF;
        v_ok := public.release_early_adopter_seat(v_x1, 'downgraded');      -- X1 is on pro now, the seat is max
        SELECT s.release_reason, s.released_by_payment_id INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF NOT v_ok OR v_r.release_reason IS DISTINCT FROM 'downgraded' OR v_r.released_by_payment_id IS NOT NULL
           OR NOT public.promo_tier_used(v_x1, 'max', 2::smallint) THEN
            RAISE EXCEPTION '226 self-test [D6 release downgraded]: released %, seat %/%, max t2 used %',
                v_ok, v_r.release_reason, v_r.released_by_payment_id, public.promo_tier_used(v_x1, 'max', 2::smallint);
        END IF;
        v_ok := public.restore_early_adopter_seat(v_x1);
        IF v_ok
           OR EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.seat_id = v_old AND s.released_at IS NULL) THEN
            RAISE EXCEPTION '226 self-test [D6 restore]: a ''downgraded'' seat was restored';
        END IF;
        -- X2: on pro_plus holding its own pro_plus seat → left alone (S2)
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_x2, now(), now() + interval '60 days', 'pro_plus_t1_a', 'pro_plus', 1, 99.90)
        RETURNING seat_id INTO v_old;
        v_ok := public.release_early_adopter_seat(v_x2, 'downgraded');
        IF v_ok
           OR NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.seat_id = v_old AND s.released_at IS NULL) THEN
            RAISE EXCEPTION '226 self-test [D6 own-plan seat]: the current plan''s seat was released as ''downgraded''';
        END IF;
        -- X3: on pro with a LEGACY pro seat → a legacy seat is always released
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_x3, 'pro', 49.90, 'paid', now() - interval '10 days') RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_x3, v_pay, now() - interval '10 days', now() + interval '80 days')
        RETURNING seat_id INTO v_old;
        -- (separate statements: an EXISTS in the same expression as the call
        -- would read the snapshot from before the call's UPDATE)
        -- 3-arg named call with ANOTHER user's payment (v_pay2 is S1's): the
        -- seat is released, the foreign payment id is ignored
        v_ok := public.release_early_adopter_seat(p_user_id => v_x3, p_reason => 'downgraded',
                                                  p_payment_id => v_pay2);
        SELECT format('%s|%s', s.release_reason, s.released_by_payment_id IS NULL) INTO v_s
          FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF NOT v_ok OR v_s IS DISTINCT FROM 'downgraded|t' THEN
            RAISE EXCEPTION '226 self-test [D6 legacy seat]: released %, seat % (expected true, downgraded|t)', v_ok, v_s;
        END IF;
        RAISE NOTICE '226 [D6]: OK — release(''downgraded'') ends the left plan''s seat (no payment id, used, not restorable) and a legacy seat, leaves a batch seat on the current plan alone, refuses unknown reasons, ignores another user''s payment id (2-arg and 3-arg named calls)';

        -- ── D7. the CHECKs ──
        v_ok := false;
        BEGIN
            INSERT INTO public.early_adopter_seats
                (user_id, claimed_at, promo_ends_at, released_at, release_reason, released_by_payment_id)
            VALUES (v_x1, now() - interval '5 days', now() + interval '5 days', now(), 'cancelled', v_pay2);
        EXCEPTION WHEN check_violation THEN
            v_ok := true;
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION '226 self-test [D7]: released_by_payment_id accepted on a ''cancelled'' seat';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.early_adopter_seats
                (user_id, claimed_at, promo_ends_at, released_at, release_reason)
            VALUES (v_x1, now() - interval '5 days', now() + interval '5 days', now(), 'demoted');
        EXCEPTION WHEN check_violation THEN
            v_ok := true;
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION '226 self-test [D7]: an unknown release_reason was accepted';
        END IF;
        RAISE NOTICE '226 [D7]: OK — CHECKs accept ''downgraded'' (+ payment id), still refuse unknown reasons and a payment id on ''cancelled''';

        -- ── D8. Q1: the PROMISE honoured — plan off sale, batch disabled and repriced ──
        -- Scheduled while pro_plus_t1_a sold at 99.90 × 2; by the renewal the owner
        -- took pro_plus off sale and switched the batch off (and edited it).
        UPDATE public.plans p SET price_sar = NULL WHERE p.plan_id = 'pro_plus';
        UPDATE public.promo_batches b
           SET enabled = false, price_sar = 119.90, months = 1
         WHERE b.batch_id = 'pro_plus_t1_a';
        -- checkout is NOT widened: nobody can be quoted it any more
        SELECT count(*) INTO v_n FROM public.promo_quote(v_q1, 'pro_plus', true) q;
        SELECT string_agg(format('%s|%s|%s', c.ladder_v2, c.price_sar, c.batch_id), ';')
          INTO v_s FROM public.promo_checkout_price(v_q1, 'pro_plus', true) c;
        IF v_n <> 0 OR v_s IS DISTINCT FROM 't||' THEN
            RAISE EXCEPTION '226 self-test [D8 checkout not widened]: % quote row(s), checkout % (expected 0, t||)', v_n, v_s;
        END IF;
        -- the renewal charges the promise, stamped with the promised batch and terms
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, initiated_by, renewal_attempt, period_start,
             promo_batch_id, intro_offer_terms)
        VALUES (v_q1, 'pro_plus', 99.90, 'paid', now(), 'renewal', 0, now(),
                'pro_plus_t1_a', '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        -- grant_plan reads only duration_days: a plan with no list price is granted
        SELECT g.plan_id, g.expires_at INTO v_r FROM public.grant_plan(v_q1, 'pro_plus', 'payment', v_pay2) g;
        IF v_r.plan_id IS DISTINCT FROM 'pro_plus' OR v_r.expires_at IS DISTINCT FROM now() + interval '30 days' THEN
            RAISE EXCEPTION '226 self-test [D8 grant_plan, no list price]: % until % (expected pro_plus, +30 days)', v_r.plan_id, v_r.expires_at;
        END IF;
        IF (SELECT format('%s|%s', s.plan_id, t.fulfilled_at IS NOT NULL)
              FROM public.user_subscriptions s
              JOIN public.payment_transactions t ON t.payment_id = v_pay2
             WHERE s.user_id = v_q1) IS DISTINCT FROM 'pro_plus|t' THEN
            RAISE EXCEPTION '226 self-test [D8 grant_plan]: subscription / fulfilment not written';
        END IF;
        SELECT c.action, c.seat_id, c.over_capacity INTO v_act, v_new, v_over
          FROM public.claim_early_adopter_seat(v_q1, v_pay2) c;
        SELECT s.batch_id, s.plan_id, s.price_tier, s.price_sar, s.promo_ends_at INTO v_r
          FROM public.early_adopter_seats s WHERE s.seat_id = v_new;
        IF v_act IS DISTINCT FROM 'claimed' OR v_over
           OR v_r.batch_id IS DISTINCT FROM 'pro_plus_t1_a' OR v_r.plan_id IS DISTINCT FROM 'pro_plus'
           OR v_r.price_tier IS DISTINCT FROM 1::smallint OR v_r.price_sar IS DISTINCT FROM 99.90
           OR v_r.promo_ends_at IS DISTINCT FROM now() + interval '60 days' THEN
            RAISE EXCEPTION '226 self-test [D8 claim, disabled + repriced batch]: action %, over %, seat %',
                v_act, v_over, to_jsonb(v_r);
        END IF;
        -- the promise outlives the shelf (§0c): month 2 (inside the window) is the
        -- seat price with pro_plus still unpriced; month 3 (window over) is NULL —
        -- an off-sale plan does not renew
        v_p := public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '30 days');
        IF v_p IS DISTINCT FROM 99.90 THEN
            RAISE EXCEPTION '226 self-test [D8 month 2, plan off sale]: % (expected 99.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '60 days');
        IF v_p IS NOT NULL THEN
            RAISE EXCEPTION '226 self-test [D8 month 3, plan off sale]: % (expected NULL)', v_p;
        END IF;
        RAISE NOTICE '226 [D8]: OK — promised pro_plus 99.90 × 2 with pro_plus OFF SALE and the batch DISABLED + repriced (119.90 × 1): grant_plan grants it, the claim seats it at 99.90 for 60 days; checkout still quotes nobody; renewal price month 2 = 99.90, month 3 = NULL';

        -- ── D9. Q2: the same promise, batch now FULL (enabled) → over_capacity ──
        UPDATE public.plans p SET price_sar = 149.90 WHERE p.plan_id = 'pro_plus';
        -- back on sale: month 2 still the seat, month 3 the list price (unchanged rule)
        IF public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '30 days') IS DISTINCT FROM 99.90
           OR public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '60 days') IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '226 self-test [D9 priced plan, month 2 / 3]: % / % (expected 99.90 / 149.90)',
                public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '30 days'),
                public.effective_plan_price(v_q1, 'pro_plus', 'current', now() + interval '60 days');
        END IF;
        UPDATE public.promo_batches b
           SET enabled = true, price_sar = 99.90, months = 2,
               seat_limit = (SELECT count(*)::integer FROM public.early_adopter_seats x
                              WHERE x.released_at IS NULL AND x.batch_id = b.batch_id)
         WHERE b.batch_id = 'pro_plus_t1_a';
        SELECT count(*) INTO v_n FROM public.promo_quote(v_q2, 'pro_plus', true) q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '226 self-test [D9 full batch still quoted]: % row(s)', v_n;
        END IF;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, initiated_by, renewal_attempt, period_start,
             promo_batch_id, intro_offer_terms)
        VALUES (v_q2, 'pro_plus', 99.90, 'paid', now(), 'renewal', 0, now(),
                'pro_plus_t1_a', '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        PERFORM public.grant_plan(v_q2, 'pro_plus', 'payment', v_pay2);
        SELECT c.action, c.over_capacity INTO v_act, v_over FROM public.claim_early_adopter_seat(v_q2, v_pay2) c;
        IF v_act IS DISTINCT FROM 'claimed' OR NOT COALESCE(v_over, false) THEN
            RAISE EXCEPTION '226 self-test [D9 full batch]: action %, over_capacity % (expected claimed, true)', v_act, v_over;
        END IF;
        RAISE NOTICE '226 [D9]: OK — promised offer in a FULL batch is seated with over_capacity; promo_quote quotes the full batch to nobody';

        -- ── D10. Q3: off-sale target, held max seat → still 'downgraded' (S3); Q4: manual grant ──
        UPDATE public.plans p SET price_sar = NULL WHERE p.plan_id = 'pro_plus';
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_q3, now() - interval '10 days', now() + interval '50 days', 'max_t2_c', 'max', 2, 169.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, initiated_by, renewal_attempt, period_start,
             promo_batch_id, intro_offer_terms)
        VALUES (v_q3, 'pro_plus', 99.90, 'paid', now(), 'renewal', 0, now(),
                'pro_plus_t1_a', '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        PERFORM public.grant_plan(v_q3, 'pro_plus', 'payment', v_pay2);
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_q3, v_pay2) c;
        SELECT s.release_reason INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_act IS DISTINCT FROM 'claimed' OR v_s IS DISTINCT FROM 'downgraded' THEN
            RAISE EXCEPTION '226 self-test [D10 off-sale target]: action %, old max seat % (expected claimed, downgraded)', v_act, v_s;
        END IF;
        SELECT g.plan_id INTO v_s FROM public.grant_plan(v_q4, 'pro_plus') g;
        IF v_s IS DISTINCT FROM 'pro_plus'
           OR (SELECT s.plan_id FROM public.user_subscriptions s WHERE s.user_id = v_q4) IS DISTINCT FROM 'pro_plus' THEN
            RAISE EXCEPTION '226 self-test [D10 manual grant, no list price]: %', v_s;
        END IF;
        RAISE NOTICE '226 [D10]: OK — with pro_plus off sale a max seat superseded by the promised pro_plus is released ''downgraded'' (weekly-points rank); grant_plan(user, pro_plus) works with no list price and no payment';

        -- ── D11. window anchor: a renewal anchors on period_start, a checkout on paid_at ──
        UPDATE public.plans p SET price_sar = 149.90 WHERE p.plan_id = 'pro_plus';
        v_e := now() - interval '12 hours';                  -- the old expiry E; paid at E + 12h
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, initiated_by, renewal_attempt, period_start,
             promo_batch_id, intro_offer_terms)
        VALUES (v_w1, 'pro_plus', 99.90, 'paid', now(), 'renewal', 1, v_e,
                'pro_plus_t1_a', '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        PERFORM public.grant_plan(v_w1, 'pro_plus', 'payment', v_pay2);
        SELECT c.action, c.seat_id INTO v_act, v_new FROM public.claim_early_adopter_seat(v_w1, v_pay2) c;
        SELECT s.claimed_at, s.promo_ends_at INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_new;
        IF v_act IS DISTINCT FROM 'claimed'
           OR v_r.claimed_at IS DISTINCT FROM v_e
           OR v_r.promo_ends_at IS DISTINCT FROM v_e + interval '60 days' THEN
            RAISE EXCEPTION '226 self-test [D11 late renewal anchor]: action %, claimed % / ends % (expected E = %, E + 60d)',
                v_act, v_r.claimed_at, v_r.promo_ends_at, v_e;
        END IF;
        -- period 2 (E + 30d) is the offer; period 3 (E + 60d) is NOT a third offer month
        IF public.effective_plan_price(v_w1, 'pro_plus', 'current', v_e + interval '30 days') IS DISTINCT FROM 99.90
           OR public.effective_plan_price(v_w1, 'pro_plus', 'current', v_e + interval '60 days') IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '226 self-test [D11 period 2 / 3]: % / % (expected 99.90 / 149.90)',
                public.effective_plan_price(v_w1, 'pro_plus', 'current', v_e + interval '30 days'),
                public.effective_plan_price(v_w1, 'pro_plus', 'current', v_e + interval '60 days');
        END IF;
        -- a CHECKOUT claim keeps paid_at
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_c2, 'pro_plus', 99.90, 'paid', now() - interval '1 hour', 'pro_plus_t1_a',
                '{"offer_sar": "99.90", "offer_months": "2"}'::jsonb)
        RETURNING payment_id INTO v_pay2;
        SELECT c.action, c.seat_id INTO v_act, v_new FROM public.claim_early_adopter_seat(v_c2, v_pay2) c;
        SELECT s.claimed_at, s.promo_ends_at INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_new;
        IF v_act IS DISTINCT FROM 'claimed'
           OR v_r.claimed_at IS DISTINCT FROM now() - interval '1 hour'
           OR v_r.promo_ends_at IS DISTINCT FROM now() - interval '1 hour' + interval '60 days' THEN
            RAISE EXCEPTION '226 self-test [D11 checkout anchor]: action %, claimed % / ends %', v_act, v_r.claimed_at, v_r.promo_ends_at;
        END IF;
        RAISE NOTICE '226 [D11]: OK — a renewal paid 12h late anchors its seat on period_start (E … E + 60d): period 2 = 99.90, period 3 = list 149.90; a checkout claim still anchors on paid_at';

        -- ── D12. list-priced downgrade: release(…, ''downgraded'', payment) + refund give-back ──
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_l1, now() - interval '10 days', now() + interval '50 days', 'max_t2_c', 'max', 2, 169.90)
        RETURNING seat_id INTO v_old;
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, initiated_by, renewal_attempt, period_start)
        VALUES (v_l1, 'pro_plus', 149.90, 'paid', now(), 'renewal', 0, now())
        RETURNING payment_id INTO v_pay2;
        PERFORM public.grant_plan(v_l1, 'pro_plus', 'payment', v_pay2);
        v_ok := public.release_early_adopter_seat(v_l1, 'downgraded', v_pay2);
        SELECT format('%s|%s', s.release_reason, s.released_by_payment_id = v_pay2) INTO v_s
          FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF NOT v_ok OR v_s IS DISTINCT FROM 'downgraded|t' THEN
            RAISE EXCEPTION '226 self-test [D12 stamp]: released %, seat % (expected true, downgraded|t)', v_ok, v_s;
        END IF;
        -- the list-priced claim seats nothing (no batch, list price) — no live seat now
        SELECT c.action INTO v_act FROM public.claim_early_adopter_seat(v_l1, v_pay2) c;
        IF EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.user_id = v_l1 AND s.released_at IS NULL) THEN
            RAISE EXCEPTION '226 self-test [D12]: the list-priced renewal seated something (action %)', v_act;
        END IF;
        -- refund it: no live seat is released, the max seat comes back
        UPDATE public.payment_transactions t SET status = 'refunded' WHERE t.payment_id = v_pay2;
        v_ok := public.release_early_adopter_seat(v_l1, 'refund');
        SELECT format('%s|%s|%s', s.released_at IS NULL, s.release_reason IS NULL, s.released_by_payment_id IS NULL)
          INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_ok OR v_s IS DISTINCT FROM 't|t|t' THEN
            RAISE EXCEPTION '226 self-test [D12 refund give-back]: returned % (expected false: no live seat), max seat % (expected t|t|t)', v_ok, v_s;
        END IF;
        RAISE NOTICE '226 [D12]: OK — list-priced downgrade: release(user, ''downgraded'', payment) stamps released_by; claim (action %) seats nothing; refunding the payment gives the max seat back (release returns false: no live seat)', v_act;

        RAISE EXCEPTION USING ERRCODE = 'LU226', MESSAGE = '226 self-test sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU226' THEN
        RAISE NOTICE '226: behavioural self-tests D1–D12 passed; every test write rolled back';
    END;

    IF (SELECT count(*) FROM public.early_adopter_seats) <> v_seats0
       OR (SELECT count(*) FROM public.payment_transactions) <> v_pays0
       OR (SELECT count(*) FROM public.users) <> v_users0 THEN
        RAISE EXCEPTION '226 self-test left rows behind (seats/payments/users counts changed)';
    END IF;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-226-%') THEN
        RAISE EXCEPTION '226 self-test left fake auth.users rows behind';
    END IF;
    IF (SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), '')) FROM public.promo_batches b) IS DISTINCT FROM v_batches0
       OR (SELECT md5(COALESCE(string_agg(p::text, '|' ORDER BY p.plan_id), '')) FROM public.plans p) IS DISTINCT FROM v_plans0
       OR (SELECT md5(c::text) FROM public.early_adopter_campaign c WHERE c.id) IS DISTINCT FROM v_camp0 THEN
        RAISE EXCEPTION '226 self-test did not restore plans / batches / switches';
    END IF;
END
$selftest$;


-- ════════════════════════════════════════════════════════════════════════════
-- 6. user_subscriptions: the scheduled plan and its PROMISE (§8-6) — LAST
-- ════════════════════════════════════════════════════════════════════════════
-- One statement, one ACCESS EXCLUSIVE acquisition. The CHECK keeps the four
-- columns in step: they are set together and cleared together, or a write
-- fails (owner 2026-11-20: the promise is part of the schedule, never apart).
ALTER TABLE public.user_subscriptions
    ADD COLUMN IF NOT EXISTS renewal_plan_id           text          NULL REFERENCES public.plans (plan_id),
    ADD COLUMN IF NOT EXISTS renewal_plan_set_at       timestamptz   NULL,
    ADD COLUMN IF NOT EXISTS renewal_plan_price_sar    numeric(10,2) NULL,
    ADD COLUMN IF NOT EXISTS renewal_plan_batch_id     text          NULL REFERENCES public.promo_batches (batch_id),
    ADD COLUMN IF NOT EXISTS renewal_plan_promo_months integer       NULL,
    DROP CONSTRAINT IF EXISTS user_subscriptions_renewal_plan_terms_check,
    ADD  CONSTRAINT user_subscriptions_renewal_plan_terms_check CHECK (
            (    renewal_plan_id           IS NULL
             AND renewal_plan_price_sar    IS NULL
             AND renewal_plan_batch_id     IS NULL
             AND renewal_plan_promo_months IS NULL)
         OR (    renewal_plan_id           IS NOT NULL
             AND renewal_plan_price_sar    IS NOT NULL
             AND renewal_plan_price_sar    > 0
             AND (renewal_plan_batch_id IS NULL) = (renewal_plan_promo_months IS NULL)
             AND (renewal_plan_promo_months IS NULL OR renewal_plan_promo_months BETWEEN 1 AND 12))
    );

COMMENT ON COLUMN public.user_subscriptions.renewal_plan_id IS
'Scheduled downgrade (226, plan §4): the plan the NEXT renewal switches to. NULL = renew the current plan. Set and cleared TOGETHER with renewal_plan_price_sar / renewal_plan_batch_id / renewal_plan_promo_months (CHECK user_subscriptions_renewal_plan_terms_check). Written by the backend only (service role; clients have no write policy). Cleared by cancel, reactivate, any upgrade / re-purchase, and the renewal that applies it — in Python, never inside grant_plan. Not read by any SQL function or by user_subscriptions_live.';
COMMENT ON COLUMN public.user_subscriptions.renewal_plan_set_at IS
'When renewal_plan_id was last set (226). Audit / UI only.';
COMMENT ON COLUMN public.user_subscriptions.renewal_plan_price_sar IS
'The PROMISED first charge of the scheduled plan, VAT-inclusive SAR, quoted when the downgrade was scheduled (promo_checkout_price(user, plan, p_as_new => true)). Owner 2026-11-20: the renewal charges this, or the renewal-day fresh price if lower, even if the plan is off sale or the batch is full / disabled by then. NOT NULL and > 0 iff renewal_plan_id is set.';
COMMENT ON COLUMN public.user_subscriptions.renewal_plan_batch_id IS
'The offer batch the promised price came from; NULL = promised at list price. The renewal payment is stamped with it (promo_batch_id) and claim_early_adopter_seat seats it whether the batch is still enabled, disabled or full (over_capacity) — the promise is the quote. Service-side only: never send a batch id to a client.';
COMMENT ON COLUMN public.user_subscriptions.renewal_plan_promo_months IS
'The promised offer''s months (1–12), stamped into the renewal payment''s intro_offer_terms.offer_months; NOT NULL iff renewal_plan_batch_id is set.';

DO $us$
DECLARE
    v_s    TEXT;
    v_n    BIGINT;
    v_auth UUID;
    v_id   UUID;
    v_exp  TIMESTAMPTZ;
    v_ok   BOOLEAN;
    v_us0  RECORD;
    v_case RECORD;
BEGIN
    -- shape
    SELECT string_agg(format('%s:%s:%s', c.column_name, format_type(a.atttypid, a.atttypmod), c.is_nullable),
                      ',' ORDER BY c.column_name)
      INTO v_s
      FROM information_schema.columns c
      JOIN pg_attribute a ON a.attrelid = 'public.user_subscriptions'::regclass AND a.attname = c.column_name
     WHERE c.table_schema = 'public' AND c.table_name = 'user_subscriptions'
       AND c.column_name LIKE 'renewal\_plan\_%';
    IF v_s IS DISTINCT FROM 'renewal_plan_batch_id:text:YES,renewal_plan_id:text:YES,'
                            'renewal_plan_price_sar:numeric(10,2):YES,renewal_plan_promo_months:integer:YES,'
                            'renewal_plan_set_at:timestamp with time zone:YES' THEN
        RAISE EXCEPTION '226 us: columns are %', v_s;
    END IF;
    SELECT count(*) INTO v_n
      FROM pg_constraint c
     WHERE c.conrelid = 'public.user_subscriptions'::regclass
       AND c.contype = 'f'
       AND ((c.confrelid = 'public.plans'::regclass
             AND c.conkey = ARRAY[(SELECT a.attnum FROM pg_attribute a
                                    WHERE a.attrelid = 'public.user_subscriptions'::regclass
                                      AND a.attname = 'renewal_plan_id')]::smallint[])
         OR (c.confrelid = 'public.promo_batches'::regclass
             AND c.conkey = ARRAY[(SELECT a.attnum FROM pg_attribute a
                                    WHERE a.attrelid = 'public.user_subscriptions'::regclass
                                      AND a.attname = 'renewal_plan_batch_id')]::smallint[]));
    IF v_n <> 2 THEN
        RAISE EXCEPTION '226 us: % of the 2 FKs (renewal_plan_id → plans, renewal_plan_batch_id → promo_batches)', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                    WHERE c.conrelid = 'public.user_subscriptions'::regclass
                      AND c.conname = 'user_subscriptions_renewal_plan_terms_check'
                      AND c.contype = 'c' AND c.convalidated) THEN
        RAISE EXCEPTION '226 us: user_subscriptions_renewal_plan_terms_check missing';
    END IF;

    -- nothing else on the table moved: grants, RLS, policies, the live view
    SELECT * INTO v_us0 FROM _m226_us0;
    IF (SELECT c.relacl::text FROM pg_class c WHERE c.oid = 'public.user_subscriptions'::regclass) IS DISTINCT FROM v_us0.acl
       OR (SELECT c.relrowsecurity FROM pg_class c WHERE c.oid = 'public.user_subscriptions'::regclass) IS DISTINCT FROM v_us0.rls
       OR NOT v_us0.rls
       OR (SELECT c.relforcerowsecurity FROM pg_class c WHERE c.oid = 'public.user_subscriptions'::regclass) IS DISTINCT FROM v_us0.force_rls
       OR (SELECT string_agg(format('%s|%s|%s|%s|%s', p.polname, p.polcmd,
                                    pg_get_expr(p.polqual, p.polrelid),
                                    pg_get_expr(p.polwithcheck, p.polrelid),
                                    p.polroles::regrole[]::text), ';' ORDER BY p.polname)
             FROM pg_policy p WHERE p.polrelid = 'public.user_subscriptions'::regclass) IS DISTINCT FROM v_us0.policies
       OR md5(pg_get_viewdef('public.user_subscriptions_live'::regclass)) IS DISTINCT FROM v_us0.view_md5
       OR (SELECT count(*) FROM information_schema.columns ic
            WHERE ic.table_schema = 'public' AND ic.table_name = 'user_subscriptions_live') IS DISTINCT FROM v_us0.view_cols THEN
        RAISE EXCEPTION '226 us: grants / RLS / policies / user_subscriptions_live changed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policy p
                WHERE p.polrelid = 'public.user_subscriptions'::regclass AND p.polcmd <> 'r') THEN
        RAISE EXCEPTION '226 us: a client write policy exists on user_subscriptions';
    END IF;

    -- behaviour, on a fake account, rolled back
    BEGIN
        v_auth := gen_random_uuid();
        INSERT INTO auth.users (id, instance_id, aud, role, email,
                                raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
        VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
                format('selftest-226b-%s@selftest.invalid', replace(gen_random_uuid()::text, '-', '')),
                '{}'::jsonb, '{}'::jsonb, now(), now());
        SELECT u.user_id INTO v_id FROM public.users u WHERE u.auth_id = v_auth;
        UPDATE public.user_subscriptions s SET plan_id = 'max' WHERE s.user_id = v_id;
        SELECT s.expires_at INTO v_exp FROM public.user_subscriptions s WHERE s.user_id = v_id;

        -- a new row starts with nothing scheduled
        IF (SELECT s.renewal_plan_id IS NOT NULL OR s.renewal_plan_set_at IS NOT NULL
                   OR s.renewal_plan_price_sar IS NOT NULL OR s.renewal_plan_batch_id IS NOT NULL
                   OR s.renewal_plan_promo_months IS NOT NULL
              FROM public.user_subscriptions s WHERE s.user_id = v_id) THEN
            RAISE EXCEPTION '226 us: a new subscription row has a scheduled plan';
        END IF;

        -- schedule pro_plus WITH its promise (the offer): the plan / term are
        -- untouched (the assignment trigger fires on plan_id only)
        UPDATE public.user_subscriptions s
           SET renewal_plan_id = 'pro_plus', renewal_plan_set_at = now(),
               renewal_plan_price_sar = 99.90, renewal_plan_batch_id = 'pro_plus_t1_a',
               renewal_plan_promo_months = 2
         WHERE s.user_id = v_id;
        IF (SELECT format('%s|%s|%s|%s|%s|%s', s.plan_id, s.renewal_plan_id, s.renewal_plan_price_sar,
                          s.renewal_plan_batch_id, s.renewal_plan_promo_months, s.expires_at = v_exp)
              FROM public.user_subscriptions s WHERE s.user_id = v_id)
           IS DISTINCT FROM 'max|pro_plus|99.90|pro_plus_t1_a|2|t' THEN
            RAISE EXCEPTION '226 us: scheduling with an offer promise failed or changed the running term';
        END IF;
        -- re-schedule at LIST price (no batch, no months)
        UPDATE public.user_subscriptions s
           SET renewal_plan_id = 'pro', renewal_plan_price_sar = 94.90,
               renewal_plan_batch_id = NULL, renewal_plan_promo_months = NULL
         WHERE s.user_id = v_id;

        -- the CHECK refuses every half-written state
        FOR v_case IN
            SELECT *
              FROM (VALUES
                    ('plan without a price',          'pro'::text,  NULL::numeric, NULL::text,      NULL::integer),
                    ('price 0',                       'pro',        0.00,          NULL,            NULL),
                    ('negative price',                'pro',        -1.00,         NULL,            NULL),
                    ('batch without months',          'pro_plus',   99.90,         'pro_plus_t1_a', NULL),
                    ('months without a batch',        'pro_plus',   99.90,         NULL,            2),
                    ('months 13',                     'pro_plus',   99.90,         'pro_plus_t1_a', 13),
                    ('price left after clearing',     NULL,         94.90,         NULL,            NULL),
                    ('batch left after clearing',     NULL,         NULL,          'pro_plus_t1_a', 2)
                   ) v(label, pid, price, batch, months)
        LOOP
            v_ok := false;
            BEGIN
                UPDATE public.user_subscriptions s
                   SET renewal_plan_id = v_case.pid, renewal_plan_price_sar = v_case.price,
                       renewal_plan_batch_id = v_case.batch, renewal_plan_promo_months = v_case.months
                 WHERE s.user_id = v_id;
            EXCEPTION WHEN check_violation THEN
                v_ok := true;
            END;
            IF NOT v_ok THEN
                RAISE EXCEPTION '226 us: user_subscriptions_renewal_plan_terms_check accepted "%"', v_case.label;
            END IF;
        END LOOP;

        -- an unknown plan / batch is refused by the FKs
        v_ok := false;
        BEGIN
            UPDATE public.user_subscriptions s
               SET renewal_plan_id = 'no_such_plan', renewal_plan_price_sar = 94.90,
                   renewal_plan_batch_id = NULL, renewal_plan_promo_months = NULL
             WHERE s.user_id = v_id;
        EXCEPTION WHEN foreign_key_violation THEN
            v_ok := true;
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION '226 us: renewal_plan_id accepted an unknown plan';
        END IF;
        v_ok := false;
        BEGIN
            UPDATE public.user_subscriptions s
               SET renewal_plan_id = 'pro_plus', renewal_plan_price_sar = 99.90,
                   renewal_plan_batch_id = 'no_such_batch', renewal_plan_promo_months = 2
             WHERE s.user_id = v_id;
        EXCEPTION WHEN foreign_key_violation THEN
            v_ok := true;
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION '226 us: renewal_plan_batch_id accepted an unknown batch';
        END IF;

        -- clearing all four together is accepted
        UPDATE public.user_subscriptions s
           SET renewal_plan_id = NULL, renewal_plan_set_at = NULL, renewal_plan_price_sar = NULL,
               renewal_plan_batch_id = NULL, renewal_plan_promo_months = NULL
         WHERE s.user_id = v_id;

        -- the live view still answers for the row
        IF (SELECT l.effective_plan_id FROM public.user_subscriptions_live l WHERE l.user_id = v_id) IS DISTINCT FROM 'max' THEN
            RAISE EXCEPTION '226 us: user_subscriptions_live broke';
        END IF;

        RAISE EXCEPTION USING ERRCODE = 'LU226', MESSAGE = '226 us sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU226' THEN
        NULL;
    END;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-226b-%') THEN
        RAISE EXCEPTION '226 us: fake auth.users row left behind';
    END IF;

    SELECT count(*) INTO v_n FROM public.user_subscriptions s WHERE s.renewal_plan_id IS NOT NULL;
    RAISE NOTICE '226 us: OK — renewal_plan_id text → plans, renewal_plan_set_at timestamptz, renewal_plan_price_sar numeric(10,2), renewal_plan_batch_id text → promo_batches, renewal_plan_promo_months integer (all NULL); user_subscriptions_renewal_plan_terms_check refuses 8 half-written states, accepts offer / list / cleared; FKs refuse unknown plan / batch; the running term is untouched; grants / RLS (1 self-row SELECT policy) / user_subscriptions_live unchanged; % row(s) scheduled', v_n;
END
$us$;


-- ════════════════════════════════════════════════════════════════════════════
-- 7. ONE renewal charge per period, whatever the plan (review item 2)
-- ════════════════════════════════════════════════════════════════════════════
-- 132's uniq_payment_renewal_period is (user_id, plan_id, period_start): since
-- 226 one period can be attempted under two plans (the current one, and the
-- scheduled one), so two ticks / workers could charge the same period twice,
-- once per plan. This index closes it; 132's stays (a strict subset — the
-- backend matches 23505 by SQLSTATE / "duplicate key", never by index name).
-- Same predicate and style as 132. Prod on 2026-11-20: 192 rows (14 renewal),
-- no (user_id, period_start) with two non-failed renewal rows.
-- LOCK: a non-concurrent build takes SHARE on payment_transactions (blocks
-- writes — checkouts, webhooks, renewals — not reads) until COMMIT, so it is
-- the LAST thing in this file: milliseconds at this size.
DO $idx_guard$
DECLARE
    v_s TEXT;
BEGIN
    SELECT string_agg(format('%s @ %s: %s', x.user_id, x.period_start, x.rows), ' ‖ ')
      INTO v_s
      FROM (SELECT t.user_id, t.period_start,
                   string_agg(format('%s %s %s', t.payment_id, t.plan_id, t.status), ', ') AS rows
              FROM public.payment_transactions t
             WHERE t.initiated_by = 'renewal' AND t.status <> 'failed'
               AND t.user_id IS NOT NULL AND t.period_start IS NOT NULL
             GROUP BY t.user_id, t.period_start
            HAVING count(*) > 1) x;
    IF v_s IS NOT NULL THEN
        RAISE EXCEPTION '226: renewal rows already share a period across plans — resolve before applying: %', v_s;
    END IF;
END
$idx_guard$;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_renewal_period_any_plan
    ON public.payment_transactions USING btree (user_id, period_start)
 WHERE ((initiated_by = 'renewal'::text) AND (status <> 'failed'::text)
        AND (user_id IS NOT NULL) AND (period_start IS NOT NULL));

COMMENT ON INDEX public.uniq_payment_renewal_period_any_plan IS
'THE cross-plan double-charge guard (226). At most ONE non-failed renewal charge per (user, period_start), whatever plan it charges — a scheduled downgrade lets one period be attempted under the current plan or the scheduled one, which 132''s per-plan uniq_payment_renewal_period cannot see. Only status = ''failed'' releases the slot (the dunning ladder retries under either plan). The backend''s renewal pre-filter must match it: same period, any plan.';

DO $idx$
DECLARE
    v_s    TEXT;
    v_auth UUID;
    v_id   UUID;
    v_p    TIMESTAMPTZ := date_trunc('second', now()) + interval '7 days';
    v_ok   BOOLEAN;
BEGIN
    SELECT format('%s|%s|%s', i.indisunique, i.indisvalid, pg_get_indexdef(i.indexrelid))
      INTO v_s
      FROM pg_index i
     WHERE i.indexrelid = 'public.uniq_payment_renewal_period_any_plan'::regclass;
    IF v_s IS DISTINCT FROM
       't|t|CREATE UNIQUE INDEX uniq_payment_renewal_period_any_plan ON public.payment_transactions USING btree (user_id, period_start) WHERE ((initiated_by = ''renewal''::text) AND (status <> ''failed''::text) AND (user_id IS NOT NULL) AND (period_start IS NOT NULL))' THEN
        RAISE EXCEPTION '226 idx: %', v_s;
    END IF;
    IF to_regclass('public.uniq_payment_renewal_period') IS NULL THEN
        RAISE EXCEPTION '226 idx: 132''s uniq_payment_renewal_period is gone';
    END IF;

    BEGIN
        v_auth := gen_random_uuid();
        INSERT INTO auth.users (id, instance_id, aud, role, email,
                                raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
        VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
                format('selftest-226c-%s@selftest.invalid', replace(gen_random_uuid()::text, '-', '')),
                '{}'::jsonb, '{}'::jsonb, now(), now());
        SELECT u.user_id INTO v_id FROM public.users u WHERE u.auth_id = v_auth;

        -- period P charged under max …
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, initiated_by, renewal_attempt, period_start)
        VALUES (v_id, 'max', 219.90, 'paid', 'renewal', 0, v_p);
        -- … a second attempt for P under the scheduled plan is refused
        v_ok := false;
        BEGIN
            INSERT INTO public.payment_transactions
                (user_id, plan_id, amount_sar, status, initiated_by, renewal_attempt, period_start)
            VALUES (v_id, 'pro_plus', 149.90, 'initiated', 'renewal', 0, v_p);
        EXCEPTION WHEN unique_violation THEN
            v_ok := true;
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION '226 idx: a second plan charged the same renewal period';
        END IF;
        -- a FAILED attempt never holds the slot; another period is free
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, initiated_by, renewal_attempt, period_start)
        VALUES (v_id, 'pro_plus', 149.90, 'failed', 'renewal', 1, v_p),
               (v_id, 'pro_plus', 149.90, 'initiated', 'renewal', 0, v_p + interval '30 days');

        RAISE EXCEPTION USING ERRCODE = 'LU226', MESSAGE = '226 idx sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU226' THEN
        NULL;
    END;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-226c-%') THEN
        RAISE EXCEPTION '226 idx: fake auth.users row left behind';
    END IF;

    RAISE NOTICE '226 idx: OK — uniq_payment_renewal_period_any_plan (user_id, period_start) WHERE renewal AND status <> failed, valid; a second plan for the same period → 23505; a failed attempt and another period are allowed; 132''s per-plan index kept';
END
$idx$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY CHECKS — run manually
-- ════════════════════════════════════════════════════════════════════════════
-- SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = 'public.early_adopter_seats'::regclass AND conname LIKE '%release%';
-- SELECT column_name, data_type, is_nullable FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'user_subscriptions'
--    AND column_name LIKE 'renewal_plan%';
-- Superseded seats waiting on a refund (V5), now either direction:
-- SELECT s.seat_id, s.user_id, s.release_reason, s.promo_ends_at, s.released_by_payment_id, t.status
--   FROM public.early_adopter_seats s
--   JOIN public.payment_transactions t ON t.payment_id = s.released_by_payment_id
--  WHERE s.release_reason IN ('upgraded', 'downgraded') AND s.promo_ends_at > now();
