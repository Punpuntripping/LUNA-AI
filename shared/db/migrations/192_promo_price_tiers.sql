-- ════════════════════════════════════════════════════════════════════════════
-- 192 — price tiers + seat batches («pricing campaigns v2»), behind ladder_v2
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/pricing_campaigns_v2.md §1 (rules), §2 (this file), §6
--       (rollout). Owner decisions 2026-10-26/27.
-- Depends on: 068/092 (plans, payment_transactions), 113/119/132 (payment
--             columns), 115 + 183 §4b (users.profession_first_group), 120 +
--             129/129a (user_subscriptions_live — a security_invoker view,
--             READ here and never rebuilt), 126 (product_docs), 138 + 182 + 183
--             (seats, campaign, the 4-arg effective_plan_price, the intro
--             offer). ⚠ §0 refuses to run before 183.
-- Idempotent: IF NOT EXISTS / DROP … IF EXISTS + ADD / ON CONFLICT DO NOTHING /
--             CREATE OR REPLACE on unchanged signatures / md5-guarded doc
--             UPDATE. Re-runnable, including after the flip (§9 notes).
--
-- ── WHY ─────────────────────────────────────────────────────────────────────
-- One promo price per plan (plans.promo_price_sar, 138) and one 90-day window
-- (early_adopter_campaign.promo_days) can no longer describe what the owner
-- sells: two price tiers per plan, each sold in BATCHES of seats, each batch
-- with its own price and number of months (§1.1–1.2), quoted per user (§1.3 —
-- each tier once per plan, Pro Tier 1 only for three professions and never
-- while Basic runs, past subscribers welcome). The numbers changed six times in
-- two days, so they are DATA: promo_batches, one row per batch, changed with an
-- UPDATE and no deploy.
--
-- A seat FREEZES its terms (§1.4-3): batch, plan, tier, price and window are
-- copied onto the seat when it is claimed, so editing a batch never re-prices a
-- held seat. The seats that exist before this file (10 live on 2026-10-26: 6
-- pro @ 49.90 and 4 max @ 99.90, 90 days each) are LEGACY seats (batch_id
-- NULL). They stay priced from plans.promo_price_sar exactly as today, which
-- is why that column is now FROZEN (§5 comments it, §8 asserts it).
--
-- ── SHIPS INERT ─────────────────────────────────────────────────────────────
-- early_adopter_campaign.ladder_v2 = false. With the flag down:
--   * promo_quote returns nothing for anybody;
--   * effective_plan_price, claim_early_adopter_seat, early_adopter_open,
--     intro_offer_quote, early_adopter_status and promo_public_quote all take
--     today's (183's) path;
--   * the flag-independent pricing changes (effective_plan_price's batch-seat
--     branch, and the give-back in release_early_adopter_seat) only act on
--     batch seats / 'upgraded' seats, which can only be minted from a payment
--     stamped with promo_batch_id — which the backend stamps only from a
--     promo_quote row, and there are none until the flip;
--   * promo_checkout_price with the flag down IS effective_plan_price;
--   * the two flag-independent non-pricing changes: release_early_adopter_seat
--     now takes the claim's advisory lock, and (V7) client roles lose SELECT on
--     three payment_transactions columns (anon: on the table) — no client reads
--     it.
-- Applying this file therefore changes behaviour for ZERO users. Launch:
--     UPDATE public.early_adopter_campaign SET ladder_v2 = true WHERE id;
-- Rollback = the same row back to false. Held batch seats keep their own price
-- to the end of their window: that branch ignores the flag on purpose (gating
-- it would charge a 19.90 seat its list price in month 2).
--
-- ── DEPLOY ORDER ────────────────────────────────────────────────────────────
-- 192 → backend + frontend (they select payment_transactions.promo_batch_id; a
-- missing column 42703s checkout AND /history for everybody — M182:68-74) →
-- verify → flip. ⚠ NEVER FLIP BEFORE THE NEW BACKEND IS LIVE. The old backend
-- does not stamp promo_batch_id, so with the flag up it would charge 59.90 /
-- 149.90 / 189.90 with no seat behind it (month 2 at list) and keep advertising
-- 49.90 / 99.90 on /payments/early-adopter. (A 19.90 no longer becomes a legacy
-- 90-day seat: the R2 guard refuses any unstamped promo-priced payment created
-- after the flip — but that customer still holds no seat.)
--
-- ── WHERE THIS FILE HAD TO INTERPRET THE CONTRACT ──────────────────────────
-- (I1) claim_early_adopter_seat keeps its REAL signature (p_user_id uuid,
--      p_payment_id uuid). The contract's "(p_payment_id uuid) — SAME
--      signature" is read as shorthand: subscription_service.py:807-811 calls
--      it with both named args, and a 1-arg overload would break that call.
-- (I2) promo_quote is ALSO empty while the user holds a LIVE seat still inside
--      its window that prices the plan asked for (someone who lapsed by
--      dunning, or whose card cannot renew, keeps a live seat — only refund /
--      cancel release one). A BATCH seat blocks only its own plan. A LEGACY seat
--      blocks EVERY plan (review fix 3: legacy holders keep their terms on the
--      other plan too, as before 192), with ONE exception — a legacy seat whose
--      payment plan is pro, quoting max: the owner-approved Pro → Max switch
--      (§1.3-6 / A3). effective_plan_price prices a blocked purchase from the
--      seat (branch b), so a batch quote would disagree with the charge and
--      overwrite a running promise. After the window ends the quote applies
--      normally and the claim releases the spent seat.
-- (I3) effective_plan_price, flag ON, 'purchase': a promo_quote row is applied
--      BEFORE the legacy-seat part of branch (b). Because of (I2) the quote can
--      only win over a live legacy seat in the A3 case — a legacy PRO seat
--      buying MAX takes the Max quote (149.90), not the legacy carry-over
--      (99.90). A legacy max holder buying pro gets no quote and keeps the
--      legacy 49.90. 'current' keeps the contract order: a legacy seat prices
--      any plan exactly as today, so a pre-flip carry-over (legacy pro seat,
--      now on max) keeps renewing at 99.90.
-- (I4) The v2 claim releases ('upgraded') ANY live seat that is not in the
--      payment's batch — legacy, other plan, or same plan / other batch (a spent
--      window that is still live). The contract names only the first two; a
--      same-plan seat left live would collide with early_adopter_seats_one_live
--      and the payer of the new batch would get no seat.
-- (I5) Flag OFF: the batch-seat branch (b1) runs BEFORE 183's forfeiture branch
--      and prices the seat's OWN plan at its frozen price. For the OTHER plan a
--      live batch seat carries over at plans.promo_price_sar through 182's
--      branch (b), exactly as every seat did before 192 (review fix 4), and a
--      live batch seat also suspends 183's forfeiture so that carry-over holds
--      for a user with an old 'cancelled' legacy row. Before the first flip no
--      batch seat exists, so this is today's behaviour exactly.
-- (I6) early_adopter_status: v_forfeit never applies to a live batch seat, nor
--      with the flag ON — has_seat must mean exactly what effective_plan_price
--      charges. Body-only change; RETURNS unchanged.
-- (I7) promo_quote honours promo_batches.profession_only: an open batch marked
--      profession_only is not offered outside the three groups. For the six
--      seeded rows this is exactly the contract's result.
-- (I8) promo_public_quote under the flag also requires campaign.enabled, like
--      promo_quote and early_adopter_open — /pricing must not advertise what
--      checkout refuses.
-- (I9) Extra guards beyond the contract: CHECK price_sar >= 1.00 on batches
--      (Moyasar's minimum charge, as 183 did for intro prices) and a seat CHECK
--      that a batch seat carries plan/tier/price.
--
-- ── REVISION 2026-10-27 (reconciled with the backend) ──────────────────────
-- (R1) promo_tier_used: a legacy seat whose claiming payment carries
--      intro_offer_group (a 183 first-month purchase at 1.99 / 9.90) counts as
--      BOTH (pro, 1) and (pro, 2) used — the profession offer was already had.
-- (R2) No new LEGACY seats after the flip. early_adopter_campaign.ladder_v2_at
--      is stamped by trigger on every false → true flip of ladder_v2 and never
--      cleared. On the legacy claim path (no promo_batch_id), while ladder_v2 is
--      on, a payment CREATED at or after ladder_v2_at gets no seat. Payments
--      created before the flip keep today's claim (the quote is the promise).
--      The check sits AFTER the legacy price gate, so a list-priced sale still
--      answers 'not_promo_priced' exactly as today; only an unstamped payment
--      priced at or below the legacy promo — a checkout bug after the flip —
--      answers 'plan_not_eligible' (the backend's ERROR branch) with a WARNING.
-- (R3) idx_early_adopter_seats_batch_live (batch_id) WHERE released_at IS NULL
--      for the backend's per-batch count (the 90% alert, via PostgREST).
-- (R4) payment_transactions.intro_offer_terms re-documented: every batch sale
--      carries it (offer_sar / offer_months); only Pro tier 1 rows carry
--      intro_offer_group.
--
-- ── REVISION 2026-10-27 (independent review) ───────────────────────────────
-- (V1) The v2 claim freezes the QUOTED terms: intro_offer_terms->>'offer_sar' /
--      'offer_months' drive the amount gate, the seat's price_sar and its
--      promo_ends_at; the batch row is only the fallback when those keys are
--      missing. An operator editing a batch between checkout and settlement no
--      longer changes what the customer bought.
-- (V2) promo_checkout_price(user, plan): ONE call, ONE snapshot → the price to
--      charge AND the batch / tier / months / intro group to stamp. Flag OFF it
--      is effective_plan_price('purchase'); flag ON it calls promo_quote once
--      and computes the no-quote fallback inline (never re-quoting).
-- (V3) = (I2) / (I3) above: legacy seats block quotes for every plan except the
--      legacy-pro → max switch.
-- (V4) = (I5) above: with the flag off a live batch seat carries over to the
--      other plan at plans.promo_price_sar.
-- (V5) Refunding the payment that claimed a seat via a switch gives the seat it
--      superseded back. early_adopter_seats.released_by_payment_id records which
--      payment released a seat 'upgraded'; release_early_adopter_seat (SAME
--      signature) restores that seat on a 'refund' release when the released
--      seat's own claiming payment is the refunded one (status 'refunded' — both
--      backend refund paths stamp it before releasing) and its window is open.
-- (V6) payment_transactions.credit_basis ('price' | 'cash', NULL = 'price'):
--      stamped by checkout when a credit applies, so settlement never re-derives
--      the basis from the flag.
-- (V7) Clients can no longer read promo_batch_id / intro_offer_terms /
--      credit_basis: table-level SELECT on payment_transactions is replaced by a
--      column list for authenticated (every column except those three), and
--      anon loses SELECT entirely. No client reads the table (verified: no
--      supabase-js data call or realtime subscription in frontend/ or mobile/).
--
-- ── REVISION 2026-10-27 (owner decision) ───────────────────────────────────
-- (O1) A REFUND is not having used a tier. promo_tier_used skips a seat released
--      'refund' whose OWN claiming payment is 'refunded' (the purchase itself
--      was refunded — as if never bought), batch and legacy seats alike. A seat
--      released 'refund' because a LATER renewal was refunded (its claiming
--      payment still 'paid') still counts, so refunding month 2 cannot re-open
--      the tier. Consequence with (V5): refunding a Pro → Max switch gives the
--      Pro seat back AND leaves Max tier open, as if the switch never happened.
--
-- ── LOCKS ───────────────────────────────────────────────────────────────────
-- ALTER TABLE takes ACCESS EXCLUSIVE on payment_transactions,
-- early_adopter_seats and early_adopter_campaign until COMMIT — including the
-- self-tests in §10, which take well under a second. lock_timeout fails fast
-- instead of queueing every checkout and webhook behind this transaction.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 0. 183 must be live; every function this file replaces must be unique ──
DO $$
DECLARE
    v_name TEXT;
    v_n    INTEGER;
BEGIN
    IF to_regprocedure('public.is_intro_offer_member(uuid)') IS NULL
       OR to_regclass('public.intro_offer_campaign') IS NULL
       OR to_regclass('public.early_adopter_campaign') IS NULL
       OR to_regclass('public.early_adopter_seats') IS NULL THEN
        RAISE EXCEPTION '192: apply 138, 182 and 183 first — nothing changed';
    END IF;

    -- CREATE OR REPLACE on a signature that does not exist CREATES a second
    -- overload — PGRST203 on every named-arg call (182 §2). Refuse instead.
    FOREACH v_name IN ARRAY ARRAY['effective_plan_price', 'claim_early_adopter_seat',
                                  'early_adopter_status', 'early_adopter_open',
                                  'intro_offer_quote', 'release_early_adopter_seat'] LOOP
        SELECT count(*) INTO v_n
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = v_name;
        IF v_n <> 1 THEN
            RAISE EXCEPTION '192: expected exactly one public.%, found % overload(s)', v_name, v_n;
        END IF;
    END LOOP;

    IF to_regprocedure('public.effective_plan_price(uuid, text, text, timestamptz)') IS NULL
       OR to_regprocedure('public.claim_early_adopter_seat(uuid, uuid)') IS NULL
       OR to_regprocedure('public.early_adopter_status(uuid)') IS NULL
       OR to_regprocedure('public.early_adopter_open()') IS NULL
       OR to_regprocedure('public.intro_offer_quote(uuid)') IS NULL
       OR to_regprocedure('public.release_early_adopter_seat(uuid, text)') IS NULL THEN
        RAISE EXCEPTION
            '192: a function this file replaces has an unexpected signature — '
            'read it live (pg_get_functiondef) before applying. Nothing changed.';
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. promo_batches — tiers and batches as data (§1.2, §2.1)
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.promo_batches (
    batch_id        text          PRIMARY KEY,
    plan_id         text          NOT NULL REFERENCES public.plans(plan_id),
    price_tier      smallint      NOT NULL,
    batch_order     integer       NOT NULL,
    price_sar       numeric(10,2) NOT NULL,
    months          integer       NOT NULL,
    seat_limit      integer       NOT NULL,
    profession_only boolean       NOT NULL DEFAULT false,
    enabled         boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT promo_batches_plan_tier_order_key UNIQUE (plan_id, price_tier, batch_order)
);

-- CHECKs apart from CREATE TABLE so a re-run converges (113's note: an existing
-- table skips the whole CREATE, inline constraints included).
ALTER TABLE public.promo_batches
    DROP CONSTRAINT IF EXISTS promo_batches_price_tier_check,
    DROP CONSTRAINT IF EXISTS promo_batches_months_check,
    DROP CONSTRAINT IF EXISTS promo_batches_seat_limit_check,
    DROP CONSTRAINT IF EXISTS promo_batches_price_sar_check;

ALTER TABLE public.promo_batches
    ADD CONSTRAINT promo_batches_price_tier_check CHECK (price_tier IN (1, 2)),
    ADD CONSTRAINT promo_batches_months_check     CHECK (months BETWEEN 1 AND 12),
    ADD CONSTRAINT promo_batches_seat_limit_check CHECK (seat_limit > 0),
    -- (I9) Moyasar refuses a charge under 1.00 (MIN_CHARGE_SAR); a batch priced
    -- below it would be a checkout that cannot be paid.
    ADD CONSTRAINT promo_batches_price_sar_check  CHECK (price_sar >= 1.00);

-- updated_at: the repo-wide trigger fn (014), else 126's. §8 reads
-- updated_at = created_at as "never edited since the seed", so the trigger is
-- load-bearing, not decoration.
DO $$
BEGIN
    EXECUTE 'DROP TRIGGER IF EXISTS trg_promo_batches_updated_at ON public.promo_batches';
    IF to_regprocedure('public.update_updated_at()') IS NOT NULL THEN
        EXECUTE 'CREATE TRIGGER trg_promo_batches_updated_at BEFORE UPDATE ON public.promo_batches '
             || 'FOR EACH ROW EXECUTE FUNCTION public.update_updated_at()';
    ELSIF to_regprocedure('public.set_updated_at()') IS NOT NULL THEN
        EXECUTE 'CREATE TRIGGER trg_promo_batches_updated_at BEFORE UPDATE ON public.promo_batches '
             || 'FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()';
    ELSE
        RAISE EXCEPTION '192: neither public.update_updated_at() nor public.set_updated_at() exists';
    END IF;
END $$;

-- RLS on, NO policies, service_role only (early_adopter_campaign's posture,
-- M138:461-465): seat_limit is a number that is never disclosed (§1.4-10).
ALTER TABLE public.promo_batches ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.promo_batches FROM PUBLIC;
REVOKE ALL ON TABLE public.promo_batches FROM anon;
REVOKE ALL ON TABLE public.promo_batches FROM authenticated;
GRANT  ALL ON TABLE public.promo_batches TO service_role;

-- The seed (§1.2, owner 2026-10-26/27). DO NOTHING: a re-run never reverts an
-- operator's UPDATE (the same posture as 138 §3 / 183 §1).
INSERT INTO public.promo_batches
    (batch_id, plan_id, price_tier, batch_order, price_sar, months, seat_limit, profession_only)
VALUES
    ('pro_t1_a', 'pro', 1, 1,  19.90, 2,  500, true),
    ('pro_t1_b', 'pro', 1, 2,  19.90, 1,  500, true),
    ('pro_t2_a', 'pro', 2, 1,  59.90, 2,  500, false),
    ('max_t1_a', 'max', 1, 1, 149.90, 4,  100, false),
    ('max_t2_a', 'max', 2, 1, 189.90, 3,  400, false),
    ('max_t2_b', 'max', 2, 2, 189.90, 1, 1000, false)
ON CONFLICT (batch_id) DO NOTHING;

COMMENT ON TABLE public.promo_batches IS
    'Price tiers and seat batches (192, pricing_campaigns_v2.md §1.2). One row per '
    'batch: plan, tier (1 or 2), order within the tier, price, months, seats. '
    'Within (plan, tier) the ACTIVE batch is the lowest batch_order that is '
    'enabled and has fewer live seats than seat_limit (promo_active_batch). '
    'Editing a row is the whole of changing an offer — no deploy — and NEVER '
    're-prices a held seat: claim_early_adopter_seat copies the terms onto the '
    'seat. Never DELETE or rename a row a seat or payment references (the FKs '
    'refuse); set enabled = false. Only read when early_adopter_campaign.'
    'ladder_v2 is on. RLS on, no policies, service_role only — seat_limit and '
    'the batch structure are never disclosed (§1.4-10).';
COMMENT ON COLUMN public.promo_batches.price_tier IS
    '1 or 2. Each user may hold each (plan, tier) ONCE (promo_tier_used). Pro '
    'tier 1 additionally needs the first profession answer in legal / specialist '
    '/ entrepreneur, the intro_offer_campaign window, and no running basic.';
COMMENT ON COLUMN public.promo_batches.batch_order IS
    'Sale order within (plan, tier): batches fill one after another, lowest first.';
COMMENT ON COLUMN public.promo_batches.months IS
    'Length of the seat''s promo window in 30-day months: promo_ends_at = paid_at '
    '+ 30 × months days. The renewal job prices periods that BEGIN inside it.';
COMMENT ON COLUMN public.promo_batches.seat_limit IS
    'Live seats this batch sells. Never disclosed. A quote issued while seats '
    'were free is honoured after the batch fills (seat stamped over_capacity).';
COMMENT ON COLUMN public.promo_batches.profession_only IS
    'TRUE ⇒ only offered to users whose profession_first_group is legal / '
    'specialist / entrepreneur (promo_quote).';
COMMENT ON COLUMN public.promo_batches.enabled IS
    'FALSE ⇒ never quoted again. Held seats and outstanding quotes are unaffected.';


-- ════════════════════════════════════════════════════════════════════════════
-- 2. early_adopter_seats — a seat freezes its batch terms (§1.4-3)
-- ════════════════════════════════════════════════════════════════════════════
-- Columns bare, constraints after (ADD COLUMN IF NOT EXISTS skips a whole
-- clause, constraint included — 113 / 132 §2). No backfill: batch_id NULL marks
-- a legacy seat, and the functions map legacy seats themselves (§1.3-7).
ALTER TABLE public.early_adopter_seats
    ADD COLUMN IF NOT EXISTS batch_id               text          NULL,
    ADD COLUMN IF NOT EXISTS plan_id                text          NULL,
    ADD COLUMN IF NOT EXISTS price_tier             smallint      NULL,
    ADD COLUMN IF NOT EXISTS price_sar              numeric(10,2) NULL,
    ADD COLUMN IF NOT EXISTS released_by_payment_id uuid          NULL;

ALTER TABLE public.early_adopter_seats
    DROP CONSTRAINT IF EXISTS early_adopter_seats_batch_id_fkey,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_release_reason_check,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_batch_terms_check,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_released_by_payment_id_fkey,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_released_by_check;

ALTER TABLE public.early_adopter_seats
    ADD CONSTRAINT early_adopter_seats_batch_id_fkey
        FOREIGN KEY (batch_id) REFERENCES public.promo_batches(batch_id),
    -- M138:227-228 widened with 'upgraded' (§1.3-6): a Pro seat released by a
    -- Pro → Max switch. Counts as having used that tier; is NEVER forfeiture
    -- (every forfeiture predicate tests release_reason = 'cancelled' only).
    ADD CONSTRAINT early_adopter_seats_release_reason_check
        CHECK (release_reason IS NULL
               OR release_reason IN ('refund', 'cancelled', 'upgraded')),
    -- (I9) A batch seat must carry the terms it froze. Spelled with IS NOT NULL
    -- on every term: a bare `price_tier IN (1, 2)` is NULL — i.e. PASSES — for a
    -- NULL tier.
    ADD CONSTRAINT early_adopter_seats_batch_terms_check
        CHECK (batch_id IS NULL
               OR (plan_id IS NOT NULL
                   AND price_tier IS NOT NULL AND price_tier IN (1, 2)
                   AND price_sar IS NOT NULL)),
    -- (V5) the payment whose v2 claim released this seat 'upgraded'. Payments
    -- are never deleted (117), so NO ACTION never blocks anything — the same
    -- posture as early_adopter_seats.payment_id (138 §2).
    ADD CONSTRAINT early_adopter_seats_released_by_payment_id_fkey
        FOREIGN KEY (released_by_payment_id)
        REFERENCES public.payment_transactions(payment_id),
    -- only an 'upgraded' release names the payment that superseded it; a
    -- restore clears both together
    ADD CONSTRAINT early_adopter_seats_released_by_check
        CHECK (released_by_payment_id IS NULL OR release_reason = 'upgraded');

-- FK index (project rule): every seat ever sold from a batch, live or released.
CREATE INDEX IF NOT EXISTS idx_early_adopter_seats_batch
    ON public.early_adopter_seats (batch_id)
    WHERE batch_id IS NOT NULL;

-- (R3) The live count per batch — promo_active_batch, the claim's capacity
-- check, early_adopter_open, and the backend's 90% alert (which counts through
-- PostgREST as service_role). Only live rows, so it stays ≤ the seats on sale.
CREATE INDEX IF NOT EXISTS idx_early_adopter_seats_batch_live
    ON public.early_adopter_seats (batch_id)
    WHERE released_at IS NULL;

-- (V5) FK index + the refund path's "which seat did this payment supersede".
CREATE INDEX IF NOT EXISTS idx_early_adopter_seats_released_by
    ON public.early_adopter_seats (released_by_payment_id)
    WHERE released_by_payment_id IS NOT NULL;

COMMENT ON COLUMN public.early_adopter_seats.released_by_payment_id IS
    'Set only with release_reason = ''upgraded'' (192): the payment whose v2 claim '
    'superseded this seat (a Pro → Max switch, a newer batch purchase). If that '
    'payment is refunded, release_early_adopter_seat(user, ''refund'') gives THIS '
    'seat back while its window is open. Cleared on restore.';

COMMENT ON COLUMN public.early_adopter_seats.batch_id IS
    'The promo_batches row this seat was sold from (192). NULL = a LEGACY seat '
    '(138/183, pre-tiers): priced from plans.promo_price_sar for any plan, and '
    'mapped by promo_tier_used through its payment (legacy pro ⇒ pro tier 2 '
    'used, legacy max ⇒ max tier 1 used).';
COMMENT ON COLUMN public.early_adopter_seats.plan_id IS
    'Batch seats only: the plan this seat prices at price_sar. With ladder_v2 on '
    'a batch seat prices THIS plan and no other; with it off (a rollback) it also '
    'carries over to the other plan at plans.promo_price_sar, as every seat did '
    'before 192.';
COMMENT ON COLUMN public.early_adopter_seats.price_tier IS
    'Batch seats only: the tier this seat used (promo_tier_used reads it, live '
    'or released).';
COMMENT ON COLUMN public.early_adopter_seats.price_sar IS
    'Batch seats only: the frozen price charged for every period that BEGINS '
    'before promo_ends_at — the QUOTED offer_sar from the claiming payment''s '
    'intro_offer_terms (the batch row only if the payment carries none). Editing '
    'the batch afterwards never changes it.';
COMMENT ON COLUMN public.early_adopter_seats.release_reason IS
    '''refund'' (money back; the seat returns to its batch) | ''cancelled'' '
    '(cancellation stood) | ''upgraded'' (192: released by a v2 claim — a Pro → '
    'Max switch or a newer batch purchase). With ladder_v2 OFF, ''cancelled'' on '
    'ANY row is today''s permanent forfeiture of the legacy promo (138 §1 rule 6, '
    'intro-offer members exempt — 183). With ladder_v2 ON there is no global '
    'forfeiture: a released seat only marks its (plan, tier) as used — except a '
    '''refund'' release whose own claiming payment is ''refunded'' (a refunded '
    'purchase counts as never bought; owner 2026-10-27). ''upgraded'' never forfeits in either mode, and an ''upgraded'' seat is '
    'given back (released_by_payment_id) if the payment that superseded it is '
    'refunded while its window is open. Undo a cancellation with '
    'restore_early_adopter_seat, never by editing this column.';


-- ════════════════════════════════════════════════════════════════════════════
-- 3. payment_transactions.promo_batch_id — the quote is the promise (§1.4-5)
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS promo_batch_id text NULL;

-- ⚠ Contract FK. Unlike 183's intro_offer_group (deliberately FK-less), this
-- ties a ledger row to a config row: a batch referenced by any checkout can
-- never be DELETEd or renamed. That is acceptable only because batches are
-- retired with enabled = false, never deleted.
ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_promo_batch_id_fkey;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_promo_batch_id_fkey
    FOREIGN KEY (promo_batch_id) REFERENCES public.promo_batches(batch_id);

CREATE INDEX IF NOT EXISTS idx_payment_transactions_promo_batch
    ON public.payment_transactions (promo_batch_id)
    WHERE promo_batch_id IS NOT NULL;

COMMENT ON COLUMN public.payment_transactions.promo_batch_id IS
    'The batch promo_quote returned when this checkout was priced (192). '
    'Stamped only while ladder_v2 is on, and only from a promo_quote row — the '
    'price charged must be that row''s price. claim_early_adopter_seat seats the '
    'payment in THIS batch even if the batch filled or the flag was turned off '
    'before the money landed (over_capacity is stamped). NULL = no tier price '
    '(list price, or a pre-192 / flag-off checkout on the legacy path). Renewal '
    'rows never carry it.';

-- (R4) 183 documented intro_offer_terms as "NULL on every non-offer row". From
-- 192 the backend stamps it on EVERY batch sale; the comment follows.
COMMENT ON COLUMN public.payment_transactions.intro_offer_terms IS
    'The offer schedule shown at checkout, stamped once (183; widened by 192). '
    'From 192 on EVERY batch sale (promo_batch_id NOT NULL) carries it — Pro '
    'tier 1, Pro tier 2 and Max alike — with "offer_sar" / "offer_months" (the '
    'batch price and its months) beside the 183 keys, kept truthful for old '
    'clients: "first_month_sar" = "then_sar" = offer_sar, "then_months" = '
    'offer_months − 1, "list_sar". 2-dp strings / integers. The recurring-consent '
    'disclosure is rebuilt from THIS, never from the live catalog or batch row, '
    'so the hashed text a customer agreed to can always be reproduced. Only Pro '
    'tier 1 rows also carry intro_offer_group (the non-refundable first payment, '
    'and is_intro_offer_member''s evidence); a Pro tier 2 or Max row has '
    'intro_offer_terms with intro_offer_group NULL. Pre-192 rows: 183 offer '
    'first months only. NULL on list-price and renewal rows.';

-- ── 3b. (V6) credit_basis — decided at checkout, never re-derived ───────────
-- §1.4-8 moves the upgrade credit from "the price of the plan" to "the cash
-- actually paid". Which basis a credited checkout used is a fact about THAT
-- checkout; re-deriving it from ladder_v2 at settlement would change the owed
-- credit for a quote that straddles a flip.
ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS credit_basis text NULL;

ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_credit_basis_check;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_credit_basis_check
    CHECK (credit_basis IS NULL OR credit_basis IN ('price', 'cash'));

COMMENT ON COLUMN public.payment_transactions.credit_basis IS
    'How upgrade_credit_sar was computed at checkout (192): ''price'' = the old '
    'plan''s effective price ''current'' × remaining-days ratio (pre-192 rule); '
    '''cash'' = the cash amount_sar of the payment funding the running term × '
    'the same ratio (§1.4-8). Stamped by create_checkout whenever a credit '
    'applies; settlement (_revalidate_credited_charge) must re-run the credit '
    'with THIS basis, never re-derive it from ladder_v2. NULL = ''price'' (every '
    'pre-192 row and every uncredited row).';

-- ── 3c. (V7) clients cannot read the quote internals ───────────────────────
-- payment_transactions predates 118's default-privilege guard, so anon and
-- authenticated still hold Supabase's blanket table grants; reads are scoped
-- only by RLS (payment_transactions_select_self, M092:87-91 — a user reads
-- their own rows). promo_batch_id would hand a client the batch structure, and
-- intro_offer_terms / credit_basis are checkout internals.
--
-- Column privileges only bite once TABLE-level SELECT is gone (a table grant
-- covers every column), so: revoke table SELECT (which also drops any column
-- SELECT grants — the re-run converges), then grant authenticated SELECT on
-- every CURRENT column except the three, read from the catalog so drift
-- between these files and prod cannot drop a column by accident. anon gets
-- nothing: it has no rows under the policy anyway.
--
-- Verified 2026-10-27: frontend/ and mobile/ make no supabase-js data call or
-- realtime subscription on this table (frontend only names it in comments);
-- every read is the backend's service-role client, untouched by this. No
-- view, policy or invoker-rights function reads it as a client role.
-- ⚠ A column added to payment_transactions LATER is invisible to
-- authenticated until granted — deny-by-default, deliberately.
-- INSERT / UPDATE / DELETE grants are untouched (no write policy exists; the
-- table-wide sweep is 118's deferred item, not this file's).
REVOKE SELECT ON TABLE public.payment_transactions FROM anon;
REVOKE SELECT ON TABLE public.payment_transactions FROM authenticated;

DO $$
DECLARE
    v_cols TEXT;
BEGIN
    SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum)
      INTO v_cols
      FROM pg_attribute a
     WHERE a.attrelid = 'public.payment_transactions'::regclass
       AND a.attnum > 0
       AND NOT a.attisdropped
       AND a.attname NOT IN ('promo_batch_id', 'intro_offer_terms', 'credit_basis');

    IF v_cols IS NULL THEN
        RAISE EXCEPTION '192: payment_transactions has no columns to grant?';
    END IF;

    EXECUTE format('GRANT SELECT (%s) ON TABLE public.payment_transactions TO authenticated', v_cols);
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 4. early_adopter_campaign.ladder_v2 — the switch (§2.1)
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.early_adopter_campaign
    ADD COLUMN IF NOT EXISTS ladder_v2    boolean     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ladder_v2_at timestamptz NULL;

-- (R2) ladder_v2_at = the instant of the LATEST false → true flip. Stamped by
-- trigger so the flip stays one UPDATE of one column; never cleared (an UPDATE
-- that writes NULL keeps the old value). now() = the flipping transaction's
-- start, the same clock payment_transactions.created_at defaults to.
CREATE OR REPLACE FUNCTION public.stamp_ladder_v2_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'public'
AS $function$
BEGIN
    IF NEW.ladder_v2 AND NOT OLD.ladder_v2 THEN
        NEW.ladder_v2_at := now();
    ELSIF NEW.ladder_v2_at IS NULL THEN
        NEW.ladder_v2_at := OLD.ladder_v2_at;
    END IF;
    RETURN NEW;
END;
$function$;

REVOKE EXECUTE ON FUNCTION public.stamp_ladder_v2_at() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_early_adopter_campaign_ladder_v2_at ON public.early_adopter_campaign;
CREATE TRIGGER trg_early_adopter_campaign_ladder_v2_at
    BEFORE UPDATE ON public.early_adopter_campaign
    FOR EACH ROW EXECUTE FUNCTION public.stamp_ladder_v2_at();

COMMENT ON COLUMN public.early_adopter_campaign.ladder_v2_at IS
    'When ladder_v2 last went false → true (192, stamped by '
    'trg_early_adopter_campaign_ladder_v2_at; never cleared, re-stamped on a '
    're-flip). While ladder_v2 is on, a pro/max payment created at or after this '
    'instant without a promo_batch_id cannot claim a LEGACY seat '
    '(claim_early_adopter_seat answers plan_not_eligible if it was promo-priced, '
    'not_promo_priced if list-priced). Payments created before it keep the legacy '
    'claim. NULL = never flipped.';

COMMENT ON COLUMN public.early_adopter_campaign.ladder_v2 IS
    'TRUE ⇒ price tiers + seat batches (192): new quotes come from promo_quote, '
    'new claims from payment_transactions.promo_batch_id, and the global '
    'cancellation forfeiture is off. FALSE (the default, and the rollback) ⇒ '
    'today''s 138/183 behaviour for every NEW quote and claim. It never '
    're-prices a held seat in either direction: batch seats keep their own '
    'price and legacy seats keep plans.promo_price_sar to the end of their '
    'window. ⚠ Flip only after the backend that stamps promo_batch_id is live. '
    '`enabled` still gates quoting in both modes.';


-- ════════════════════════════════════════════════════════════════════════════
-- 5. plans.promo_price_sar — frozen (§1.4-4)
-- ════════════════════════════════════════════════════════════════════════════
COMMENT ON COLUMN public.plans.promo_price_sar IS
    'FROZEN (192). Legacy early-adopter seats (early_adopter_seats.batch_id NULL) '
    'are priced from this column — pro 49.90, max 99.90 — for every period that '
    'begins inside their window, and the 183 intro offer reads it while '
    'ladder_v2 is off. NEVER EDIT IT AGAIN: changing it re-prices every legacy '
    'holder''s next renewal on a saved card. Tier prices live in promo_batches. '
    'Never read directly by application code — go through effective_plan_price.';


-- ════════════════════════════════════════════════════════════════════════════
-- 6. Functions — all SECURITY DEFINER, search_path pinned, service_role only.
--    New names avoid overload trouble; replaced functions keep their exact
--    signature AND return type (a RETURNS change would need DROP and lose the
--    grants). Order matters: SQL-language bodies are validated at CREATE.
-- ════════════════════════════════════════════════════════════════════════════

-- ── 6.1 promo_tier_used — "ever held this tier of this plan" (§1.3-7) ───────
CREATE OR REPLACE FUNCTION public.promo_tier_used(p_user_id uuid, p_plan_id text, p_tier smallint)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
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

COMMENT ON FUNCTION public.promo_tier_used(uuid, text, smallint) IS
    'TRUE iff this user ever held a seat — live OR released (cancelled, upgraded, '
    'refund) — at tier p_tier of plan p_plan_id (192, §1.3-7). EXCEPT a refunded '
    'purchase (owner 2026-10-27): a seat released ''refund'' whose own claiming '
    'payment is ''refunded'' counts as never bought. A seat released ''refund'' '
    'because a later renewal was refunded (claiming payment still ''paid'') still '
    'counts. A batch seat answers from its own (plan_id, price_tier); a LEGACY seat maps '
    'through its claiming payment: legacy pro ⇒ (pro, 2), legacy max ⇒ (max, 1), '
    'and a legacy seat claimed by a 183 intro-offer first month '
    '(intro_offer_group NOT NULL) ⇒ (pro, 1) AND (pro, 2). A legacy seat with no '
    'payment maps to nothing. NULL user → false. Callers pass p_tier as smallint '
    '(1::smallint) — an int literal does not resolve. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.promo_tier_used(uuid, text, smallint) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.promo_tier_used(uuid, text, smallint) TO service_role;


-- ── 6.2 promo_active_batch — the batch now selling in (plan, tier) ──────────
CREATE OR REPLACE FUNCTION public.promo_active_batch(p_plan_id text, p_tier smallint)
RETURNS SETOF public.promo_batches
LANGUAGE sql
STABLE
SECURITY DEFINER
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
               AND s.batch_id = b.batch_id) < b.seat_limit
     ORDER BY b.batch_order
     LIMIT 1;
$function$;

COMMENT ON FUNCTION public.promo_active_batch(text, smallint) IS
    'Zero or one promo_batches row (192, §1.4-1): the lowest batch_order in '
    '(plan, tier) that is enabled and has fewer live seats (released_at IS NULL, '
    'this batch_id) than seat_limit. Ignores the campaign switches — promo_quote '
    'and promo_public_quote gate on those. Its row carries seat_limit: never '
    'forward it to a client. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.promo_active_batch(text, smallint) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.promo_active_batch(text, smallint) TO service_role;


-- ── 6.3 early_adopter_open — zero args kept (PS:886 calls it with {}) ───────
CREATE OR REPLACE FUNCTION public.early_adopter_open()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
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
                                        AND s.batch_id = b.batch_id) < b.seat_limit)
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

COMMENT ON FUNCTION public.early_adopter_open() IS
    'A boolean and never a count. ladder_v2 OFF (138): campaign enabled AND live '
    'seats < seat_limit. ladder_v2 ON (192): campaign enabled AND some enabled '
    'batch has free seats — including a profession-only Pro tier-1 batch, even '
    'after intro_offer_campaign.ends_at (the public price is promo_public_quote). '
    'Missing campaign row → false. Service-role only; public through GET '
    '/payments/early-adopter.';

REVOKE EXECUTE ON FUNCTION public.early_adopter_open() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.early_adopter_open() TO service_role;


-- ── 6.4 promo_quote — THE personal quote (§1.3) ─────────────────────────────
-- ⚠ RETURNS TABLE columns are plpgsql variables (batch_id, price_tier,
--   price_sar, months) and share names with promo_batches / seat columns —
--   every column reference below is table-qualified, or it is ambiguous.
CREATE OR REPLACE FUNCTION public.promo_quote(p_user_id uuid, p_plan_id text)
RETURNS TABLE(batch_id text, price_tier smallint, price_sar numeric, months integer)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
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
    IF p_user_id IS NULL OR p_plan_id IS NULL OR p_plan_id NOT IN ('pro', 'max') THEN
        RETURN;
    END IF;

    SELECT c.enabled AS is_on, c.ladder_v2 AS v2
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;
    IF NOT FOUND OR NOT v_camp.v2 OR NOT v_camp.is_on THEN
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

COMMENT ON FUNCTION public.promo_quote(uuid, text) IS
    'The personal tier quote (192, §1.3): zero or one row (batch_id, price_tier, '
    'price_sar, months). EMPTY when ladder_v2 or the campaign is off, the plan is '
    'not pro/max, the user is on that plan now (user_subscriptions_live), the '
    'user is on max and asks for pro, or the user holds a live seat still inside '
    'its window that prices that plan (I2: a batch seat its own plan; a legacy '
    'seat every plan, except a legacy pro seat asked for max — the A3 switch). '
    'Otherwise tier 1 '
    '(max: anyone; pro: profession_first_group in legal/specialist/entrepreneur, '
    'intro_offer_campaign enabled and before ends_at, no running basic) if never '
    'used and a batch is open, else tier 2 if never used and a batch is open. '
    'A profession_only batch is skipped outside the three groups. Checkout '
    'charges price_sar and stamps payment_transactions.promo_batch_id = batch_id; '
    'effective_plan_price(''purchase'') returns the same price. Callers must FAIL '
    'CLOSED (error ⇒ no quote). Never send batch_id or tier to a client. '
    'Service-role only.';

REVOKE EXECUTE ON FUNCTION public.promo_quote(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.promo_quote(uuid, text) TO service_role;


-- ── 6.5 promo_public_quote — the general (non-personal) public price ────────
CREATE OR REPLACE FUNCTION public.promo_public_quote()
RETURNS TABLE(plan_id text, price_sar numeric, months integer)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
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

    RETURN;
END;
$function$;

COMMENT ON FUNCTION public.promo_public_quote() IS
    'The general public promo price per plan (192) for /pricing and GET '
    '/payments/early-adopter: rows (plan_id, price_sar, months), a closed plan '
    'drops out. ladder_v2 ON (and campaign enabled): pro = the active pro tier-2 '
    'batch; max = the active max tier-1 batch, else tier 2. ladder_v2 OFF: '
    'today — every plan with a non-NULL promo_price_sar while early_adopter_open(), '
    'months = promo_days / 30. Never a count, a batch or a tier. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.promo_public_quote() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.promo_public_quote() TO service_role;


-- ── 6.6 effective_plan_price — same signature, body only ────────────────────
-- Order of the branches (✱ = flag-independent):
--   ✱ (b1) a live BATCH seat for THIS plan inside its window → seat.price_sar.
--          First of all — before 183's forfeiture — so a held batch seat keeps
--          its price after a rollback (I5).
--   ladder_v2 ON:
--          no forfeiture branch (used-tier logic in promo_quote replaces it);
--          (c)  'purchase' → promo_quote(...).price_sar if a row         (I3)
--     ✱    (b2) a live LEGACY seat inside its window → promo_price_sar
--          list.
--   ladder_v2 OFF: 183's body; a live batch seat suspends the forfeiture and,
--          for the OTHER plan, carries over at promo_price_sar like any seat
--          (identical until the first batch seat exists).
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
        END IF;

        -- Basic and every other plan: list. Basic is outside every tier (§1.1).
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

COMMENT ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz) IS
    'THE price of a plan FOR A GIVEN USER (138; p_at 182; intro exemption 183; '
    'tiers 192). p_context ''purchase'' = what it costs if bought now '
    '(create_checkout); ''current'' = what this user''s running term costs '
    '(renewal job, _upgrade_credit). DO NOT COLLAPSE THE TWO. p_at = the instant '
    'priced (renewals pass the new period''s start); NULL = now(). '
    'IN EVERY MODE: a live batch seat for THIS plan inside its window returns its '
    'frozen price_sar. ladder_v2 ON: no forfeiture; ''purchase'' returns '
    'promo_quote''s price when it has a row; then a live legacy seat inside its '
    'window returns promo_price_sar (any plan, as today); else list. ladder_v2 '
    'OFF: 183''s rules (forfeiture on a ''cancelled'' row unless intro member or '
    'a live batch seat is held; any other live seat in its window — legacy, or a '
    'batch seat of the other plan — ⇒ promo_price_sar; ''purchase'' while '
    'early_adopter_open() ⇒ promo). '
    'NULL p_user_id = no seat. Unknown p_context RAISES 22023. Rounded to 2dp. '
    'Service-role only. ⚠ ONE SIGNATURE ONLY — a second overload makes PostgREST '
    'named-arg calls ambiguous (PGRST203) and the backend charges the catalog.';

REVOKE EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text, timestamptz)
    TO service_role;


-- ── 6.7 claim_early_adopter_seat — same signature, v2 path added ────────────
-- (I1) Signature unchanged: (p_user_id, p_payment_id), RETURNS TABLE unchanged,
-- action vocabulary unchanged. A payment with promo_batch_id takes the v2 path
-- whatever the flag says now — the quote is the promise (§1.4-5). Everything
-- else takes 183's body, its forfeiture skipped while the flag is on.
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
BEGIN
    -- ⚠ FIRST, same key as 138 — every writer of the seat table takes it.
    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    SELECT t.user_id, t.plan_id, t.paid_at, t.amount_sar, t.upgrade_credit_sar,
           t.promo_batch_id, t.created_at, t.intro_offer_terms
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
        SELECT s.seat_id AS hid, s.batch_id AS hbatch, s.promo_ends_at AS hends,
               s.over_capacity AS hover
          INTO v_held
          FROM public.early_adopter_seats s
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
        v_at   := COALESCE(v_pay.paid_at, now());
        v_ends := v_at + make_interval(days => 30 * v_months);           -- (V1)

        -- Release + insert in ONE subtransaction: a unique_violation (a manual
        -- INSERT that skipped the lock) rolls the release back too.
        BEGIN
            IF v_has_held THEN
                -- (I4) legacy seat, other plan, or a spent / other batch of
                -- this plan: superseded by this purchase. Counts as used,
                -- never forfeits. (V5) remembers WHICH payment superseded it,
                -- so refunding that payment gives this seat back.
                UPDATE public.early_adopter_seats s
                   SET released_at            = now(),
                       release_reason         = 'upgraded',
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
    -- it switched off while ladder_v2 is on (192). 'upgraded' never matches.
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

COMMENT ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) IS
    'Seat a paid pro/max purchase (138; intro exemption 183; batches 192). Called '
    'AFTER grant_plan, under pg_advisory_xact_lock(hashtext(''early_adopter_seats'')). '
    'Idempotent on payment_id. V2 PATH — the payment carries promo_batch_id '
    '(honoured even if ladder_v2 was turned off since): plan must match the batch; '
    'the QUOTED terms (intro_offer_terms offer_sar / offer_months; the batch row '
    'only if absent) set the gate amount_sar + upgrade_credit_sar <= offer + 0.01, '
    'the seat price_sar and promo_ends_at = paid_at + 30 × offer_months days; any '
    'live seat of the user not in this batch is released ''upgraded'' with '
    'released_by_payment_id = this payment (a Pro → Max switch, a legacy seat, a '
    'spent window); the new seat copies batch_id/plan_id/price_tier; '
    'over_capacity when the batch already had seat_limit live seats. LEGACY PATH '
    '(no promo_batch_id): 183''s body — a live seat carries over, gate against '
    'plans.promo_price_sar, window promo_days — with the ''cancelled'' forfeiture '
    'skipped while ladder_v2 is on, and (R2) while ladder_v2 is on a payment '
    'created at or after early_adopter_campaign.ladder_v2_at that passes the '
    'legacy gate gets NO seat (plan_not_eligible + WARNING: an unstamped promo '
    'sale after the flip is a checkout bug); payments created before the flip '
    'keep the legacy claim. Actions unchanged: claimed / already_claimed '
    '/ not_promo_priced / campaign_disabled / forfeited / plan_not_eligible / '
    'payment_not_found / user_mismatch; seat_id IS NULL ⇔ no seat. Never raises '
    'on a missing payment. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.claim_early_adopter_seat(uuid, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) TO service_role;


-- ── 6.8 intro_offer_quote — same signature and RETURNS; a wrapper when ON ──
-- LANGUAGE sql → plpgsql (allowed by CREATE OR REPLACE; signature, return type
-- and grants unchanged). OUT columns profession_group / first_month_sar share
-- names with intro_offer_prices / users columns — everything is qualified.
CREATE OR REPLACE FUNCTION public.intro_offer_quote(p_user_id uuid)
RETURNS TABLE(profession_group text, first_month_sar numeric)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
BEGIN
    -- ══ ladder_v2 ON: eligible iff the personal Pro quote is tier 1 ══
    IF COALESCE((SELECT c.ladder_v2 FROM public.early_adopter_campaign c WHERE c.id), false) THEN
        RETURN QUERY
            SELECT u.profession_first_group, round(q.price_sar, 2)
              FROM public.users u
              CROSS JOIN LATERAL public.promo_quote(u.user_id, 'pro') q
             WHERE u.user_id = p_user_id
               AND q.price_tier = 1;
        RETURN;
    END IF;

    -- ══ ladder_v2 OFF: 183's body, unchanged ══
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
                   AND t.plan_id IN ('pro', 'max')
                   AND t.status IN ('paid', 'refunded'))
           AND NOT EXISTS (
                SELECT 1
                  FROM public.early_adopter_seats s
                 WHERE s.user_id = p_user_id
                   AND s.release_reason = 'cancelled');
END;
$function$;

COMMENT ON FUNCTION public.intro_offer_quote(uuid) IS
    'Pro tier-1 / profession offer eligibility (183; tiers 192): one row '
    '(profession_group, first_month_sar) or none. ladder_v2 ON: a row iff '
    'promo_quote(user, ''pro'') returns a tier-1 row; first_month_sar is that '
    'row''s price_sar (the whole offer price, for that row''s months), '
    'profession_group the write-once users.profession_first_group. ladder_v2 '
    'OFF: 183 — intro_offer_campaign open, early_adopter_open(), effective plan '
    'free/locked, never paid or refunded pro/max, no cancelled seat, price from '
    'intro_offer_prices. Checkout stamps intro_offer_group from it (first '
    'payment non-refundable). Callers must FAIL CLOSED. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.intro_offer_quote(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.intro_offer_quote(uuid) TO service_role;


-- ── 6.9 early_adopter_status — body only (the fourth predicate copy) ───────
CREATE OR REPLACE FUNCTION public.early_adopter_status(p_user_id uuid)
 RETURNS TABLE(campaign_open boolean, has_seat boolean, promo_ends_at timestamp with time zone)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
    v_ends     TIMESTAMPTZ;
    v_is_batch BOOLEAN;
    v_forfeit  BOOLEAN;
BEGIN
    SELECT s.promo_ends_at, (s.batch_id IS NOT NULL)
      INTO v_ends, v_is_batch
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL
     ORDER BY s.claimed_at DESC
     LIMIT 1;

    -- has_seat must mean EXACTLY what effective_plan_price charges (138/183).
    -- (I6) 192: a live batch seat is priced before any forfeiture in both
    -- modes, and the flag removes forfeiture altogether — so the 183 predicate
    -- only bites a live LEGACY seat with the flag off. 'upgraded' never matches.
    v_forfeit := NOT COALESCE(v_is_batch, false)
                 AND NOT COALESCE((SELECT c.ladder_v2
                                     FROM public.early_adopter_campaign c
                                    WHERE c.id), false)
                 AND EXISTS (SELECT 1
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

COMMENT ON FUNCTION public.early_adopter_status(uuid) IS
    'One row describing a user''s own seat (138; 183; 192): campaign_open = '
    'early_adopter_open(); has_seat = a live seat whose window is still open and '
    'that effective_plan_price is honouring — a batch seat always, a legacy seat '
    'unless forfeited (ladder_v2 off, a standing ''cancelled'' row, not an intro '
    'member); promo_ends_at = that seat''s window end. NO count. Always exactly '
    'one row. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.early_adopter_status(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.early_adopter_status(uuid) TO service_role;


-- ── 6.10 release_early_adopter_seat — SAME signature; a refund gives back (V5)
-- Body = 138's, plus: (1) the claim's advisory lock, because this function may
-- now also RESTORE a seat (138's own rule: every writer takes the same key);
-- (2) on a 'refund' release, if the seat just released was claimed by a payment
-- that is now 'refunded', give back the seat THAT payment superseded
-- ('upgraded', released_by_payment_id = it) while its window is open.
--
-- WHY NO SIGNATURE CHANGE IS NEEDED: both backend refund paths stamp the
-- payment row status = 'refunded' BEFORE they call this (payment_service.py
-- _mark_refunded ~2899-2905 → release ~2913-2919; refund_payment ~3266-3276 →
-- release ~3285-3291), and the live seat names its own claiming payment. So
-- "the released seat's payment is refunded" identifies the refunded switch
-- payment exactly. A refund of some OTHER payment (a renewal) still releases
-- the live seat as today, but restores nothing — that seat's own payment is
-- still 'paid'.
--
-- The restore never fails the refund: it runs in its own subtransaction and
-- downgrades any error to a WARNING (the release itself stands).
CREATE OR REPLACE FUNCTION public.release_early_adopter_seat(p_user_id uuid, p_reason text)
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
BEGIN
    IF p_user_id IS NULL THEN
        RETURN false;
    END IF;

    -- The CHECK constraint would raise; the refund path must not 500 over a
    -- typo'd literal. 'upgraded' is written only by the claim, never here.
    IF p_reason IS NULL OR p_reason NOT IN ('refund', 'cancelled') THEN
        RAISE NOTICE 'release_early_adopter_seat: refusing unknown reason %', p_reason;
        RETURN false;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    SELECT s.seat_id AS id, s.payment_id AS pay
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL
     ORDER BY s.claimed_at DESC
     LIMIT 1;

    UPDATE public.early_adopter_seats s
       SET released_at    = now(),
           release_reason = p_reason
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;
    END IF;

    -- (V5) the refunded payment claimed this seat through a switch → give the
    -- superseded seat back (the one-live index is free: we just released).
    IF p_reason = 'refund'
       AND v_seat.pay IS NOT NULL
       AND EXISTS (SELECT 1
                     FROM public.payment_transactions t
                    WHERE t.payment_id = v_seat.pay
                      AND t.status = 'refunded') THEN
        BEGIN
            SELECT s.seat_id AS id, s.batch_id AS batch
              INTO v_prev
              FROM public.early_adopter_seats s
             WHERE s.user_id = p_user_id
               AND s.release_reason = 'upgraded'
               AND s.released_by_payment_id = v_seat.pay
               AND s.promo_ends_at > now()
             ORDER BY s.released_at DESC
             LIMIT 1;

            IF FOUND THEN
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
                    '(the seat that payment had superseded)', v_seat.pay, v_prev.id;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING
                'release_early_adopter_seat: refund of payment % released its seat but '
                'could not give back the superseded one: % (%) — restore it by hand',
                v_seat.pay, SQLERRM, SQLSTATE;
        END;
    END IF;

    RETURN true;
END;
$function$;

COMMENT ON FUNCTION public.release_early_adopter_seat(uuid, text) IS
    'Give a seat back to the pool (138; 192). reason = ''refund'' (status void, '
    'NOT restorable, NOT a forfeiture) or ''cancelled'' (forfeiture record while '
    'ladder_v2 is off; restorable by restore_early_adopter_seat). 192: takes the '
    'claim''s advisory lock, and on a ''refund'' whose released seat was claimed '
    'by a payment now ''refunded'', gives back the seat that payment superseded '
    '(release_reason ''upgraded'', released_by_payment_id = it) if its window is '
    'still open — refunding a Pro → Max switch returns the Pro seat. Returns TRUE '
    'only if a live seat was released; FALSE is informational. Never fails the '
    'refund over the give-back (WARNING instead). Service-role only.';

REVOKE EXECUTE ON FUNCTION public.release_early_adopter_seat(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.release_early_adopter_seat(uuid, text) TO service_role;


-- ── 6.11 promo_checkout_price — the ONE call checkout makes (V2) ────────────
-- Price and stamp from one snapshot: a STABLE function sees one snapshot for
-- all its statements, so the price charged and the batch stamped cannot come
-- from two different states of promo_batches / seats. Calls promo_quote ONCE;
-- the no-quote fallback is computed inline (effective_plan_price's flag-ON
-- 'purchase' path without its quote step) — calling effective_plan_price
-- would quote a second time.
-- ⚠ OUT columns share names with table columns (ladder_v2, price_sar,
--   batch_id, price_tier, months): every reference is qualified.
CREATE OR REPLACE FUNCTION public.promo_checkout_price(p_user_id uuid, p_plan_id text)
RETURNS TABLE(ladder_v2 boolean, price_sar numeric, batch_id text, price_tier smallint,
              months integer, intro_group text)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
DECLARE
    v_plan  RECORD;
    v_q     RECORD;
    v_price NUMERIC;
    v_group TEXT;
BEGIN
    SELECT p.price_sar AS lst, p.promo_price_sar AS prm, p.billing_cycle AS cyc
      INTO v_plan
      FROM public.plans p
     WHERE p.plan_id = p_plan_id;
    IF NOT FOUND THEN
        RETURN;                                          -- unknown plan: no row
    END IF;

    -- ══ flag OFF: exactly today's purchase price, nothing to stamp ══
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
      FROM public.promo_quote(p_user_id, p_plan_id) q
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

COMMENT ON FUNCTION public.promo_checkout_price(uuid, text) IS
    'The checkout quote in ONE call and ONE snapshot (192): exactly one row for a '
    'known plan, none for an unknown one. ladder_v2 OFF ⇒ (false, '
    'effective_plan_price(user, plan, ''purchase''), NULL, NULL, NULL, NULL). '
    'ladder_v2 ON ⇒ promo_quote once: a row ⇒ (true, its price_sar, batch_id, '
    'price_tier, months, intro_group = users.profession_first_group when plan = '
    'pro and tier = 1, else NULL); no row ⇒ (true, own-plan batch seat price, else '
    'legacy-seat promo_price_sar, else list, NULL, NULL, NULL, NULL) computed '
    'inline. Checkout charges price_sar (minus credit) and stamps batch_id into '
    'payment_transactions.promo_batch_id, price/months into intro_offer_terms, '
    'intro_group into intro_offer_group. price_sar NULL ⇒ not purchasable. '
    'Service-role only; never forward batch_id or price_tier to a client.';

REVOKE EXECUTE ON FUNCTION public.promo_checkout_price(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.promo_checkout_price(uuid, text) TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- 7. product_docs.pricing — «عرض الشهر الأول» → «العرض حسب المهنة»
-- ════════════════════════════════════════════════════════════════════════════
-- The router reads this row before answering anything about ريحان. Same rules
-- as 140/183: no amount, no end date, no seat count, never confirm eligibility,
-- NO DIGIT AT ALL (M126), and not the phrase «المشتركون الأوائل» (140's n_marker
-- post-check stays at 2). The M183 section is the LAST section of the doc, so
-- the replacement is: everything before its heading, then the new section.
--
-- Guarded by the md5 of the WHOLE live row (read 2026-10-27, length 3062):
-- any other content is left alone with a NOTICE, never a failure. ⚠ The row is
-- the only copy (126 has no seeder) — save it before applying:
--     SELECT content_md FROM public.product_docs WHERE doc_key = 'pricing';
-- ⚠ This file could not hash its own new Arabic text (written without DB
-- access); pin it in the dry run — POST-APPLY CHECK 2.
DO $$
DECLARE
    v_old_marker TEXT := '## عرض الشهر الأول';
    v_new_marker TEXT := '## العرض حسب المهنة';
    v_live_md5   TEXT := '00a4627022b47298a194075aaf966050';
    v_section    TEXT :=
        '## العرض حسب المهنة'
        || E'\n\n'
        || 'قد يظهر داخل ريحان، لبعض المستخدمين بحسب المهنة التي اختاروها في حسابهم، سعرٌ ترويجي على باقة «الاحترافية» لأول شهر أو أكثر حسب العرض، ثم السعر المعتاد، مع التجديد التلقائي كأي اشتراك في هذه الباقة. يظهر العرض تلقائياً داخل التطبيق لمن يستحقه فقط ولا يحتاج إلى رمز، ويُستفاد منه مرة واحدة فقط، والدفعة الأولى فيه غير قابلة للاسترداد. عند أي سؤال عنه: لا تؤكّد أنّ المستخدم مؤهّل له أو غير مؤهّل، ولا أنّ العرض ساري الآن أو انتهى، ولا تذكر أي مبلغ ولا تاريخ انتهاء ولا عدد مقاعد، وأحِل المستخدم إلى شروط العروض الترويجية https://rayhanai.com/promo-terms — وإن كان مؤهّلاً فسيظهر له العرض داخل التطبيق.'
        || E'\n';
    v_doc        TEXT;
    v_pos        INTEGER;
    v_n          INTEGER;
BEGIN
    -- The new text itself (a failure here is this file's bug → abort).
    IF v_section ~ '[0-9٠-٩۰-۹]' THEN
        RAISE EXCEPTION '192: the new pricing section contains a digit (M126 forbids any)';
    END IF;
    IF position('المشتركون الأوائل' IN v_section) > 0 THEN
        RAISE EXCEPTION '192: the new pricing section names «المشتركون الأوائل» (breaks 140''s marker count)';
    END IF;

    SELECT d.content_md INTO v_doc
      FROM public.product_docs d
     WHERE d.doc_key = 'pricing';

    IF NOT FOUND THEN
        RAISE NOTICE '192: no product_docs row doc_key=''pricing'' — section NOT written; paste it by hand.';
        RETURN;
    END IF;

    IF position(v_new_marker IN v_doc) > 0 THEN
        RAISE NOTICE '192: pricing doc already has %, nothing written (re-run).', v_new_marker;
        RETURN;
    END IF;

    IF md5(v_doc) <> v_live_md5 THEN
        RAISE NOTICE
            '192: product_docs.pricing md5 is % (length %), expected % (length 3062, '
            'read 2026-10-27) — the row changed since; section NOT replaced. Replace '
            '«%» by hand.',
            md5(v_doc), length(v_doc), v_live_md5, v_old_marker;
        RETURN;
    END IF;

    v_pos := position(v_old_marker IN v_doc);
    IF v_pos = 0 THEN
        RAISE NOTICE '192: pricing doc has no «%» heading — nothing replaced.', v_old_marker;
        RETURN;
    END IF;
    -- belt and braces under the md5: it must be the last section
    IF position(E'\n#' IN substr(v_doc, v_pos + length(v_old_marker))) > 0 THEN
        RAISE NOTICE '192: «%» is not the last section — nothing replaced; edit by hand.', v_old_marker;
        RETURN;
    END IF;

    UPDATE public.product_docs d
       SET content_md = left(d.content_md, v_pos - 1) || v_section
     WHERE d.doc_key = 'pricing'
       AND md5(d.content_md) = v_live_md5;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 1 THEN
        RAISE NOTICE '192: replaced «%» with «%» in product_docs.pricing (live within the 600s cache TTL).',
                     v_old_marker, v_new_marker;
    ELSE
        RAISE NOTICE '192: product_docs.pricing changed under us — nothing replaced.';
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 8. Verification — structure, seed, grants, signatures. Any failure aborts.
-- ════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    v_n    INTEGER;
    v_row  RECORD;
    v_pro  NUMERIC;
    v_max  NUMERIC;
    v_flag BOOLEAN;
BEGIN
    -- promo_batches: exists, RLS on, deny-all for clients, no policies, unique
    IF to_regclass('public.promo_batches') IS NULL THEN
        RAISE EXCEPTION '192: promo_batches missing';
    END IF;
    IF NOT (SELECT c.relrowsecurity FROM pg_class c
             WHERE c.oid = 'public.promo_batches'::regclass) THEN
        RAISE EXCEPTION '192: RLS is OFF on promo_batches';
    END IF;
    -- has_table_privilege, not information_schema (183's note: the latter can
    -- read empty under the migration role — a vacuous pass).
    IF has_table_privilege('anon', 'public.promo_batches', 'SELECT, INSERT, UPDATE, DELETE')
       OR has_table_privilege('authenticated', 'public.promo_batches',
                              'SELECT, INSERT, UPDATE, DELETE') THEN
        RAISE EXCEPTION '192: promo_batches is readable/writable by anon or authenticated';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.promo_batches', 'SELECT')
       OR NOT has_table_privilege('service_role', 'public.promo_batches', 'UPDATE') THEN
        RAISE EXCEPTION '192: service_role cannot read/update promo_batches';
    END IF;
    SELECT count(*) INTO v_n FROM pg_policies p
     WHERE p.schemaname = 'public' AND p.tablename = 'promo_batches';
    IF v_n <> 0 THEN
        RAISE EXCEPTION '192: promo_batches has % RLS polic(ies); expected none (deny-all)', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint con
                    WHERE con.conrelid = 'public.promo_batches'::regclass
                      AND con.contype = 'u'
                      AND pg_get_constraintdef(con.oid) = 'UNIQUE (plan_id, price_tier, batch_order)') THEN
        RAISE EXCEPTION '192: promo_batches lacks UNIQUE (plan_id, price_tier, batch_order)';
    END IF;

    -- columns
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'early_adopter_seats'
       AND column_name IN ('batch_id', 'plan_id', 'price_tier', 'price_sar',
                           'released_by_payment_id');
    IF v_n <> 5 THEN
        RAISE EXCEPTION '192: early_adopter_seats 192 columns missing (found % of 5)', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'payment_transactions'
       AND column_name IN ('promo_batch_id', 'credit_basis');
    IF v_n <> 2 THEN
        RAISE EXCEPTION '192: payment_transactions 192 columns missing (found % of 2)', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint con
                    WHERE con.conname = 'payment_transactions_credit_basis_check'
                      AND con.conrelid = 'public.payment_transactions'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint con
                       WHERE con.conname = 'early_adopter_seats_released_by_check'
                         AND con.conrelid = 'public.early_adopter_seats'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint con
                       WHERE con.conname = 'early_adopter_seats_released_by_payment_id_fkey'
                         AND con.conrelid = 'public.early_adopter_seats'::regclass) THEN
        RAISE EXCEPTION '192: credit_basis / released_by_payment_id constraints missing';
    END IF;

    -- (V7) clients: anon reads nothing; authenticated reads every column except
    -- the three quote internals — and still reads the ledger it always could.
    IF has_table_privilege('anon', 'public.payment_transactions', 'SELECT')
       OR has_any_column_privilege('anon', 'public.payment_transactions', 'SELECT') THEN
        RAISE EXCEPTION '192: anon can still SELECT payment_transactions';
    END IF;
    IF has_table_privilege('authenticated', 'public.payment_transactions', 'SELECT') THEN
        RAISE EXCEPTION '192: authenticated still holds TABLE-level SELECT on payment_transactions (column privileges are moot)';
    END IF;
    IF has_column_privilege('authenticated', 'public.payment_transactions', 'promo_batch_id', 'SELECT')
       OR has_column_privilege('authenticated', 'public.payment_transactions', 'intro_offer_terms', 'SELECT')
       OR has_column_privilege('authenticated', 'public.payment_transactions', 'credit_basis', 'SELECT') THEN
        RAISE EXCEPTION '192: authenticated can still read a hidden payment_transactions column';
    END IF;
    IF NOT has_column_privilege('authenticated', 'public.payment_transactions', 'amount_sar', 'SELECT')
       OR NOT has_column_privilege('authenticated', 'public.payment_transactions', 'payment_id', 'SELECT') THEN
        RAISE EXCEPTION '192: authenticated lost SELECT on the visible payment_transactions columns';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.payment_transactions', 'SELECT') THEN
        RAISE EXCEPTION '192: service_role lost SELECT on payment_transactions';
    END IF;
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'early_adopter_campaign'
       AND column_name IN ('ladder_v2', 'ladder_v2_at');
    IF v_n <> 2 THEN
        RAISE EXCEPTION '192: early_adopter_campaign.ladder_v2 / ladder_v2_at missing (found % of 2)', v_n;
    END IF;

    -- (R2) the flip stamp: trigger installed, trigger fn not callable by clients
    IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                    WHERE tg.tgrelid = 'public.early_adopter_campaign'::regclass
                      AND tg.tgname = 'trg_early_adopter_campaign_ladder_v2_at'
                      AND NOT tg.tgisinternal
                      AND tg.tgenabled <> 'D') THEN
        RAISE EXCEPTION '192: trg_early_adopter_campaign_ladder_v2_at missing or disabled';
    END IF;
    IF has_function_privilege('anon', 'public.stamp_ladder_v2_at()', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.stamp_ladder_v2_at()', 'EXECUTE') THEN
        RAISE EXCEPTION '192: anon/authenticated can EXECUTE stamp_ladder_v2_at()';
    END IF;

    -- (R3) the per-batch live-count index
    IF to_regclass('public.idx_early_adopter_seats_batch_live') IS NULL
       OR to_regclass('public.idx_early_adopter_seats_batch') IS NULL
       OR to_regclass('public.idx_early_adopter_seats_released_by') IS NULL THEN
        RAISE EXCEPTION '192: early_adopter_seats batch indexes missing';
    END IF;

    -- constraints
    IF NOT EXISTS (SELECT 1 FROM pg_constraint con
                    WHERE con.conname = 'early_adopter_seats_batch_id_fkey'
                      AND con.conrelid = 'public.early_adopter_seats'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint con
                       WHERE con.conname = 'payment_transactions_promo_batch_id_fkey'
                         AND con.conrelid = 'public.payment_transactions'::regclass) THEN
        RAISE EXCEPTION '192: a promo_batches foreign key is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint con
                    WHERE con.conname = 'early_adopter_seats_release_reason_check'
                      AND con.conrelid = 'public.early_adopter_seats'::regclass
                      AND pg_get_constraintdef(con.oid) LIKE '%upgraded%') THEN
        RAISE EXCEPTION '192: release_reason CHECK does not admit ''upgraded''';
    END IF;

    -- seed rows = §1.2. Strict while a row is unedited (updated_at =
    -- created_at — the trigger bumps updated_at on any later UPDATE); an
    -- operator's later edit is reported, not judged.
    FOR v_row IN
        SELECT v.*, b.batch_id AS got_id, b.plan_id AS got_plan, b.price_tier AS got_tier,
               b.batch_order AS got_order, b.price_sar AS got_price, b.months AS got_months,
               b.seat_limit AS got_limit, b.profession_only AS got_prof, b.enabled AS got_on,
               (b.updated_at = b.created_at) AS unedited
          FROM (VALUES
                ('pro_t1_a', 'pro', 1, 1,  19.90, 2,  500, true),
                ('pro_t1_b', 'pro', 1, 2,  19.90, 1,  500, true),
                ('pro_t2_a', 'pro', 2, 1,  59.90, 2,  500, false),
                ('max_t1_a', 'max', 1, 1, 149.90, 4,  100, false),
                ('max_t2_a', 'max', 2, 1, 189.90, 3,  400, false),
                ('max_t2_b', 'max', 2, 2, 189.90, 1, 1000, false)
               ) v(batch_id, plan_id, price_tier, batch_order, price_sar, months, seat_limit, profession_only)
          LEFT JOIN public.promo_batches b ON b.batch_id = v.batch_id
    LOOP
        IF v_row.got_id IS NULL THEN
            RAISE EXCEPTION '192: seed batch % is missing', v_row.batch_id;
        END IF;
        IF (v_row.got_plan, v_row.got_tier, v_row.got_order, v_row.got_price,
            v_row.got_months, v_row.got_limit, v_row.got_prof, v_row.got_on)
           IS DISTINCT FROM
           (v_row.plan_id, v_row.price_tier, v_row.batch_order, v_row.price_sar,
            v_row.months, v_row.seat_limit, v_row.profession_only, true) THEN
            IF v_row.unedited THEN
                RAISE EXCEPTION
                    '192: batch % = (%, %, %, %, %, %, %, %), expected the §1.2 seed',
                    v_row.batch_id, v_row.got_plan, v_row.got_tier, v_row.got_order,
                    v_row.got_price, v_row.got_months, v_row.got_limit, v_row.got_prof,
                    v_row.got_on;
            ELSE
                RAISE NOTICE '192: batch % was edited after seeding — kept as is', v_row.batch_id;
            END IF;
        END IF;
    END LOOP;

    -- plans.promo_price_sar is FROZEN: legacy seats are priced from it (§1.4-4).
    SELECT p.promo_price_sar INTO v_pro FROM public.plans p WHERE p.plan_id = 'pro';
    SELECT p.promo_price_sar INTO v_max FROM public.plans p WHERE p.plan_id = 'max';
    IF v_pro IS DISTINCT FROM 49.90 OR v_max IS DISTINCT FROM 99.90 THEN
        RAISE EXCEPTION
            '192: plans.promo_price_sar is pro % / max % — legacy seats are priced from '
            'it and it is frozen at 49.90 / 99.90. Find out who changed it first.',
            v_pro, v_max;
    END IF;

    -- functions: exactly one overload, the contract signature and return,
    -- service_role only
    FOR v_row IN
        SELECT * FROM (VALUES
            ('promo_tier_used',          'public.promo_tier_used(uuid, text, smallint)',
             'boolean'),
            ('promo_active_batch',       'public.promo_active_batch(text, smallint)',
             NULL),
            ('promo_quote',              'public.promo_quote(uuid, text)',
             'TABLE(batch_id text, price_tier smallint, price_sar numeric, months integer)'),
            ('promo_public_quote',       'public.promo_public_quote()',
             'TABLE(plan_id text, price_sar numeric, months integer)'),
            ('effective_plan_price',     'public.effective_plan_price(uuid, text, text, timestamptz)',
             'numeric'),
            ('claim_early_adopter_seat', 'public.claim_early_adopter_seat(uuid, uuid)',
             'TABLE(seat_id uuid, promo_ends_at timestamp with time zone, over_capacity boolean, action text)'),
            ('early_adopter_open',       'public.early_adopter_open()',
             'boolean'),
            ('intro_offer_quote',        'public.intro_offer_quote(uuid)',
             'TABLE(profession_group text, first_month_sar numeric)'),
            ('early_adopter_status',     'public.early_adopter_status(uuid)',
             'TABLE(campaign_open boolean, has_seat boolean, promo_ends_at timestamp with time zone)'),
            ('is_intro_offer_member',    'public.is_intro_offer_member(uuid)',
             'boolean'),
            ('release_early_adopter_seat', 'public.release_early_adopter_seat(uuid, text)',
             'boolean'),
            ('promo_checkout_price',     'public.promo_checkout_price(uuid, text)',
             'TABLE(ladder_v2 boolean, price_sar numeric, batch_id text, price_tier smallint, months integer, intro_group text)')
        ) f(fname, fsig, fresult)
    LOOP
        SELECT count(*) INTO v_n
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = v_row.fname;
        IF v_n <> 1 THEN
            RAISE EXCEPTION '192: public.% has % overload(s), expected exactly 1', v_row.fname, v_n;
        END IF;
        IF to_regprocedure(v_row.fsig) IS NULL THEN
            RAISE EXCEPTION '192: % does not have the signature %', v_row.fname, v_row.fsig;
        END IF;
        IF v_row.fresult IS NOT NULL
           AND pg_get_function_result(to_regprocedure(v_row.fsig)) IS DISTINCT FROM v_row.fresult THEN
            RAISE EXCEPTION '192: % returns %, expected %', v_row.fname,
                pg_get_function_result(to_regprocedure(v_row.fsig)), v_row.fresult;
        END IF;
        IF has_function_privilege('anon', v_row.fsig, 'EXECUTE')
           OR has_function_privilege('authenticated', v_row.fsig, 'EXECUTE') THEN
            RAISE EXCEPTION '192: anon/authenticated can EXECUTE %', v_row.fsig;
        END IF;
        IF NOT has_function_privilege('service_role', v_row.fsig, 'EXECUTE') THEN
            RAISE EXCEPTION '192: service_role cannot EXECUTE %', v_row.fsig;
        END IF;
        IF NOT (SELECT p.prosecdef FROM pg_proc p WHERE p.oid = to_regprocedure(v_row.fsig)) THEN
            RAISE EXCEPTION '192: % is not SECURITY DEFINER', v_row.fsig;
        END IF;
    END LOOP;

    IF NOT (SELECT p.proretset AND p.prorettype = 'public.promo_batches'::regtype
              FROM pg_proc p
             WHERE p.oid = to_regprocedure('public.promo_active_batch(text, smallint)')) THEN
        RAISE EXCEPTION '192: promo_active_batch must return SETOF promo_batches';
    END IF;

    SELECT c.ladder_v2 INTO v_flag FROM public.early_adopter_campaign c WHERE c.id;
    RAISE NOTICE '192: structure OK — ladder_v2 = %, ladder_v2_at = % (ADD COLUMN … DEFAULT false never flips an existing value)',
        v_flag, (SELECT c.ladder_v2_at FROM public.early_adopter_campaign c WHERE c.id);
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 9. Behavioural self-tests — flag ON, fake accounts, then ALL ROLLED BACK.
-- ════════════════════════════════════════════════════════════════════════════
-- Everything runs inside an inner BEGIN … EXCEPTION block that ends by raising
-- a private SQLSTATE (LU192) and catching it: PostgreSQL rolls the whole
-- subtransaction back — fake auth.users / users / user_subscriptions rows,
-- payments, seats, the ladder_v2 / enabled / intro switches and the batch
-- reshaping. Any real assertion failure raises P0001, is NOT caught, and aborts
-- the migration. Afterwards §10 proves nothing was left behind.
--
-- Fake accounts go through auth.users so on_auth_user_created builds their
-- public.users + user_subscriptions rows exactly as a signup does (users.auth_id
-- is NOT NULL → auth.users). Needs the postgres role (SQL editor); checked.
--
--   L1 legal       free, nothing held               → the positive controls
--   I1 individual  free                             → never pro tier 1
--   B1 legal       ACTIVE basic                     → never pro tier 1
--   U1 legal       released ('cancelled') pro_t1_a  → used tier 1 → tier 2
--   P1 individual  LEGACY pro seat, lapsed (free)   → 49.90
--   M1 legal       LEGACY max seat, ACTIVE max      → 99.90
--   T1 legal       live pro_t1_a seat, lapsed       → 19.90, pro only (I2)
--   S1 legal       LEGACY pro seat, ACTIVE pro      → switches to max (I3, I4)
--   F1 legal       filler seat in max_t1_a          → fills the batch
--   X1 legal       LEGACY seat from a 183 intro     → both pro tiers used (R1)
--                  first month (1.99), window spent
--   Y1 legal       LEGACY max seat, lapsed, in      → buying pro keeps the
--                  window                             legacy 49.90 (V3)
--   Z1 legal       pro_t1_a + legacy max seats,     → REFUNDED purchases: both
--                  released 'refund', payments        tiers quoted again (O1)
--                  'refunded'
--   W1 legal       the same seats, released         → refunded RENEWALS: both
--                  'refund', payments still 'paid'    tiers still used (O1)
--   (B1 also buys pro_t2_a in block E2 — the quoted terms survive a batch
--    edit (V1); S1's switch is refunded in block R — the pro seat comes back
--    (V5).)
--   (I1 also carries the R2 claims in block H: unstamped payments created
--    after vs before the flip.)
DO $selftest$
DECLARE
    -- residue snapshot
    v_seats0   BIGINT;
    v_pays0    BIGINT;
    v_users0   BIGINT;
    v_batches0 TEXT;
    v_camp0    TEXT;
    v_intro0   TEXT;
    v_flag0    BOOLEAN;
    v_flag1    BOOLEAN;
    -- test state
    v_run   TEXT := replace(gen_random_uuid()::text, '-', '');
    v_prof  TEXT[] := ARRAY['legal', 'individual', 'legal', 'legal', 'individual',
                            'legal', 'legal', 'legal', 'legal', 'legal', 'legal',
                            'legal', 'legal'];
    v_ids   UUID[] := ARRAY[]::uuid[];
    v_auth  UUID;
    v_id    UUID;
    v_i     INTEGER;
    v_l1 UUID; v_i1 UUID; v_b1 UUID; v_u1 UUID; v_p1 UUID;
    v_m1 UUID; v_t1 UUID; v_s1 UUID; v_f1 UUID; v_x1 UUID; v_y1 UUID;
    v_z1 UUID; v_w1 UUID;
    v_swpay  UUID;
    v_swseat UUID;
    v_yseat  UUID;
    v_flip  TIMESTAMPTZ;
    v_days  INTEGER;
    v_pay   UUID;
    v_old   UUID;
    v_c     RECORD;
    v_r     RECORD;
    v_s     TEXT;
    v_s2    TEXT;
    v_p     NUMERIC;
    v_n     BIGINT;
    v_bad   BIGINT;
    v_prol  NUMERIC;
    v_maxl  NUMERIC;
BEGIN
    SELECT count(*) INTO v_seats0 FROM public.early_adopter_seats;
    SELECT count(*) INTO v_pays0  FROM public.payment_transactions;
    SELECT count(*) INTO v_users0 FROM public.users;
    SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), ''))
      INTO v_batches0 FROM public.promo_batches b;
    SELECT md5(c::text), c.ladder_v2 INTO v_camp0, v_flag0
      FROM public.early_adopter_campaign c WHERE c.id;
    SELECT md5(c::text) INTO v_intro0 FROM public.intro_offer_campaign c WHERE c.id;
    SELECT p.price_sar INTO v_prol FROM public.plans p WHERE p.plan_id = 'pro';
    SELECT p.price_sar INTO v_maxl FROM public.plans p WHERE p.plan_id = 'max';

    IF NOT has_table_privilege('auth.users', 'INSERT') THEN
        RAISE EXCEPTION
            '192 self-test: this role cannot INSERT into auth.users (fake accounts '
            'for the behavioural tests). Run the migration as postgres (SQL editor).';
    END IF;

    BEGIN
        -- ── setup: switches on, batches into the §1.2 shape and wide open ──
        UPDATE public.early_adopter_campaign c SET enabled = true, ladder_v2 = true WHERE c.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION '192 self-test: early_adopter_campaign row missing';
        END IF;
        -- (R2) the trigger stamped the flip (or, on a re-run after the real
        -- flip, kept the real instant)
        SELECT c.ladder_v2_at, c.promo_days INTO v_flip, v_days
          FROM public.early_adopter_campaign c WHERE c.id;
        IF v_flip IS NULL
           OR (NOT v_flag0 AND v_flip IS DISTINCT FROM now()) THEN
            RAISE EXCEPTION '192 self-test [R2 trigger]: ladder_v2_at = % after the flip (expected %)',
                v_flip, now();
        END IF;
        UPDATE public.intro_offer_campaign c
           SET enabled = true, ends_at = now() + interval '1 day'
         WHERE c.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION '192 self-test: intro_offer_campaign row missing';
        END IF;

        -- Park every batch out of the way first (uniform offset keeps the
        -- UNIQUE intact), then put the six seed rows back exactly as §1.2 says,
        -- each with 1000 free seats. A re-run after operator edits still tests
        -- the rules, not today's numbers. Rolled back with everything else.
        UPDATE public.promo_batches b
           SET batch_order = b.batch_order + 100000, enabled = false
         WHERE b.batch_id IS NOT NULL;              -- explicit WHERE: pg-safeupdate
        UPDATE public.promo_batches b
           SET plan_id         = v.plan_id,
               price_tier      = v.price_tier,
               batch_order     = v.batch_order,
               price_sar       = v.price_sar,
               months          = v.months,
               profession_only = v.profession_only,
               enabled         = true,
               seat_limit      = (SELECT count(*)::integer
                                    FROM public.early_adopter_seats x
                                   WHERE x.released_at IS NULL
                                     AND x.batch_id = b.batch_id) + 1000
          FROM (VALUES
                ('pro_t1_a', 'pro', 1::smallint, 1,  19.90, 2, true),
                ('pro_t1_b', 'pro', 1::smallint, 2,  19.90, 1, true),
                ('pro_t2_a', 'pro', 2::smallint, 1,  59.90, 2, false),
                ('max_t1_a', 'max', 1::smallint, 1, 149.90, 4, false),
                ('max_t2_a', 'max', 2::smallint, 1, 189.90, 3, false),
                ('max_t2_b', 'max', 2::smallint, 2, 189.90, 1, false)
               ) v(batch_id, plan_id, price_tier, batch_order, price_sar, months, profession_only)
         WHERE b.batch_id = v.batch_id;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n <> 6 THEN
            RAISE EXCEPTION '192 self-test: reshaped % batches, expected 6', v_n;
        END IF;

        -- ── fake accounts ──
        FOR v_i IN 1 .. array_length(v_prof, 1) LOOP
            v_auth := gen_random_uuid();
            INSERT INTO auth.users (id, instance_id, aud, role, email,
                                    raw_app_meta_data, raw_user_meta_data,
                                    created_at, updated_at)
            VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated',
                    'authenticated',
                    format('selftest-192-%s-%s@selftest.invalid', v_run, v_i),
                    '{}'::jsonb, '{}'::jsonb, now(), now());

            SELECT u.user_id INTO v_id FROM public.users u WHERE u.auth_id = v_auth;
            IF v_id IS NULL THEN
                RAISE EXCEPTION '192 self-test: on_auth_user_created built no public.users row';
            END IF;

            UPDATE public.users u SET profession_group = v_prof[v_i] WHERE u.user_id = v_id;
            IF (SELECT u.profession_first_group FROM public.users u WHERE u.user_id = v_id)
               IS DISTINCT FROM v_prof[v_i] THEN
                RAISE EXCEPTION '192 self-test: profession_first_group not stamped for fake user %', v_i;
            END IF;
            v_ids := v_ids || v_id;
        END LOOP;

        v_l1 := v_ids[1]; v_i1 := v_ids[2]; v_b1 := v_ids[3]; v_u1 := v_ids[4];
        v_p1 := v_ids[5]; v_m1 := v_ids[6]; v_t1 := v_ids[7]; v_s1 := v_ids[8];
        v_f1 := v_ids[9]; v_x1 := v_ids[10]; v_y1 := v_ids[11];
        v_z1 := v_ids[12]; v_w1 := v_ids[13];

        -- subscriptions (the assignment trigger stamps expires_at from the plan)
        UPDATE public.user_subscriptions s SET plan_id = 'basic' WHERE s.user_id = v_b1;
        UPDATE public.user_subscriptions s SET plan_id = 'max'   WHERE s.user_id = v_m1;
        UPDATE public.user_subscriptions s SET plan_id = 'pro'   WHERE s.user_id = v_s1;
        -- one read per user, by user_id (never a join: the view's quota
        -- lateral must not run for every account in prod)
        SELECT l.effective_plan_id INTO v_s FROM public.user_subscriptions_live l WHERE l.user_id = v_b1;
        IF v_s IS DISTINCT FROM 'basic' THEN
            RAISE EXCEPTION '192 self-test setup: B1 effective plan is %, expected basic', v_s;
        END IF;
        SELECT l.effective_plan_id INTO v_s FROM public.user_subscriptions_live l WHERE l.user_id = v_m1;
        IF v_s IS DISTINCT FROM 'max' THEN
            RAISE EXCEPTION '192 self-test setup: M1 effective plan is %, expected max', v_s;
        END IF;
        SELECT l.effective_plan_id INTO v_s FROM public.user_subscriptions_live l WHERE l.user_id = v_s1;
        IF v_s IS DISTINCT FROM 'pro' THEN
            RAISE EXCEPTION '192 self-test setup: S1 effective plan is %, expected pro', v_s;
        END IF;
        SELECT l.effective_plan_id INTO v_s FROM public.user_subscriptions_live l WHERE l.user_id = v_l1;
        IF v_s IS DISTINCT FROM 'free' THEN
            RAISE EXCEPTION '192 self-test setup: L1 effective plan is %, expected free', v_s;
        END IF;

        -- seats and their payments
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, released_at, release_reason,
             batch_id, plan_id, price_tier, price_sar)
        VALUES (v_u1, now() - interval '20 days', now() + interval '40 days',
                now() - interval '1 day', 'cancelled', 'pro_t1_a', 'pro', 1, 19.90);

        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_p1, 'pro', 49.90, 'paid', now() - interval '10 days')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_p1, v_pay, now() - interval '10 days', now() + interval '80 days');

        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_m1, 'max', 99.90, 'paid', now() - interval '10 days')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_m1, v_pay, now() - interval '10 days', now() + interval '80 days');

        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_t1, now() - interval '5 days', now() + interval '55 days',
                'pro_t1_a', 'pro', 1, 19.90);

        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_s1, 'pro', 49.90, 'paid', now() - interval '10 days')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_s1, v_pay, now() - interval '10 days', now() + interval '80 days')
        RETURNING seat_id INTO v_old;

        -- X1: a legacy seat claimed by a 183 intro first month (1.99), its
        -- 90 days spent but the seat still live (lapsed, never released)
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, intro_offer_group)
        VALUES (v_x1, 'pro', 1.99, 'paid', now() - interval '100 days', 'legal')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_x1, v_pay, now() - interval '100 days', now() - interval '10 days');

        -- Y1: a legacy MAX seat (99.90 payment), lapsed (free), window running
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_y1, 'max', 99.90, 'paid', now() - interval '20 days')
        RETURNING payment_id INTO v_pay;
        INSERT INTO public.early_adopter_seats (user_id, payment_id, claimed_at, promo_ends_at)
        VALUES (v_y1, v_pay, now() - interval '20 days', now() + interval '70 days')
        RETURNING seat_id INTO v_yseat;

        -- Z1 / W1 (O1): the same two seats each — a pro_t1_a batch seat and a
        -- legacy max seat, both released 'refund'. Z1's CLAIMING payments were
        -- refunded (the purchases themselves); W1's are still 'paid' (a later
        -- renewal was what got refunded).
        FOR v_i IN 1 .. 2 LOOP
            v_id := CASE v_i WHEN 1 THEN v_z1 ELSE v_w1 END;
            v_s  := CASE v_i WHEN 1 THEN 'refunded' ELSE 'paid' END;

            INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
            VALUES (v_id, 'pro', 19.90, v_s, now() - interval '30 days')
            RETURNING payment_id INTO v_pay;
            INSERT INTO public.early_adopter_seats
                (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason,
                 batch_id, plan_id, price_tier, price_sar)
            VALUES (v_id, v_pay, now() - interval '30 days', now() + interval '30 days',
                    now() - interval '29 days', 'refund', 'pro_t1_a', 'pro', 1, 19.90);

            INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
            VALUES (v_id, 'max', 99.90, v_s, now() - interval '60 days')
            RETURNING payment_id INTO v_pay;
            INSERT INTO public.early_adopter_seats
                (user_id, payment_id, claimed_at, promo_ends_at, released_at, release_reason)
            VALUES (v_id, v_pay, now() - interval '60 days', now() + interval '30 days',
                    now() - interval '59 days', 'refund');
        END LOOP;

        -- ── A. legacy seats price exactly as today (§1.4-4) ──
        v_p := public.effective_plan_price(v_p1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [A legacy pro, renewal]: % (expected 49.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_p1, 'pro', 'purchase');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [A legacy pro, purchase]: % (expected 49.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_m1, 'max', 'current');
        IF v_p IS DISTINCT FROM 99.90 THEN
            RAISE EXCEPTION '192 self-test [A legacy max, renewal]: % (expected 99.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_m1, 'max', 'purchase');
        IF v_p IS DISTINCT FROM 99.90 THEN
            RAISE EXCEPTION '192 self-test [A legacy max, purchase]: % (expected 99.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_s1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [A legacy pro on pro, renewal]: % (expected 49.90)', v_p;
        END IF;
        -- and every REAL live legacy seat, under the flag, in both contexts
        SELECT count(*) FILTER (
                   WHERE public.effective_plan_price(s.user_id, t.plan_id, 'current')
                             IS DISTINCT FROM round(p.promo_price_sar, 2)
                      OR public.effective_plan_price(s.user_id, t.plan_id, 'purchase')
                             IS DISTINCT FROM round(p.promo_price_sar, 2)),
               count(*)
          INTO v_bad, v_n
          FROM public.early_adopter_seats s
          JOIN public.payment_transactions t ON t.payment_id = s.payment_id
          JOIN public.plans p ON p.plan_id = t.plan_id
         WHERE s.released_at IS NULL
           AND s.batch_id IS NULL
           AND s.user_id IS NOT NULL
           AND s.promo_ends_at > now()
           AND p.promo_price_sar IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM public.early_adopter_seats x
                            WHERE x.user_id = s.user_id AND x.release_reason = 'cancelled');
        IF v_bad <> 0 THEN
            RAISE EXCEPTION '192 self-test [A all legacy seats, flag ON]: % of % mispriced', v_bad, v_n;
        END IF;

        -- ── B. a batch seat prices its own plan only (and keeps pricing it) ──
        v_p := public.effective_plan_price(v_t1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '192 self-test [B batch seat, own plan, renewal]: % (expected 19.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_t1, 'pro', 'purchase');
        IF v_p IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '192 self-test [B batch seat, own plan, purchase]: % (expected 19.90)', v_p;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_t1, 'pro') q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [B live in-window seat ⇒ no new pro quote (I2)]: got %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_t1, 'max', 'current');
        IF v_p IS DISTINCT FROM v_maxl THEN
            RAISE EXCEPTION '192 self-test [B pro batch seat must not price max]: % (expected list %)', v_p, v_maxl;
        END IF;
        v_p := public.effective_plan_price(v_t1, 'max', 'purchase');
        IF v_p IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '192 self-test [B pro seat holder buying max]: % (expected 149.90)', v_p;
        END IF;

        -- ── C. the quote matrix ──
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_l1, 'pro') q;
        IF v_s IS DISTINCT FROM 'pro_t1_a|1|19.90|2' THEN
            RAISE EXCEPTION '192 self-test [C lawyer → pro tier 1]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_l1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C anyone → max tier 1]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s', q.profession_group, q.first_month_sar), ',')
          INTO v_s FROM public.intro_offer_quote(v_l1) q;
        IF v_s IS DISTINCT FROM 'legal|19.90' THEN
            RAISE EXCEPTION '192 self-test [C intro_offer_quote wraps pro tier 1]: got %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_l1, 'pro', 'purchase');
        IF v_p IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '192 self-test [C purchase price = the quote]: % (expected 19.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_l1, 'pro', 'current');
        IF v_p IS DISTINCT FROM v_prol THEN
            RAISE EXCEPTION '192 self-test [C ''current'' never quotes]: % (expected list %)', v_p, v_prol;
        END IF;

        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_i1, 'pro') q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' THEN
            RAISE EXCEPTION '192 self-test [C individual is never pro tier 1]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s', q.profession_group, q.first_month_sar), ',')
          INTO v_s FROM public.intro_offer_quote(v_i1) q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C individual has no intro offer]: got %', v_s;
        END IF;

        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_b1, 'pro') q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' THEN
            RAISE EXCEPTION '192 self-test [C active basic is never pro tier 1]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_b1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C active basic still gets max tier 1]: got %', v_s;
        END IF;

        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_u1, 'pro') q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' THEN
            RAISE EXCEPTION '192 self-test [C used pro tier 1 (cancelled) → tier 2]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_u1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C a pro cancellation never closes max]: got %', v_s;
        END IF;

        IF NOT public.promo_tier_used(v_u1, 'pro', 1::smallint)
           OR public.promo_tier_used(v_u1, 'pro', 2::smallint)
           OR NOT public.promo_tier_used(v_p1, 'pro', 2::smallint)
           OR public.promo_tier_used(v_p1, 'pro', 1::smallint)
           OR public.promo_tier_used(v_p1, 'max', 1::smallint)
           OR NOT public.promo_tier_used(v_m1, 'max', 1::smallint)
           OR public.promo_tier_used(v_m1, 'max', 2::smallint)
           OR public.promo_tier_used(v_l1, 'pro', 1::smallint) THEN
            RAISE EXCEPTION '192 self-test [C promo_tier_used / legacy mapping] failed';
        END IF;

        -- (R1) a legacy seat from a 183 intro first month uses BOTH pro tiers,
        -- and nothing on max
        IF NOT public.promo_tier_used(v_x1, 'pro', 1::smallint)
           OR NOT public.promo_tier_used(v_x1, 'pro', 2::smallint)
           OR public.promo_tier_used(v_x1, 'max', 1::smallint) THEN
            RAISE EXCEPTION '192 self-test [C R1 intro legacy seat ⇒ pro tiers 1 and 2 used] failed';
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_x1, 'pro') q;
        SELECT string_agg(q.profession_group, ',') INTO v_s2 FROM public.intro_offer_quote(v_x1) q;
        IF v_s IS NOT NULL OR v_s2 IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C R1 past intro buyer gets no pro offer]: quote %, intro %', v_s, v_s2;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_x1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C R1 past intro buyer still gets max tier 1]: got %', v_s;
        END IF;

        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_m1, 'max') q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C active max → no max quote]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_m1, 'pro') q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C active max → never a pro quote]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_s1, 'pro') q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C active pro → no pro quote]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_s1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C legacy pro switcher → max quote (A3)]: got %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_s1, 'max', 'purchase');
        IF v_p IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '192 self-test [C switch price = the max quote, not the legacy 99.90 (I3)]: %', v_p;
        END IF;

        SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id)
          INTO v_s FROM public.promo_public_quote() q;
        IF v_s IS DISTINCT FROM 'max|149.90|4,pro|59.90|2' THEN
            RAISE EXCEPTION '192 self-test [C public quote]: got %', v_s;
        END IF;
        IF NOT public.early_adopter_open() THEN
            RAISE EXCEPTION '192 self-test [C early_adopter_open with open batches] returned false';
        END IF;

        -- ── C2. (V2) promo_checkout_price = the quote + its stamp, one call ──
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_l1, 'pro') r;
        IF v_s IS DISTINCT FROM 'true|19.90|pro_t1_a|1|2|legal' THEN
            RAISE EXCEPTION '192 self-test [C2 lawyer pro checkout]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_i1, 'pro') r;
        IF v_s IS DISTINCT FROM 'true|59.90|pro_t2_a|2|2|' THEN
            RAISE EXCEPTION '192 self-test [C2 tier 2 carries no intro group]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_l1, 'max') r;
        IF v_s IS DISTINCT FROM 'true|149.90|max_t1_a|1|4|' THEN
            RAISE EXCEPTION '192 self-test [C2 max tier 1 carries no intro group]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_p1, 'pro') r;
        IF v_s IS DISTINCT FROM 'true|49.90||||' THEN
            RAISE EXCEPTION '192 self-test [C2 legacy fallback, nothing stamped]: got %', v_s;
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_t1, 'pro') r;
        IF v_s IS DISTINCT FROM 'true|19.90||||' THEN
            RAISE EXCEPTION '192 self-test [C2 own batch seat fallback]: got %', v_s;
        END IF;
        SELECT count(*) INTO v_n FROM public.promo_checkout_price(v_l1, 'no_such_plan');
        IF v_n <> 0 THEN
            RAISE EXCEPTION '192 self-test [C2 unknown plan ⇒ no row]: % row(s)', v_n;
        END IF;
        -- the checkout price IS the purchase price, for every fake user × plan
        SELECT count(*) INTO v_bad
          FROM unnest(ARRAY[v_l1, v_i1, v_b1, v_u1, v_p1, v_m1, v_t1, v_s1, v_x1, v_y1,
                            v_z1, v_w1]) AS x(uid)
          CROSS JOIN (VALUES ('pro'), ('max'), ('basic')) AS pl(pid)
          CROSS JOIN LATERAL public.promo_checkout_price(x.uid, pl.pid) r
         WHERE r.price_sar IS DISTINCT FROM public.effective_plan_price(x.uid, pl.pid, 'purchase');
        IF v_bad <> 0 THEN
            RAISE EXCEPTION '192 self-test [C2 promo_checkout_price ≠ effective_plan_price] in % case(s)', v_bad;
        END IF;

        -- ── C3. (V3) a lapsed LEGACY MAX holder buying pro keeps the legacy
        --        49.90: no quote, and the claim keeps (does not release) the seat
        SELECT string_agg(q.batch_id, ',') INTO v_s  FROM public.promo_quote(v_y1, 'pro') q;
        SELECT string_agg(q.batch_id, ',') INTO v_s2 FROM public.promo_quote(v_y1, 'max') q;
        IF v_s IS NOT NULL OR v_s2 IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C3 legacy max holder must not be quoted]: pro %, max %', v_s, v_s2;
        END IF;
        v_p := public.effective_plan_price(v_y1, 'pro', 'purchase');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [C3 legacy max holder buying pro]: % (expected 49.90)', v_p;
        END IF;
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_y1, 'pro', 49.90, 'paid', now())
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_y1, v_pay);
        IF v_c.action IS DISTINCT FROM 'already_claimed' OR v_c.seat_id IS DISTINCT FROM v_yseat
           OR (SELECT s.released_at FROM public.early_adopter_seats s WHERE s.seat_id = v_yseat) IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C3 legacy seat carries over, not released]: action %, seat %',
                v_c.action, v_c.seat_id;
        END IF;

        -- ── C4. (O1) a refunded PURCHASE is not a used tier; a refunded
        --        RENEWAL is (batch and legacy seats alike) ──
        IF public.promo_tier_used(v_z1, 'pro', 1::smallint)
           OR public.promo_tier_used(v_z1, 'max', 1::smallint) THEN
            RAISE EXCEPTION '192 self-test [C4 refunded purchases must not count as used]';
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_z1, 'pro') q;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s2 FROM public.promo_quote(v_z1, 'max') q;
        IF v_s IS DISTINCT FROM 'pro_t1_a|1|19.90|2' OR v_s2 IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C4 refunded purchase ⇒ same tier quoted again]: pro %, max %', v_s, v_s2;
        END IF;
        IF NOT public.promo_tier_used(v_w1, 'pro', 1::smallint)
           OR NOT public.promo_tier_used(v_w1, 'max', 1::smallint) THEN
            RAISE EXCEPTION '192 self-test [C4 refunded renewals must still count as used]';
        END IF;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_w1, 'pro') q;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s2 FROM public.promo_quote(v_w1, 'max') q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' OR v_s2 IS DISTINCT FROM 'max_t2_a|2|189.90|3' THEN
            RAISE EXCEPTION '192 self-test [C4 refunded renewal ⇒ tier stays used]: pro %, max %', v_s, v_s2;
        END IF;

        -- campaign switched off ⇒ nothing is quoted anywhere
        UPDATE public.early_adopter_campaign c SET enabled = false WHERE c.id;
        SELECT string_agg(q.batch_id, ',') INTO v_s FROM public.promo_quote(v_l1, 'pro') q;
        SELECT string_agg(q.plan_id, ',') INTO v_s2 FROM public.promo_public_quote() q;
        IF v_s IS NOT NULL OR v_s2 IS NOT NULL OR public.early_adopter_open() THEN
            RAISE EXCEPTION '192 self-test [C campaign off ⇒ silent]: quote %, public %', v_s, v_s2;
        END IF;
        UPDATE public.early_adopter_campaign c SET enabled = true WHERE c.id;

        -- intro window over ⇒ the lawyer falls to tier 2; no intro offer
        UPDATE public.intro_offer_campaign c SET ends_at = now() - interval '1 second' WHERE c.id;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_l1, 'pro') q;
        SELECT string_agg(q.profession_group, ',') INTO v_s2 FROM public.intro_offer_quote(v_l1) q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' OR v_s2 IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [C intro window over]: quote %, intro %', v_s, v_s2;
        END IF;
        UPDATE public.intro_offer_campaign c SET ends_at = now() + interval '1 day' WHERE c.id;

        -- intro switch OFF (inside the window) ⇒ same: tier 1 pro needs BOTH
        -- intro_offer_campaign.enabled AND now() < ends_at; max tier 1 does not
        UPDATE public.intro_offer_campaign c SET enabled = false WHERE c.id;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_l1, 'pro') q;
        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s2 FROM public.promo_quote(v_l1, 'max') q;
        IF v_s IS DISTINCT FROM 'pro_t2_a|2|59.90|2' OR v_s2 IS DISTINCT FROM 'max_t1_a|1|149.90|4' THEN
            RAISE EXCEPTION '192 self-test [C intro switch off]: pro %, max %', v_s, v_s2;
        END IF;
        UPDATE public.intro_offer_campaign c SET enabled = true WHERE c.id;

        -- ── D. the v2 claim: Pro → Max releases the pro seat as 'upgraded' ──
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, upgrade_credit_sar, status, paid_at, promo_batch_id)
        VALUES (v_s1, 'max', 100.00, 49.90, 'paid', now(), 'max_t1_a')
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_s1, v_pay);
        IF v_c.action IS DISTINCT FROM 'claimed' OR v_c.seat_id IS NULL
           OR v_c.over_capacity IS DISTINCT FROM false
           OR v_c.promo_ends_at IS DISTINCT FROM now() + make_interval(days => 120) THEN
            RAISE EXCEPTION '192 self-test [D switch claim]: action %, seat %, over %, ends %',
                v_c.action, v_c.seat_id, v_c.over_capacity, v_c.promo_ends_at;
        END IF;
        v_swpay  := v_pay;                     -- refunded in block R (V5)
        v_swseat := v_c.seat_id;
        SELECT s.released_at IS NOT NULL AS rel, s.release_reason AS why,
               s.released_by_payment_id AS by_pay
          INTO v_r FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF NOT v_r.rel OR v_r.why IS DISTINCT FROM 'upgraded'
           OR v_r.by_pay IS DISTINCT FROM v_swpay THEN
            RAISE EXCEPTION '192 self-test [D old pro seat]: released %, reason %, by % (expected upgraded by %)',
                v_r.rel, v_r.why, v_r.by_pay, v_swpay;
        END IF;
        SELECT format('%s|%s|%s|%s|%s', s.batch_id, s.plan_id, s.price_tier, s.price_sar,
                      s.released_at IS NULL)
          INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_c.seat_id;
        IF v_s IS DISTINCT FROM 'max_t1_a|max|1|149.90|true' THEN
            RAISE EXCEPTION '192 self-test [D new seat terms]: %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_s1, 'max', 'current');
        IF v_p IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '192 self-test [D max renewal after the switch]: % (expected 149.90)', v_p;
        END IF;
        IF NOT public.promo_tier_used(v_s1, 'pro', 2::smallint)
           OR NOT public.promo_tier_used(v_s1, 'max', 1::smallint) THEN
            RAISE EXCEPTION '192 self-test [D upgraded/claimed tiers must count as used]';
        END IF;
        IF NOT (SELECT e.has_seat FROM public.early_adopter_status(v_s1) e) THEN
            RAISE EXCEPTION '192 self-test [D early_adopter_status after the switch] has_seat = false';
        END IF;
        SELECT * INTO v_r FROM public.claim_early_adopter_seat(v_s1, v_pay);
        IF v_r.action IS DISTINCT FROM 'already_claimed' OR v_r.seat_id IS DISTINCT FROM v_c.seat_id THEN
            RAISE EXCEPTION '192 self-test [D replay]: action %, seat % (expected already_claimed %)',
                v_r.action, v_r.seat_id, v_c.seat_id;
        END IF;

        -- ── E. the amount gate is per batch ──
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_i1, 'pro', 94.90, 'paid', now(), 'pro_t2_a')
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_i1, v_pay);
        IF v_c.action IS DISTINCT FROM 'not_promo_priced' OR v_c.seat_id IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [E list-priced payment on a batch]: action %, seat %',
                v_c.action, v_c.seat_id;
        END IF;

        -- ── E2. (V1) the claim freezes the QUOTED terms, not the batch row ──
        -- checkout quoted pro_t2_a at 59.90 for 2 months and stamped it …
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_b1, 'pro', 59.90, 'paid', now(), 'pro_t2_a',
                jsonb_build_object('offer_sar', '59.90', 'offer_months', 2,
                                   'first_month_sar', '59.90', 'then_sar', '59.90',
                                   'then_months', 1, 'list_sar', v_prol::text))
        RETURNING payment_id INTO v_pay;
        -- … then an operator re-prices the batch before the money lands …
        UPDATE public.promo_batches b SET price_sar = 69.90, months = 3
         WHERE b.batch_id = 'pro_t2_a';
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_b1, v_pay);
        SELECT format('%s|%s', s.price_sar, s.promo_ends_at = now() + make_interval(days => 60))
          INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_c.seat_id;
        IF v_c.action IS DISTINCT FROM 'claimed' OR v_s IS DISTINCT FROM '59.90|true' THEN
            RAISE EXCEPTION '192 self-test [E2 quoted terms survive a batch edit]: action %, seat %',
                v_c.action, v_s;
        END IF;
        -- … and a payment over the QUOTED price is still refused even though the
        -- edited batch row would now admit it
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id, intro_offer_terms)
        VALUES (v_i1, 'pro', 69.90, 'paid', now(), 'pro_t2_a',
                jsonb_build_object('offer_sar', '59.90', 'offer_months', 2))
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_i1, v_pay);
        IF v_c.action IS DISTINCT FROM 'not_promo_priced' THEN
            RAISE EXCEPTION '192 self-test [E2 gate uses the quoted price]: action %', v_c.action;
        END IF;
        UPDATE public.promo_batches b SET price_sar = 59.90, months = 2
         WHERE b.batch_id = 'pro_t2_a';

        -- ── F. fill max_t1_a → the quote rolls to max_t2_a; the promise holds ──
        INSERT INTO public.early_adopter_seats
            (user_id, claimed_at, promo_ends_at, batch_id, plan_id, price_tier, price_sar)
        VALUES (v_f1, now() - interval '1 day', now() + interval '119 days',
                'max_t1_a', 'max', 1, 149.90);
        UPDATE public.promo_batches b
           SET seat_limit = (SELECT count(*)::integer
                               FROM public.early_adopter_seats x
                              WHERE x.released_at IS NULL
                                AND x.batch_id = 'max_t1_a')
         WHERE b.batch_id = 'max_t1_a';

        SELECT string_agg(format('%s|%s|%s|%s', q.batch_id, q.price_tier, q.price_sar, q.months), ',')
          INTO v_s FROM public.promo_quote(v_l1, 'max') q;
        IF v_s IS DISTINCT FROM 'max_t2_a|2|189.90|3' THEN
            RAISE EXCEPTION '192 self-test [F full max_t1_a → max_t2_a]: got %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_l1, 'max', 'purchase');
        IF v_p IS DISTINCT FROM 189.90 THEN
            RAISE EXCEPTION '192 self-test [F purchase price after the fill]: % (expected 189.90)', v_p;
        END IF;
        SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id)
          INTO v_s FROM public.promo_public_quote() q;
        IF v_s IS DISTINCT FROM 'max|189.90|3,pro|59.90|2' THEN
            RAISE EXCEPTION '192 self-test [F public quote after the fill]: got %', v_s;
        END IF;
        -- a max_t1_a quote issued before the fill settles after it
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, promo_batch_id)
        VALUES (v_l1, 'max', 149.90, 'paid', now(), 'max_t1_a')
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_l1, v_pay);
        IF v_c.action IS DISTINCT FROM 'claimed' OR v_c.over_capacity IS DISTINCT FROM true THEN
            RAISE EXCEPTION '192 self-test [F quote is the promise]: action %, over_capacity %',
                v_c.action, v_c.over_capacity;
        END IF;
        v_p := public.effective_plan_price(v_l1, 'max', 'current');
        IF v_p IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '192 self-test [F over-capacity seat keeps its batch price]: %', v_p;
        END IF;

        -- ── H. (R2) no new LEGACY seat from a payment created after the flip ──
        -- (the first claim below prints ONE expected WARNING)
        -- unstamped, priced at the legacy promo, created now() ≥ ladder_v2_at
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_i1, 'pro', 49.90, 'paid', now())
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_i1, v_pay);
        IF v_c.action IS DISTINCT FROM 'plan_not_eligible' OR v_c.seat_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM public.early_adopter_seats s WHERE s.payment_id = v_pay) THEN
            RAISE EXCEPTION '192 self-test [H unstamped promo payment after the flip]: action %, seat %',
                v_c.action, v_c.seat_id;
        END IF;
        -- unstamped, list-priced, after the flip: today's answer, untouched
        INSERT INTO public.payment_transactions (user_id, plan_id, amount_sar, status, paid_at)
        VALUES (v_i1, 'pro', 94.90, 'paid', now())
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_i1, v_pay);
        IF v_c.action IS DISTINCT FROM 'not_promo_priced' OR v_c.seat_id IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [H list-priced sale after the flip]: action %, seat %',
                v_c.action, v_c.seat_id;
        END IF;
        -- unstamped, priced at the legacy promo, created BEFORE the flip: the
        -- quote is the promise — today's legacy seat, promo_days long
        INSERT INTO public.payment_transactions
            (user_id, plan_id, amount_sar, status, paid_at, created_at)
        VALUES (v_i1, 'pro', 49.90, 'paid', now(), v_flip - interval '1 hour')
        RETURNING payment_id INTO v_pay;
        SELECT * INTO v_c FROM public.claim_early_adopter_seat(v_i1, v_pay);
        IF v_c.action IS DISTINCT FROM 'claimed' OR v_c.seat_id IS NULL
           OR v_c.promo_ends_at IS DISTINCT FROM now() + make_interval(days => v_days) THEN
            RAISE EXCEPTION '192 self-test [H pre-flip quote keeps the legacy claim]: action %, seat %, ends %',
                v_c.action, v_c.seat_id, v_c.promo_ends_at;
        END IF;
        IF (SELECT s.batch_id FROM public.early_adopter_seats s WHERE s.seat_id = v_c.seat_id) IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [H pre-flip seat must be LEGACY (batch_id NULL)]';
        END IF;

        -- ── G. flag OFF again (the rollback): held seats keep their terms ──
        UPDATE public.early_adopter_campaign c SET ladder_v2 = false WHERE c.id;
        IF (SELECT c.ladder_v2_at FROM public.early_adopter_campaign c WHERE c.id)
           IS DISTINCT FROM v_flip THEN
            RAISE EXCEPTION '192 self-test [R2 trigger]: ladder_v2_at changed when the flag went OFF (must never clear)';
        END IF;
        SELECT string_agg(q.batch_id, ',') INTO v_s FROM public.promo_quote(v_i1, 'pro') q;
        IF v_s IS NOT NULL THEN
            RAISE EXCEPTION '192 self-test [G flag off ⇒ promo_quote silent]: got %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_t1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 19.90 THEN
            RAISE EXCEPTION '192 self-test [G batch seat survives the rollback]: % (expected 19.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_s1, 'max', 'current');
        IF v_p IS DISTINCT FROM 149.90 THEN
            RAISE EXCEPTION '192 self-test [G switched max seat survives the rollback]: % (expected 149.90)', v_p;
        END IF;
        IF NOT (SELECT e.has_seat FROM public.early_adopter_status(v_s1) e) THEN
            RAISE EXCEPTION '192 self-test [G ''upgraded'' must not forfeit] has_seat = false';
        END IF;
        v_p := public.effective_plan_price(v_p1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [G legacy pro, flag off]: % (expected 49.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_u1, 'pro', 'purchase');
        IF v_p IS DISTINCT FROM v_prol THEN
            RAISE EXCEPTION '192 self-test [G flag off restores 183 forfeiture]: % (expected list %)', v_p, v_prol;
        END IF;
        -- (V4) flag off: a pro batch seat carries over to MAX at the legacy promo
        v_p := public.effective_plan_price(v_t1, 'max', 'current');
        IF v_p IS DISTINCT FROM 99.90 THEN
            RAISE EXCEPTION '192 self-test [G batch seat carries over to the other plan]: % (expected 99.90)', v_p;
        END IF;
        v_p := public.effective_plan_price(v_t1, 'max', 'purchase');
        IF v_p IS DISTINCT FROM 99.90 THEN
            RAISE EXCEPTION '192 self-test [G batch seat carry-over, purchase]: % (expected 99.90)', v_p;
        END IF;
        -- (V2) flag off: promo_checkout_price is effective_plan_price, unstamped
        SELECT string_agg(format('%s|%s|%s|%s|%s|%s', r.ladder_v2, r.price_sar, r.batch_id,
                                 r.price_tier, r.months, r.intro_group), ',')
          INTO v_s FROM public.promo_checkout_price(v_i1, 'pro') r;
        IF v_s IS DISTINCT FROM format('false|%s||||', public.effective_plan_price(v_i1, 'pro', 'purchase')) THEN
            RAISE EXCEPTION '192 self-test [G flag-off checkout price]: got %', v_s;
        END IF;

        -- ── R. (V5) refunding the switch payment gives the pro seat back ──
        -- (the backend stamps status = 'refunded' before it releases)
        UPDATE public.payment_transactions t SET status = 'refunded' WHERE t.payment_id = v_swpay;
        IF NOT public.release_early_adopter_seat(v_s1, 'refund') THEN
            RAISE EXCEPTION '192 self-test [R refund released nothing]';
        END IF;
        SELECT format('%s|%s', s.release_reason, s.released_at IS NOT NULL)
          INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_swseat;
        IF v_s IS DISTINCT FROM 'refund|true' THEN
            RAISE EXCEPTION '192 self-test [R the refunded max seat]: %', v_s;
        END IF;
        SELECT format('%s|%s|%s', s.released_at IS NULL, s.release_reason, s.released_by_payment_id)
          INTO v_s FROM public.early_adopter_seats s WHERE s.seat_id = v_old;
        IF v_s IS DISTINCT FROM 'true||' THEN
            RAISE EXCEPTION '192 self-test [R the superseded pro seat is live again]: %', v_s;
        END IF;
        v_p := public.effective_plan_price(v_s1, 'pro', 'current');
        IF v_p IS DISTINCT FROM 49.90 THEN
            RAISE EXCEPTION '192 self-test [R restored legacy pro seat prices again]: % (expected 49.90)', v_p;
        END IF;
        -- a second refund call finds the restored pro seat live: it releases it
        -- (today's rule) but its own payment is still 'paid' → nothing comes back
        IF NOT public.release_early_adopter_seat(v_s1, 'refund')
           OR (SELECT s.release_reason FROM public.early_adopter_seats s WHERE s.seat_id = v_old)
              IS DISTINCT FROM 'refund'
           OR EXISTS (SELECT 1 FROM public.early_adopter_seats s
                       WHERE s.user_id = v_s1 AND s.released_at IS NULL) THEN
            RAISE EXCEPTION '192 self-test [R refund of an unrelated payment gives nothing back]';
        END IF;
        -- (O1) end to end: the refunded switch is as if never bought (max tier 1
        -- open again); the pro seat's purchase was never refunded, only a later
        -- charge, so pro tier 2 stays used
        IF public.promo_tier_used(v_s1, 'max', 1::smallint)
           OR NOT public.promo_tier_used(v_s1, 'pro', 2::smallint) THEN
            RAISE EXCEPTION '192 self-test [R O1 after the refunds]: max t1 used %, pro t2 used %',
                public.promo_tier_used(v_s1, 'max', 1::smallint),
                public.promo_tier_used(v_s1, 'pro', 2::smallint);
        END IF;

        RAISE EXCEPTION USING ERRCODE = 'LU192', MESSAGE = '192 self-test sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU192' THEN
        RAISE NOTICE '192: behavioural self-tests A–H, R passed; every test write rolled back';
    END;

    -- ── 10. nothing left behind; and, with the flag down, nothing changed ──
    IF (SELECT count(*) FROM public.early_adopter_seats) <> v_seats0
       OR (SELECT count(*) FROM public.payment_transactions) <> v_pays0
       OR (SELECT count(*) FROM public.users) <> v_users0 THEN
        RAISE EXCEPTION '192 self-test left rows behind (seats/payments/users counts changed)';
    END IF;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-192-%') THEN
        RAISE EXCEPTION '192 self-test left fake auth.users rows behind';
    END IF;
    IF (SELECT md5(COALESCE(string_agg(b::text, '|' ORDER BY b.batch_id), ''))
          FROM public.promo_batches b) IS DISTINCT FROM v_batches0
       OR (SELECT md5(c::text) FROM public.early_adopter_campaign c WHERE c.id)
          IS DISTINCT FROM v_camp0
       OR (SELECT md5(c::text) FROM public.intro_offer_campaign c WHERE c.id)
          IS DISTINCT FROM v_intro0 THEN
        RAISE EXCEPTION '192 self-test did not restore the switches / batches';
    END IF;
    SELECT c.ladder_v2 INTO v_flag1 FROM public.early_adopter_campaign c WHERE c.id;
    IF v_flag1 IS DISTINCT FROM v_flag0 THEN
        RAISE EXCEPTION '192 self-test changed ladder_v2 (% → %)', v_flag0, v_flag1;
    END IF;

    IF NOT v_flag0 THEN
        -- promo_quote silent for a real lawyer (183's single-user inert check)
        SELECT count(*) INTO v_n
          FROM (SELECT u.user_id FROM public.users u
                 WHERE u.profession_first_group = 'legal' LIMIT 1) x,
               LATERAL public.promo_quote(x.user_id, 'pro') q;
        IF v_n <> 0 THEN
            RAISE EXCEPTION '192: ladder_v2 is OFF but promo_quote returned % row(s)', v_n;
        END IF;

        -- early_adopter_open = 138's expression
        IF public.early_adopter_open() IS DISTINCT FROM COALESCE(
               (SELECT c.enabled
                       AND (SELECT count(*) FROM public.early_adopter_seats s
                             WHERE s.released_at IS NULL) < c.seat_limit
                  FROM public.early_adopter_campaign c WHERE c.id), false) THEN
            RAISE EXCEPTION '192: early_adopter_open() differs from 138 with ladder_v2 OFF';
        END IF;

        -- promo_public_quote = today's catalog promo
        SELECT string_agg(format('%s|%s|%s', q.plan_id, q.price_sar, q.months), ',' ORDER BY q.plan_id)
          INTO v_s FROM public.promo_public_quote() q;
        SELECT string_agg(format('%s|%s|%s', p.plan_id, round(p.promo_price_sar, 2), c.promo_days / 30),
                          ',' ORDER BY p.plan_id)
          INTO v_s2
          FROM public.plans p
          CROSS JOIN public.early_adopter_campaign c
         WHERE c.id
           AND p.promo_price_sar IS NOT NULL
           AND public.early_adopter_open();
        IF v_s IS DISTINCT FROM v_s2 THEN
            RAISE EXCEPTION '192: promo_public_quote() = %, today''s promo = %', v_s, v_s2;
        END IF;

        -- every real live legacy seat priced exactly as before, both contexts
        SELECT count(*) FILTER (
                   WHERE public.effective_plan_price(s.user_id, t.plan_id, 'current')
                             IS DISTINCT FROM round(p.promo_price_sar, 2)
                      OR public.effective_plan_price(s.user_id, t.plan_id, 'purchase')
                             IS DISTINCT FROM round(p.promo_price_sar, 2)),
               count(*)
          INTO v_bad, v_n
          FROM public.early_adopter_seats s
          JOIN public.payment_transactions t ON t.payment_id = s.payment_id
          JOIN public.plans p ON p.plan_id = t.plan_id
         WHERE s.released_at IS NULL
           AND s.batch_id IS NULL
           AND s.user_id IS NOT NULL
           AND s.promo_ends_at > now()
           AND p.promo_price_sar IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM public.early_adopter_seats x
                            WHERE x.user_id = s.user_id AND x.release_reason = 'cancelled');
        IF v_bad <> 0 THEN
            RAISE EXCEPTION '192: % of % live legacy seats priced differently with ladder_v2 OFF', v_bad, v_n;
        END IF;

        RAISE NOTICE '192: OK — inert (ladder_v2 OFF): % live legacy seat(s) priced as before, no quotes, public promo unchanged', v_n;
    ELSE
        RAISE NOTICE '192: OK — ladder_v2 is ON (a re-run after the flip); inert checks skipped';
    END IF;
END $selftest$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY CHECKS — run manually
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. Inert. EXPECT ladder_v2 = false, ladder_v2_at NULL; six batches as §1.2.
-- SELECT enabled, ladder_v2, ladder_v2_at, seat_limit, promo_days FROM public.early_adopter_campaign;
-- SELECT batch_id, plan_id, price_tier, batch_order, price_sar, months, seat_limit,
--        profession_only, enabled
--   FROM public.promo_batches ORDER BY plan_id, price_tier, batch_order;
--
-- -- 2. Pin the new doc section (this file could not hash its own Arabic), and
-- --    the no-digit / marker checks. EXPECT has_any_digit = false, n_marker = 2.
-- WITH tail AS (
--     SELECT substr(content_md, position('## العرض حسب المهنة' IN content_md)) AS t, content_md
--       FROM public.product_docs WHERE doc_key = 'pricing'
-- )
-- SELECT md5(t) AS section_md5, length(content_md) AS doc_len,
--        t ~ '[0-9٠-٩]' AS has_any_digit,
--        position('## عرض الشهر الأول' IN content_md) = 0 AS old_section_gone,
--        (length(content_md) - length(replace(content_md, 'المشتركون الأوائل', '')))
--            / length('المشتركون الأوائل') AS n_marker
--   FROM tail;
--
-- -- 3. Preview the flip WITHOUT flipping (plan §6 step 5) — always ROLLBACK:
-- -- BEGIN;
-- --   UPDATE public.early_adopter_campaign SET ladder_v2 = true WHERE id;
-- --   SELECT * FROM public.promo_quote('<eligible lawyer>', 'pro');      -- pro_t1_a 19.90 / 2
-- --   SELECT * FROM public.promo_quote('<individual>', 'pro');           -- pro_t2_a 59.90 / 2
-- --   SELECT * FROM public.promo_quote('<lapsed pro lawyer>', 'pro');    -- tier 1 unless used
-- --   SELECT * FROM public.promo_quote('<active-basic lawyer>', 'pro');  -- pro_t2_a
-- --   SELECT public.effective_plan_price('<legacy pro holder>', 'pro', 'current'); -- 49.90
-- --   SELECT public.effective_plan_price('<legacy max holder>', 'max', 'current'); -- 99.90
-- --   SELECT * FROM public.promo_quote('<legacy pro holder on pro>', 'max');      -- max_t1_a
-- --   SELECT * FROM public.promo_public_quote();          -- max 149.90/4, pro 59.90/2
-- --   SELECT * FROM public.promo_checkout_price('<eligible lawyer>', 'pro');
-- --       -- true | 19.90 | pro_t1_a | 1 | 2 | legal  (the one call checkout makes)
-- --   SELECT * FROM public.promo_checkout_price('<legacy max holder, lapsed>', 'pro');
-- --       -- true | 49.90 | (nothing stamped — the legacy seat keeps its terms)
-- --   SELECT public.early_adopter_open();                 -- true
-- -- ROLLBACK;
--
-- -- 4. FLIP — only after the backend that stamps promo_batch_id is live:
-- -- UPDATE public.early_adopter_campaign SET ladder_v2 = true WHERE id;
-- -- (the trigger stamps ladder_v2_at; check it is set:
-- --  SELECT ladder_v2, ladder_v2_at FROM public.early_adopter_campaign;)
-- -- …then purge the ISR cache for '/' and '/pricing' (percent-encoded paths).
-- -- OFF SWITCH (rollback): the same row back to false. Held batch seats keep
-- -- their price to the end of their window; new buyers get 138/183 terms.
--
-- -- 5. Operator: live seats per batch (NEVER expose these numbers).
-- SELECT b.batch_id, b.enabled, b.seat_limit,
--        count(s.seat_id) FILTER (WHERE s.released_at IS NULL)        AS live,
--        count(s.seat_id) FILTER (WHERE s.over_capacity)              AS over,
--        count(s.seat_id) FILTER (WHERE s.release_reason = 'upgraded') AS upgraded
--   FROM public.promo_batches b
--   LEFT JOIN public.early_adopter_seats s ON s.batch_id = b.batch_id
--  GROUP BY b.batch_id, b.enabled, b.seat_limit
--  ORDER BY b.batch_id;
--
-- -- 6. Invariant audit, batch seats: claiming payment priced above its batch.
-- --    EXPECT ZERO ROWS.
-- SELECT s.seat_id, t.payment_id, t.amount_sar, t.upgrade_credit_sar, s.price_sar
--   FROM public.early_adopter_seats s
--   JOIN public.payment_transactions t ON t.payment_id = s.payment_id
--  WHERE s.batch_id IS NOT NULL
--    AND t.amount_sar + COALESCE(t.upgrade_credit_sar, 0) > s.price_sar + 0.01;
--
-- -- 7. Seats whose frozen terms differ from what their payment quoted (V1).
-- --    EXPECT ZERO ROWS (a row = a claim that fell back to the batch row).
-- SELECT s.seat_id, s.price_sar, t.intro_offer_terms
--   FROM public.early_adopter_seats s
--   JOIN public.payment_transactions t ON t.payment_id = s.payment_id
--  WHERE s.batch_id IS NOT NULL
--    AND t.intro_offer_terms ? 'offer_sar'
--    AND s.price_sar IS DISTINCT FROM round((t.intro_offer_terms->>'offer_sar')::numeric, 2);
--
-- -- 8. Client grants on payment_transactions (V7). EXPECT anon: no SELECT at
-- --    all; authenticated: no table SELECT, no SELECT on the three columns.
-- SELECT r.role,
--        has_table_privilege(r.role, 'public.payment_transactions', 'SELECT')                    AS table_select,
--        has_column_privilege(r.role, 'public.payment_transactions', 'promo_batch_id', 'SELECT')    AS batch_col,
--        has_column_privilege(r.role, 'public.payment_transactions', 'intro_offer_terms', 'SELECT') AS terms_col,
--        has_column_privilege(r.role, 'public.payment_transactions', 'credit_basis', 'SELECT')      AS basis_col,
--        has_column_privilege(r.role, 'public.payment_transactions', 'amount_sar', 'SELECT')        AS amount_col
--   FROM (VALUES ('anon'), ('authenticated'), ('service_role')) r(role);
--
-- -- 9. Superseded seats waiting on a refund (V5): 'upgraded' seats still inside
-- --    their window, and whether the superseding payment was refunded.
-- SELECT s.seat_id, s.user_id, s.promo_ends_at, s.released_by_payment_id, t.status
--   FROM public.early_adopter_seats s
--   JOIN public.payment_transactions t ON t.payment_id = s.released_by_payment_id
--  WHERE s.release_reason = 'upgraded' AND s.promo_ends_at > now();
-- --    A row with t.status = 'refunded' means a give-back was missed (e.g. a
-- --    refund released the seat before status was stamped): restore by hand.
--
-- ── ROLLBACK ─────────────────────────────────────────────────────────────────
-- -- Behaviour: flip ladder_v2 back to false. That is the whole rollback.
-- -- Structure (only with the backend rolled back first — it selects
-- -- payment_transactions.promo_batch_id, and only while NO batch seat or
-- -- stamped payment exists, or the history is lost):
-- --   re-run 183 §6–§9 bodies (intro_offer_quote, effective_plan_price,
-- --   claim_early_adopter_seat, early_adopter_status) and 138 §4.1 / §4.4
-- --   (early_adopter_open, release_early_adopter_seat);
-- --   DROP FUNCTION public.promo_checkout_price(uuid, text);
-- --   GRANT SELECT ON public.payment_transactions TO authenticated;  (undo V7 —
-- --     only if a client ever needs the full row back);
-- --   DROP FUNCTION public.promo_public_quote(), public.promo_quote(uuid, text),
-- --                 public.promo_active_batch(text, smallint),
-- --                 public.promo_tier_used(uuid, text, smallint);
-- --   ALTER TABLE public.payment_transactions DROP COLUMN promo_batch_id,
-- --     DROP COLUMN credit_basis;
-- --   ALTER TABLE public.early_adopter_seats DROP COLUMN batch_id, DROP COLUMN plan_id,
-- --     DROP COLUMN price_tier, DROP COLUMN price_sar,
-- --     DROP COLUMN released_by_payment_id;  (and re-add 138's
-- --     release_reason CHECK without 'upgraded' — fails if any row uses it);
-- --   DROP TRIGGER trg_early_adopter_campaign_ladder_v2_at ON public.early_adopter_campaign;
-- --   DROP FUNCTION public.stamp_ladder_v2_at();
-- --   ALTER TABLE public.early_adopter_campaign DROP COLUMN ladder_v2, DROP COLUMN ladder_v2_at;
-- --   DROP TABLE public.promo_batches;
-- --   restore the saved product_docs.pricing row.
-- -- ⚠ Re-running 183 (or 182 / 138) AFTER 192 silently reverts the v2 bodies —
-- --   182 refuses after 183, but 183 has no guard against running after 192.
