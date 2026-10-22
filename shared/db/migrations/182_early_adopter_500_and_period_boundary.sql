-- ════════════════════════════════════════════════════════════════════════════
-- 182 — early adopters: 500 seats + price a renewal at the START of its period
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/profession_intro_offer.md §2 (Phase 0 — ships before, and
--       independently of, the intro offer).
-- Owner decisions 2026-10-21:
--   * «المشتركون الأوائل» grows from 100 to 500 seats — for the WHOLE campaign,
--     not only the profession intro offer that will ride on it (183).
--   * A seat holder pays the promo price for exactly the periods that BEGIN
--     inside the 90-day window — three charges (day 0, 30, 60), then list.
-- Depends on: 138 (early_adopter_campaign, early_adopter_seats,
--             effective_plan_price, early_adopter_open).
-- Idempotent: guarded UPDATE, DROP … IF EXISTS + CREATE OR REPLACE, re-runnable
--             REVOKE/GRANT. Re-running BEFORE 183 is a no-op; AFTER 183 it
--             refuses (§0) rather than strip 183's forfeiture exemption.
--
-- ── 1. THE BUG THIS FIXES ───────────────────────────────────────────────────
-- 138's effective_plan_price prices a seat holder at the promo while
--
--     s.promo_ends_at > now()
--
-- but the renewal job (renewal_service.py, DUE_HORIZON = 24h) charges a term
-- up to 24 hours BEFORE it expires. The charge for the period that BEGINS on
-- day 90 therefore runs on ~day 89, when the seat is still live, and is billed
-- at the promo price. Every seat holder gets FOUR promo charges, not the three
-- that early_adopters.md §1.3 ("the first period that BEGINS after the window
-- is full price") and /promo-terms §2 promise.
--
-- First seat to hit it: claimed 2026-08-18 19:31Z → promo_ends_at 2026-11-16
-- 19:31Z → its day-90 renewal runs ≈ 2026-11-15 03:30Z. This migration and the
-- backend that sends p_at must both be live before then.
--
-- THE FIX: a new optional argument p_at — "the instant being priced". The
-- renewal job passes the term's expires_at (= the new period's start); every
-- other caller omits it and keeps now(). Only branch (b) reads it:
--
--     s.promo_ends_at > COALESCE(p_at, now())
--
-- Boundary, worked: promo_ends_at = paid_at + 90d (claim_early_adopter_seat
-- anchors on paid_at). The third renewal's period_start = fulfilled_at + 90d,
-- and paid_at is stamped BEFORE grant_plan runs, so period_start ≥
-- promo_ends_at → not inside → list price. The day-30 and day-60 periods are
-- inside → promo. Exactly three promo charges.
--
-- Branch (a) (forfeiture) and branch (c) ('purchase' while seats are open)
-- deliberately keep now(): a forfeiture is a fact about the user, not about a
-- period, and a purchase is a decision made now.
--
-- ── 2. WHY DROP + CREATE, NOT CREATE OR REPLACE ─────────────────────────────
-- CREATE OR REPLACE with a 4th parameter does not replace anything — it creates
-- a SECOND overload, effective_plan_price(uuid,text,text,timestamptz), beside
-- the old (uuid,text,text). The backend calls it through PostgREST with NAMED
-- arguments, and a 3-named-arg call is then ambiguous between
-- (uuid,text,text) and (uuid,text,text,timestamptz DEFAULT NULL): PostgREST
-- answers PGRST203 "could not choose the best candidate function", and
-- payment_service._effective_price falls back to the CATALOG — every seat
-- holder's renewal and every promo checkout at list price. So the old signature
-- is DROPPED first, inside the same transaction, and the new one is the only
-- one that exists.
--
-- No SQL object depends on the old signature (verified live 2026-10-21): no
-- view references effective_plan_price, and the only function bodies that
-- mention it (claim_early_adopter_seat, early_adopter_status) do so in comments.
-- A plpgsql 3-argument call would resolve to the new function via the default
-- anyway. DROP removes 138's COMMENT and grants, so both are re-applied below.
--
-- ── 3. DEPLOY ORDER: THIS MIGRATION FIRST, THEN THE BACKEND ─────────────────
--   * Old backend + this migration: its 3-named-arg call resolves to the new
--     function (p_at defaults to NULL → now()). Behaviour unchanged. SAFE.
--   * New backend + OLD function: the 4th named arg (p_at) matches no
--     signature → the RPC fails → _effective_price falls back to the catalog →
--     seat holders charged LIST price at renewal. NOT SAFE.
-- So: apply 182, verify, then push the backend that sends p_at.
--
-- ── 4. 100 → 500 SEATS ──────────────────────────────────────────────────────
-- A plain data UPDATE on the singleton row. Nothing in code reads 100;
-- /promo-terms and product_docs.pricing never state a count (verified
-- 2026-10-21) and must never start (early_adopters.md §1.10). Guarded on the
-- old value so a later manual change (e.g. to 750) is never silently reverted
-- by a re-run.
--
-- Body of effective_plan_price below = the LIVE body read with
-- pg_get_functiondef on 2026-10-21 (logic identical to 138's file; the live
-- body carries no inline comments), with ONLY the signature and branch (b)
-- changed.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 0. never after 183 ──────────────────────────────────────────────────────
-- 183 CREATE OR REPLACEs this same 4-arg function with the intro-offer
-- forfeiture exemption. Re-running 182 afterwards would silently strip it.
DO $$
BEGIN
    IF to_regprocedure('public.is_intro_offer_member(uuid)') IS NOT NULL THEN
        RAISE EXCEPTION
            '182: 183 is already applied — re-running 182 would remove its '
            'forfeiture exemption from effective_plan_price. Nothing changed.';
    END IF;
END $$;

-- ── 1. seats ────────────────────────────────────────────────────────────────
UPDATE public.early_adopter_campaign
   SET seat_limit = 500
 WHERE id
   AND seat_limit = 100;

-- ── 2. effective_plan_price(…, p_at) ────────────────────────────────────────
DROP FUNCTION IF EXISTS public.effective_plan_price(uuid, text, text);

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
        -- (a) forfeiture — unchanged.
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.release_reason = 'cancelled') THEN
            RETURN round(v_plan.price_sar, 2);
        END IF;

        -- (b) THE PROMISE — priced at the instant being charged FOR (182).
        -- The renewal job passes the new period's start; a period that begins
        -- at or after promo_ends_at is full price even when it is charged a day
        -- early. Everyone else passes nothing and gets now().
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.released_at IS NULL
                      AND s.promo_ends_at > COALESCE(p_at, now())) THEN
            RETURN round(v_plan.promo_price_sar, 2);
        END IF;

        -- (c) 'purchase' only, while seats remain — unchanged.
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
    'THE price of a plan FOR A GIVEN USER (138, p_at added by 182). '
    'p_context: ''purchase'' (default) = what this plan would cost if bought now '
    '(create_checkout); ''current'' = what this user is already being charged '
    '(renewal job, _upgrade_credit pricing the OLD plan). DO NOT COLLAPSE THE TWO '
    '(see 138). p_at (182) = the instant being priced; NULL = now(). The renewal '
    'job passes the term''s expires_at — the START of the period it is charging '
    'for — so a seat holder pays the promo for exactly the periods that BEGIN '
    'inside the 90-day window (day 0, 30, 60) and list from day 90, even though '
    'renewals run up to 24h early. Only the live-seat branch reads p_at; the '
    'forfeiture branch and the ''purchase'' open-campaign branch use now(). '
    'Returns price_sar ALWAYS for a user with a seat released ''cancelled'' '
    '(§1 rule 6). NULL p_user_id = no seat, no forfeiture. Unknown p_context '
    'RAISES 22023. Rounded to 2dp. Service-role only. ⚠ ONE SIGNATURE ONLY — a '
    'second overload makes PostgREST named-arg calls ambiguous (PGRST203) and the '
    'backend then silently charges the catalog price.';

REVOKE EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz)
    TO service_role;

-- ── 3. verification — any failure aborts the whole migration ────────────────
DO $$
DECLARE
    v_limit    INTEGER;
    v_overload INTEGER;
    v_nargs    INTEGER;
    v_seat     RECORD;
    v_promo    NUMERIC;
    v_list     NUMERIC;
    v_got      NUMERIC;
BEGIN
    SELECT c.seat_limit INTO v_limit FROM public.early_adopter_campaign c WHERE c.id;
    IF v_limit IS DISTINCT FROM 500 THEN
        RAISE EXCEPTION '182: early_adopter_campaign.seat_limit is %, expected 500', v_limit;
    END IF;

    SELECT count(*), max(p.pronargs)
      INTO v_overload, v_nargs
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
    IF v_overload <> 1 OR v_nargs <> 4 THEN
        RAISE EXCEPTION
            '182: expected exactly ONE effective_plan_price with 4 args, found % overload(s), max nargs %',
            v_overload, v_nargs;
    END IF;

    IF has_function_privilege('anon',
            'public.effective_plan_price(uuid, text, text, timestamptz)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.effective_plan_price(uuid, text, text, timestamptz)', 'EXECUTE') THEN
        RAISE EXCEPTION '182: anon/authenticated can EXECUTE effective_plan_price';
    END IF;
    IF NOT has_function_privilege('service_role',
            'public.effective_plan_price(uuid, text, text, timestamptz)', 'EXECUTE') THEN
        RAISE EXCEPTION '182: service_role lost EXECUTE on effective_plan_price';
    END IF;

    -- The boundary itself, against a real seat (skipped if none qualifies).
    -- A live seat whose user has no 'cancelled' seat (branch (a) would answer
    -- list for every p_at and prove nothing).
    SELECT p.promo_price_sar, p.price_sar INTO v_promo, v_list
      FROM public.plans p WHERE p.plan_id = 'pro';

    SELECT s.user_id, s.promo_ends_at
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.released_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM public.early_adopter_seats c
                        WHERE c.user_id = s.user_id
                          AND c.release_reason = 'cancelled')
     ORDER BY s.claimed_at
     LIMIT 1;

    IF FOUND AND v_promo IS NOT NULL THEN
        v_got := public.effective_plan_price(v_seat.user_id, 'pro', 'current',
                                             v_seat.promo_ends_at - interval '1 hour');
        IF v_got IS DISTINCT FROM round(v_promo, 2) THEN
            RAISE EXCEPTION '182: inside the window priced % (expected promo %)', v_got, v_promo;
        END IF;

        v_got := public.effective_plan_price(v_seat.user_id, 'pro', 'current',
                                             v_seat.promo_ends_at);
        IF v_got IS DISTINCT FROM round(v_list, 2) THEN
            RAISE EXCEPTION '182: period starting AT promo_ends_at priced % (expected list %)',
                            v_got, v_list;
        END IF;

        -- NULL p_at must still mean now() (every non-renewal caller).
        IF v_seat.promo_ends_at > now() THEN
            v_got := public.effective_plan_price(v_seat.user_id, 'pro', 'current');
            IF v_got IS DISTINCT FROM round(v_promo, 2) THEN
                RAISE EXCEPTION '182: 3-arg call (p_at NULL) priced % (expected promo %)',
                                v_got, v_promo;
            END IF;
        END IF;
    ELSE
        RAISE NOTICE '182: no qualifying live seat — boundary self-test skipped';
    END IF;

    RAISE NOTICE '182: OK — 500 seats, one 4-arg effective_plan_price, boundary verified';
END $$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY CHECKS — run manually
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. One signature, service_role only. EXPECT one row, 4 args.
-- SELECT p.oid::regprocedure, p.proacl
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
--
-- -- 2. Every live seat: day-29/59 renewals promo, day-89 renewal (period
-- --    starting at promo_ends_at) list. EXPECT inside = promo, at_end = list
-- --    on every row without a cancelled seat.
-- SELECT s.user_id, s.promo_ends_at,
--        public.effective_plan_price(s.user_id, 'pro', 'current',
--                                    s.promo_ends_at - interval '30 days') AS inside,
--        public.effective_plan_price(s.user_id, 'pro', 'current',
--                                    s.promo_ends_at)                     AS at_end
--   FROM public.early_adopter_seats s WHERE s.released_at IS NULL;
--
-- -- 3. Seats. EXPECT seat_limit = 500.
-- SELECT * FROM public.early_adopter_campaign;
--
-- ── ROLLBACK ─────────────────────────────────────────────────────────────────
-- -- Only after rolling the backend back first (the new backend sends p_at):
-- --   DROP FUNCTION public.effective_plan_price(uuid, text, text, timestamptz);
-- --   then re-run 138's CREATE FUNCTION + COMMENT + REVOKE/GRANT block.
-- --   UPDATE public.early_adopter_campaign SET seat_limit = 100 WHERE id;
