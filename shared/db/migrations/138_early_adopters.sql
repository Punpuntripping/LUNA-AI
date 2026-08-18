-- ════════════════════════════════════════════════════════════════════════════
-- 138 — المشتركون الأوائل (early adopters): the price becomes a FUNCTION
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/early_adopters.md — §3 (data model), §3.4 (the function
--       contract the backend is being written against RIGHT NOW), §3.5 (the
--       100th-seat race and deliberate over-capacity), §9 (deploy order).
-- Depends on: 079 (user_subscriptions + trg_user_subscriptions_assignment),
--             092 (plans.price_sar/billing_cycle, payment_transactions,
--                  grant_plan), 113 (upgrade_credit_sar, prior_* snapshot,
--                  revoke_plan_grant + its `action` discriminator, which this
--                  file's claim function copies), 117 (payments outlive the
--                  account: user_id nullable + ON DELETE SET NULL),
--             118 (RLS lockdown posture: RLS on, zero policies, EXECUTE
--                  revoked from anon/authenticated), 119 (partial unique index
--                  as a money-path guard), 120 (renewal_cancelled_at — the
--                  cancel this file releases a seat on), 131/137 (usage_reset),
--             132 (plans.billing_cycle = 'recurring_30d' on pro/max, which is
--                  what this file uses to mean "a seat-bearing plan").
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE TABLE/INDEX IF NOT EXISTS,
--             DROP CONSTRAINT IF EXISTS + ADD, INSERT … ON CONFLICT DO NOTHING,
--             value-stable UPDATEs, CREATE OR REPLACE FUNCTION. Re-runnable.
--
-- ⚠ APPLY THIS BEFORE PUSHING THE BACKEND (plan §9). Railway is GitHub-linked to
--   master, so the push IS the deploy, and the new backend calls functions and
--   names columns that must already exist. The gap is safe in the forward
--   direction only: with 138 applied and the OLD backend still live, everything
--   still reads plans.price_sar and the campaign is simply not running. Nothing
--   is over- or under-charged. The reverse order 42883s on every checkout.
--
-- ⚠ THIS FILE SHIPS INERT. early_adopter_campaign.enabled = false, and
--   effective_plan_price returns plans.price_sar unchanged for every user until
--   somebody runs `UPDATE public.early_adopter_campaign SET enabled = true;`.
--   Applying it changes behaviour for ZERO users. That is the whole point — the
--   campaign starts (and stops) with a one-row UPDATE, not a deploy.
--
-- ⚠ NO LIVE INTROSPECTION IN THIS SESSION ────────────────────────────────────
--   Same situation 113's and 132's headers record: the files in this directory
--   are NOT the prod schema (memory: project_migration_drift). What IS known,
--   read from prod on 2026-08-16 by the owner and restated here so the next
--   reader does not have to re-derive it:
--
--     * plans: plan_id(text PK), name_ar, name_en, points_monthly,
--       points_weekly, points_session, ocr_pages_monthly, web_calls_monthly,
--       duration_days, created_at, updated_at, price_sar numeric,
--       billing_cycle text, library_unlocks_period. THERE IS NO is_active.
--       Catalog: basic 49.90/one_time/7d · pro 89.90/recurring_30d/30d ·
--       max 189.90/recurring_30d/30d · free/dev/marketing_lawyer price NULL.
--     * user_subscriptions has NO `status` column (dropped in 091; status is
--       DERIVED in the user_subscriptions_live view). Nothing here reads it.
--     * payment_transactions PK is `payment_id`, and user_id is
--       ON DELETE SET NULL (117).
--     * users PK is `user_id`.
--
-- ⚠ WHAT THIS FILE DELIBERATELY DOES NOT TOUCH ───────────────────────────────
--   grant_plan · revoke_plan_grant · get_user_quota_state ·
--   get_user_usage_windows · stamp_usage_reset · user_subscriptions_live.
--   113/119/120 all established that the live money-path RPCs are not edited
--   for a side concern, and rebuilding that view without first reading
--   pg_get_viewdef silently drops operator columns — a trap that has now bitten
--   three times (see the headers of 120, 129a and 137). The seat claim sits
--   BESIDE grant_plan, never inside it, which also keeps it clear of
--   trg_user_subscriptions_assignment (BEFORE UPDATE OF plan_id): a separate
--   table cannot accidentally re-stamp anybody's term.
--
-- WHAT ───────────────────────────────────────────────────────────────────────
--   1. plans.promo_price_sar — the catalog stays the price authority.
--   2. early_adopter_seats — who holds a seat, from which payment, until when.
--      Deny-all: this table IS the remaining seat count.
--   3. early_adopter_campaign — the one-row kill switch / capacity constant.
--   4. Six functions, service-role only:
--        early_adopter_open()                              → boolean
--        effective_plan_price(uuid, text, text)             → numeric
--        claim_early_adopter_seat(uuid, uuid)               → TABLE(...)
--        release_early_adopter_seat(uuid, text)             → boolean
--        restore_early_adopter_seat(uuid)                   → boolean
--        early_adopter_status(uuid)                         → TABLE(...)
--
-- ⚠ TWO PLACES THIS FILE DEPARTS FROM .claude/plans/early_adopters.md ─────────
--
--   (a) §3.2's DDL says `user_id uuid NOT NULL … ON DELETE SET NULL`. Those two
--       cannot both hold: deleting a user would try to write NULL into a NOT
--       NULL column and raise 23502, ABORTING ACCOUNT DELETION — the exact
--       failure mode 132's header documents for NO ACTION on
--       payment_transactions.payment_method_id, arriving through a new door.
--       §3.2's own prose ("a purged account leaves the seat record standing")
--       requires the column to be NULLABLE, which is also literally 117's
--       posture for payment_transactions.user_id. So: NULLABLE + SET NULL.
--       The NOT NULL guarantee moves into claim_early_adopter_seat, which
--       refuses a NULL p_user_id rather than inserting a ghost seat.
--
--   (b) §3.4's resolution order CONTRADICTS §1 in two places. Both were put to
--       the owner and resolved on 2026-08-16 in favour of §1; §3.4's literal
--       text is superseded and the plan file should be amended to match:
--
--       ⚠ FORFEITURE IS ENFORCED (§1 rule 6 beats §3.4 item 3). A user with any
--         seat row released with reason 'cancelled' NEVER gets the promo price
--         on pro/max again — not with seats open, not on a brand-new purchase.
--         §3.4 item 3 as written would have re-quoted them 49.90. The predicate
--         is identical in effective_plan_price and claim_early_adopter_seat
--         (which answers action = 'forfeited') so the price quoted and the seat
--         granted can never disagree — quoting the promo and then withholding
--         the seat is the one outcome §3.5 forbids. It needs no new column: an
--         UNDONE cancellation clears released_at and release_reason together, so
--         a restored user has no forfeiting row. A 'refund' release is NOT a
--         forfeiture (§1 rule 5 lets them rejoin). basic is untouched — no
--         enrolment, no forfeiture (§1 rule 9).
--
--       ⚠ enabled = false DOES NOT REPRICE A LIVE SEAT (§1 rule 4 beats §3.4
--         item 1). The flag suppresses NEW seats and returns basic to list
--         price, and nothing else: a seat holder keeps the promo until
--         promo_ends_at even with the campaign switched off. §3.4 item 1 as
--         written would have stepped every existing member's next AUTO-RENEWAL
--         up to full price on a saved card the moment an operator flipped a
--         switch to stop signups — silently, with no human in the loop. That is
--         precisely the charge this migration exists to prevent (plan §2).
--         A true emergency stop is a deliberate, auditable UPDATE against the
--         seat rows, NOT a second flag — see the note above effective_plan_price.
--
--       Both are inert pre-launch: with zero seats and enabled = false, the
--       superseded and the corrected readings are identical for every user.
--
--   Everything else follows the plan as written.

BEGIN;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. plans.promo_price_sar — the catalog stays the price authority (§3.1)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Same posture as price_sar: tunable by a plain UPDATE, no code change, no
-- third copy of a number. NULL = this plan has no promo, and NULL is the
-- default, so free/dev/marketing_* are untouched and unaffected.
--
-- WHY NOT `UPDATE plans SET price_sar = …` (plan §2, the whole reason this
-- migration exists) — three readers share that column: checkout
-- (payment_service.create_checkout), THE RENEWAL JOB re-reading it at charge
-- time every 30 days (renewal_service.py:395), and the upgrade credit for the
-- OLD plan (_upgrade_credit). Editing it would price payer #101 at the promo
-- too, and — far worse — the day the campaign closed and the column went back
-- to 89.90, EVERY early adopter's next renewal would step up to 89.90 on a
-- saved card with no warning. The 90-day promise would die as an automatic
-- charge. Hence a SECOND column and one function over both.

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS promo_price_sar NUMERIC(10,2);

-- Value-stable: the IS DISTINCT FROM makes a re-run a genuine no-op instead of
-- churning updated_at, which the backend's 5-minute plan cache reads.
--
-- ⚠ THE FILE IS THE SEED, NOT THE AUTHORITY. If an operator tunes a promo price
--   in prod, re-running this migration puts these three values back. Same
--   posture (and same caveat) as 132 §5's billing_cycle flip.
UPDATE public.plans
   SET promo_price_sar = 39.90, updated_at = now()
 WHERE plan_id = 'basic' AND promo_price_sar IS DISTINCT FROM 39.90;

UPDATE public.plans
   SET promo_price_sar = 49.90, updated_at = now()
 WHERE plan_id = 'pro'   AND promo_price_sar IS DISTINCT FROM 49.90;

UPDATE public.plans
   SET promo_price_sar = 99.90, updated_at = now()
 WHERE plan_id = 'max'   AND promo_price_sar IS DISTINCT FROM 99.90;

COMMENT ON COLUMN public.plans.promo_price_sar IS
    'The المشتركون الأوائل price for this plan (138), VAT-inclusive like '
    'price_sar. NULL = this plan has no promo (free, dev, marketing_*). NEVER '
    'read directly by application code — read it through '
    'effective_plan_price(user, plan, context), which is the ONE definition '
    'consumed by checkout, the renewal job and the upgrade credit — and pass the '
    'right context (''purchase'' vs ''current''), because that is what keeps a '
    'renewal from discounting a non-member. Reading this column straight would '
    'price a non-member at the promo, or (worse) reading price_sar straight '
    'would step an early adopter up to list price on a saved card mid-promise. '
    'Tunable by UPDATE; re-running migration 138 restores the launch values.';


-- ════════════════════════════════════════════════════════════════════════════
-- 2. early_adopter_seats — the enrolment record, and the capacity itself (§3.2)
-- ════════════════════════════════════════════════════════════════════════════
--
-- NO seat_no COLUMN, deliberately (§3.2): seats release and are re-issued, so a
-- number would be either wrong or a second thing to reconcile. Capacity is
-- count(*) WHERE released_at IS NULL — and note what that implies and is meant
-- to imply: a seat whose 90 days have EXPIRED still occupies capacity. The
-- campaign is "the first 100 people who paid", not "100 concurrent discounts",
-- so it closes permanently once 100 seats have been issued and not given back.
--
-- ⚠ user_id IS NULLABLE — see deviation (a) in the header. NOT NULL beside
--   ON DELETE SET NULL breaks account deletion with 23502. The invariant is
--   enforced by claim_early_adopter_seat instead, which will not insert a seat
--   without a user. A purged account's seat stays STANDING and keeps consuming
--   capacity (117's retention posture applied to a non-financial row that is
--   nonetheless evidence of a commercial promise). If the owner would rather a
--   purge free the seat, release it in account_purge_service BEFORE the cascade
--   — do not weaken this FK.
--
-- ⚠ payment_id ON DELETE is left as NO ACTION on purpose. Payment rows are
--   never deleted (117 — receipt_no must stay hole-free), so NO ACTION cannot
--   block anything, and it is the honest statement that a seat without its
--   claiming payment is not a state we accept.

CREATE TABLE IF NOT EXISTS public.early_adopter_seats (
    seat_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid REFERENCES public.users(user_id) ON DELETE SET NULL,
    payment_id     uuid UNIQUE REFERENCES public.payment_transactions(payment_id),
    claimed_at     timestamptz NOT NULL DEFAULT now(),
    promo_ends_at  timestamptz NOT NULL,
    released_at    timestamptz,
    release_reason text,
    over_capacity  boolean NOT NULL DEFAULT false
);

-- Constraints added separately from CREATE TABLE: if the table already exists
-- (a re-run after a partial apply, a hand-applied hotfix) the CREATE is skipped
-- WHOLE, inline constraints included — 113's note, restated in 132 §1. DROP then
-- ADD so a re-run converges on exactly this domain.

ALTER TABLE public.early_adopter_seats
    DROP CONSTRAINT IF EXISTS early_adopter_seats_release_reason_check,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_release_pair_check,
    DROP CONSTRAINT IF EXISTS early_adopter_seats_window_check;

ALTER TABLE public.early_adopter_seats
    ADD CONSTRAINT early_adopter_seats_release_reason_check
        CHECK (release_reason IS NULL OR release_reason IN ('refund', 'cancelled')),
    -- A half-released seat is not representable. release() writes both columns,
    -- restore() clears both; anything else is a hand-edit that should fail
    -- loudly rather than leave a row that reads "released, for no reason" or
    -- "reason recorded, still live" — the second of which would silently keep
    -- consuming capacity while the operator believed it had been given back.
    ADD CONSTRAINT early_adopter_seats_release_pair_check
        CHECK ((released_at IS NULL) = (release_reason IS NULL)),
    -- The window ends after it begins. Cheap tripwire against a promo_days that
    -- someone set to 0 or negative in the campaign row.
    ADD CONSTRAINT early_adopter_seats_window_check
        CHECK (promo_ends_at > claimed_at);

-- ⚠ THE SAME TRAP, APPLIED TO THE ONE CONSTRAINT THAT MATTERS MOST. `payment_id
--   uuid UNIQUE` is inline in the CREATE TABLE above, so if the table already
--   exists it was skipped too — and without it the webhook/verify double-run
--   would issue TWO seats for one payment. It cannot be re-added blind (a fresh
--   apply already has it, and ADD CONSTRAINT is not IF NOT EXISTS), so it is
--   discovered by the name PostgreSQL generates for an inline column UNIQUE:
--   <table>_<column>_key.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint con
          JOIN pg_class     c ON c.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname = 'early_adopter_seats'
           AND con.conname = 'early_adopter_seats_payment_id_key'
    ) THEN
        ALTER TABLE public.early_adopter_seats
            ADD CONSTRAINT early_adopter_seats_payment_id_key UNIQUE (payment_id);
        RAISE NOTICE
            '138: added the missing UNIQUE(payment_id) — the claim idempotency key';
    END IF;
END $$;

COMMENT ON TABLE public.early_adopter_seats IS
    'المشتركون الأوائل enrolment (138): one row per seat ever issued. THIS '
    'TABLE IS THE REMAINING SEAT COUNT — capacity is count(*) WHERE released_at '
    'IS NULL — and §1 rule 10 of the plan says that number is never disclosed: '
    'not on the page, not in the API, not in an error message. So RLS is enabled '
    'with ZERO policies = deny-all for anon and authenticated; service_role '
    'bypasses RLS, so the backend is unaffected (the 118 lockdown posture, same '
    'as payment_methods/132, subscription_cancellations/120 and '
    'library_unlocks/104). DO NOT ADD A POLICY: on this table a policy could '
    'only leak the scarcity signal the campaign is built on. A user''s own state '
    'is read through early_adopter_status(), which returns no count.';

COMMENT ON COLUMN public.early_adopter_seats.user_id IS
    'Who holds the seat. NULLABLE + ON DELETE SET NULL (117''s retention '
    'posture): a purged account leaves the seat record standing, and that seat '
    'keeps consuming capacity. Deliberately NOT NOT-NULL — NOT NULL beside '
    'ON DELETE SET NULL would raise 23502 and abort account deletion. The '
    '"never a seat without a user" guarantee lives in claim_early_adopter_seat.';
COMMENT ON COLUMN public.early_adopter_seats.payment_id IS
    'THE IDEMPOTENCY KEY (138). UNIQUE, and that is its job: the paid path runs '
    'twice by design (Moyasar webhook + the client''s /verify), exactly as it '
    'does for grant_plan/fulfilled_at and stamp_usage_reset/paid_at. The second '
    'run cannot double-claim, cannot issue a second seat, and cannot move '
    'anybody''s 90-day window — claim_early_adopter_seat finds this row and '
    'returns action = ''already_claimed'' unchanged.';
COMMENT ON COLUMN public.early_adopter_seats.claimed_at IS
    'The claiming payment''s paid_at, NOT now() (137''s sharpest lesson: a '
    'replay must be idempotent BY VALUE, not merely blocked). Only a manual '
    'INSERT ever takes the now() default.';
COMMENT ON COLUMN public.early_adopter_seats.promo_ends_at IS
    'claimed_at + early_adopter_campaign.promo_days. WALL-CLOCK (§1 rule 3): a '
    'gap in the subscription burns promo days rather than pausing them, and an '
    'involuntary lapse in dunning does NOT stop this clock (§1 rule 7). Any '
    'charge whose period begins before this instant is priced at the promo; the '
    'first one after it is full price — which is what effective_plan_price '
    'reads, and what the renewal job therefore obeys without knowing any of it.';
COMMENT ON COLUMN public.early_adopter_seats.released_at IS
    'NULL = live, and live is what counts against capacity. Set by '
    'release_early_adopter_seat on a refund (§1 rule 5 — the seat returns to the '
    'pool and they may buy back in) or on a cancellation that STANDS (§1 rule 6 '
    '— the promo is forfeited; «تراجع عن الإلغاء» calls '
    'restore_early_adopter_seat, which un-releases it unconditionally).';
COMMENT ON COLUMN public.early_adopter_seats.release_reason IS
    '⚠ THIS COLUMN IS THE FORFEITURE RECORD (§1 rule 6), not just an audit note. '
    '''cancelled'' on ANY of a user''s seat rows means they never get the promo '
    'price on pro/max again — effective_plan_price and claim_early_adopter_seat '
    'both test exactly that, and they must keep testing the same thing. '
    '''refund'' does NOT forfeit (§1 rule 5: they may buy back in while seats '
    'remain). Undoing a cancellation clears this column and released_at TOGETHER '
    '(early_adopter_seats_release_pair_check makes any other pairing '
    'unrepresentable), which is why the undo needs no extra state: no row, no '
    'forfeiture. DO NOT hand-edit this column to ''refund'' to "un-forfeit" '
    'someone — call restore_early_adopter_seat, which gives the seat itself back.';
COMMENT ON COLUMN public.early_adopter_seats.over_capacity IS
    'TRUE = this seat was issued past seat_limit, deliberately (§3.5). It '
    'happens when a quote priced at the promo settles after the campaign closed, '
    'and when a cancellation is undone after the seat has been re-issued. The '
    'alternative is refusing a payment that already succeeded or charging '
    'someone the full price after quoting them the promo — this fails toward the '
    'customer instead. Bounded by the number of open quotes at closing time '
    '(realistically 0–3). Operator query: SELECT * FROM early_adopter_seats '
    'WHERE over_capacity.';

-- ── 2a. ONE LIVE SEAT PER USER. The claim is the index, not a count ──────────
-- 119's pattern: the invariant is a partial unique index, not a Python check
-- with a race in it. It is also what makes §1 rule 8 (upgrade carries over —
-- pro → max mid-window pays max's promo for the REMAINING days, no reset, no
-- new window) structurally true: the second payment physically cannot open a
-- second window, so claim_early_adopter_seat returns the existing seat instead.
--
-- NULL user_ids (purged accounts) never conflict with each other in a unique
-- index. That is correct here and not a hole: those seats have no user left to
-- claim a second one.
--
-- This index is ALSO the one early_adopter_open() counts: it holds exactly the
-- live rows, so the capacity check is an index-only scan of ≤ seat_limit
-- entries. No separate count index is needed and none should be added.

CREATE UNIQUE INDEX IF NOT EXISTS early_adopter_seats_one_live
    ON public.early_adopter_seats (user_id)
    WHERE released_at IS NULL;

COMMENT ON INDEX public.early_adopter_seats_one_live IS
    'At most one LIVE seat per user (138) — the structural form of "an upgrade '
    'carries the window over rather than opening a new one". Also the index '
    'early_adopter_open() counts to decide whether the campaign still has room.';

-- FK index (project rule): supports the ON DELETE SET NULL scan from users, and
-- restore_early_adopter_seat's "this user's most recent release" read, which the
-- partial index above cannot serve because released rows are not in it.
CREATE INDEX IF NOT EXISTS idx_early_adopter_seats_user
    ON public.early_adopter_seats (user_id, claimed_at DESC);

-- ── 2b. Lockdown (118 posture) ───────────────────────────────────────────────
-- 118 §6 already cleared the blanket default grant for anon/authenticated on
-- tables created in this schema, so a table created today should be born closed.
-- The REVOKEs are explicit anyway, because "born closed" is a default that has
-- been edited once already and H-2 (cases_content_backup) is what happens when
-- you rely on it.

ALTER TABLE public.early_adopter_seats ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.early_adopter_seats FROM anon;
REVOKE ALL ON TABLE public.early_adopter_seats FROM authenticated;
GRANT  ALL ON TABLE public.early_adopter_seats TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- 3. early_adopter_campaign — the capacity constant and the kill switch (§3.3)
-- ════════════════════════════════════════════════════════════════════════════
--
-- A one-row singleton (id boolean PK, CHECK (id) — a second row is
-- unrepresentable) so the campaign is turned on by an UPDATE, not a deploy. Same
-- discipline as SUBSCRIPTION_AUTO_RENEWAL_ENABLED, and it doubles as a kill
-- switch that needs no rollback.
--
-- ⚠ WHAT `enabled = false` DOES, EXACTLY (owner, 2026-08-16/17 — this overrides
--   §3.4 item 1): NOTHING NEW IS QUOTED at the promo price, and basic goes back
--   to its list price. Two things it deliberately does NOT do:
--     * it does not reprice anyone already holding a live seat — they keep the
--       promo until promo_ends_at, because §1 rule 4 says the promise travels
--       with the subscription and the alternative is a silent step-up on a saved
--       card;
--     * it does not refuse a claim for a payment that was ALREADY CHARGED the
--       promo price — a quote outstanding when the switch was flipped still
--       seats (claim_early_adopter_seat), because taking 49.90 and withholding
--       the 90 days it bought is the same broken promise in a different costume.
--   The flag is therefore a valve on NEW QUOTES, not a gate on settled money;
--   the supply of claimable payments dries up by itself within one checkout
--   window.
--   THE NORMAL END OF THE CAMPAIGN NEEDS NO FLIP AT ALL: seat 100 fills,
--   early_adopter_open() goes false by itself, and every member keeps their
--   price for the rest of their 90 days. Flip `enabled` off to stop enrolment
--   early; to stop the PRICING too — a genuine emergency — release the seats
--   explicitly (see the note above effective_plan_price). One switch, one
--   meaning, and the destructive act stays visible in the seat rows.

CREATE TABLE IF NOT EXISTS public.early_adopter_campaign (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    seat_limit integer NOT NULL DEFAULT 100,
    promo_days integer NOT NULL DEFAULT 90,
    enabled    boolean NOT NULL DEFAULT false
);

ALTER TABLE public.early_adopter_campaign
    DROP CONSTRAINT IF EXISTS early_adopter_campaign_seat_limit_check,
    DROP CONSTRAINT IF EXISTS early_adopter_campaign_promo_days_check;

ALTER TABLE public.early_adopter_campaign
    ADD CONSTRAINT early_adopter_campaign_seat_limit_check
        CHECK (seat_limit >= 0),
    -- > 0, not >= 0: a promo_days of 0 would mint seats whose window has already
    -- closed, and early_adopter_seats_window_check would then reject every
    -- claim — i.e. the campaign would silently stop enrolling anyone.
    ADD CONSTRAINT early_adopter_campaign_promo_days_check
        CHECK (promo_days > 0);

-- ON CONFLICT DO NOTHING, not DEFAULT VALUES: §3.3's literal statement is not
-- re-runnable (23505 on the PK), and — more importantly — a re-run must never
-- flip a LIVE campaign back to enabled = false. DO NOTHING leaves whatever the
-- operator set.
INSERT INTO public.early_adopter_campaign (id)
VALUES (true)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE public.early_adopter_campaign IS
    'One-row singleton holding the المشتركون الأوائل campaign switch and its '
    'capacity (138). Turned on with: UPDATE public.early_adopter_campaign SET '
    'enabled = true; — a deploy is not required and a rollback is not required '
    'to stop it. Ships enabled = false so the migration + deploy change '
    'behaviour for zero users (plan §9). RLS enabled with ZERO policies: '
    'seat_limit is the number §1 rule 10 says is never disclosed, so not even a '
    'read policy belongs here. Read it through early_adopter_open(), which '
    'answers a boolean and never a count.';

COMMENT ON COLUMN public.early_adopter_campaign.seat_limit IS
    'How many live seats the campaign issues (100 at launch). NEVER surfaced to '
    'a client, in any form, including "N remaining" or "the campaign is full". '
    'After close a user simply sees the list price.';
COMMENT ON COLUMN public.early_adopter_campaign.promo_days IS
    'Length of the promotional window in wall-clock days (90 at launch), '
    'anchored at the claiming payment''s paid_at. Changing it affects only seats '
    'claimed AFTERWARDS — promo_ends_at is materialised per seat on purpose, so '
    'no existing member''s promise can be shortened by an UPDATE here.';
COMMENT ON COLUMN public.early_adopter_campaign.enabled IS
    'The QUOTING switch. false = nothing new is quoted at the promo price and '
    'basic goes back to list. It does NOT reprice an existing member: a live '
    'seat keeps the promo until '
    'promo_ends_at whatever this says (§1 rule 4 — the flag must never become a '
    'silent step-up on a saved card, which is the charge this whole design '
    'prevents). Ships false so the deploy changes behaviour for zero users. The '
    'campaign also ends by ITSELF when seat 100 fills — flipping this is only '
    'for stopping enrolment early. To stop the PRICING as well, release the '
    'seats explicitly; there is deliberately no second flag for that.';

ALTER TABLE public.early_adopter_campaign ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.early_adopter_campaign FROM anon;
REVOKE ALL ON TABLE public.early_adopter_campaign FROM authenticated;
GRANT  ALL ON TABLE public.early_adopter_campaign TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- 4. The functions (§3.4)
-- ════════════════════════════════════════════════════════════════════════════
--
-- ⚠ THE SIGNATURES ARE A WIRE CONTRACT. The backend is being written against
--   them right now; changing an argument name, an argument order or a returned
--   column name breaks a caller that will not fail until runtime, on the money
--   path, in production. If one has to change, change the caller in the same
--   commit and say so in the migration that does it.
--
--   early_adopter_open()                        RETURNS boolean
--   effective_plan_price(p_user_id uuid, p_plan_id text,
--                        p_context text DEFAULT 'purchase')
--                                               RETURNS numeric
--       ⚠ p_context IN ('purchase','current'); anything else RAISES 22023.
--         The DEFAULT keeps two-argument call sites working, and the two-arg
--         overload is DROPPED (not replaced) so it cannot shadow this one.
--   claim_early_adopter_seat(uuid, uuid)        RETURNS TABLE(seat_id uuid,
--                                                             promo_ends_at timestamptz,
--                                                             over_capacity boolean,
--                                                             action text)
--   release_early_adopter_seat(uuid, text)      RETURNS boolean
--   restore_early_adopter_seat(uuid)            RETURNS boolean
--   early_adopter_status(uuid)                  RETURNS TABLE(campaign_open boolean,
--                                                             has_seat boolean,
--                                                             promo_ends_at timestamptz)
--
-- All six: SECURITY DEFINER, SET search_path TO 'public', EXECUTE REVOKED from
-- PUBLIC/anon/authenticated and GRANTed to service_role only (118). None of them
-- carries an internal authorization guard — they take a user_id and trust it —
-- so none of them may EVER become reachable through PostgREST. Same posture as
-- grant_plan and revoke_plan_grant. early_adopter_open() reaches the public
-- through a backend endpoint (GET /payments/early-adopter), not through the API
-- gateway.
--
-- WHAT "A SEAT-BEARING PLAN" MEANS HERE, AND WHY IT IS NOT plan_id IN ('pro','max')
-- The test used throughout §4 is:
--     plans.billing_cycle = 'recurring_30d' AND plans.promo_price_sar IS NOT NULL
-- i.e. "a plan that renews AND has a promo" — which is exactly the set of plans
-- whose promise has to survive a future charge, and therefore exactly the set
-- that needs a seat. basic (one_time, promo 39.90) is discounted for everyone
-- while seats remain and enrols nobody (§1 rule 9), which falls out of the same
-- expression rather than needing a second rule. 132's billing_cycle comment asks
-- for precisely this: read the column, do not hard-code the ids, or you grow a
-- third copy of the plan ladder that drifts.


-- ── 4.1 early_adopter_open — "is the campaign taking new seats?" ─────────────
-- A boolean and never a number (§1 rule 10). Cheap: the count is an index-only
-- scan over early_adopter_seats_one_live, which holds at most seat_limit rows.

CREATE OR REPLACE FUNCTION public.early_adopter_open()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    SELECT COALESCE(
        (SELECT c.enabled
                AND (SELECT count(*)
                       FROM public.early_adopter_seats s
                      WHERE s.released_at IS NULL) < c.seat_limit
           FROM public.early_adopter_campaign c
          WHERE c.id),
        false);
$$;

COMMENT ON FUNCTION public.early_adopter_open() IS
    'TRUE while the المشتركون الأوائل campaign is enabled AND live seats remain '
    '(138). The ONLY sanctioned way to ask about capacity — it answers a boolean '
    'so that no caller, no log line and no error message can leak the remaining '
    'count (§1 rule 10). Missing campaign row → false (fail closed). Surfaced '
    'publicly through GET /payments/early-adopter; never through PostgREST.';

REVOKE EXECUTE ON FUNCTION public.early_adopter_open() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.early_adopter_open() TO service_role;


-- ── 4.2 effective_plan_price — THE one price definition ─────────────────────
--
-- Consumed by all three readers of the old plans.price_sar (plan §2 and §4),
-- and THE CONTEXT ARGUMENT IS WHICH QUESTION EACH ONE IS ASKING:
--   * payment_service.create_checkout   → 'purchase' — "what does this plan cost
--     this user if they buy it right now";
--   * renewal_service                   → 'current'  — re-read at charge time
--     every 30 days. THIS IS THE CALL THAT KEEPS THE 90-DAY PROMISE, and the
--     reason the promise cannot be implemented as an edit to plans.price_sar;
--   * _upgrade_credit, pricing the OLD plan → 'current' — "what is this user
--     paying for the plan they already hold".
--
-- ⚠ WHY THE PARAMETER EXISTS. DO NOT "SIMPLIFY" IT AWAY. Two distinct money
--   bugs, one root cause — 'purchase' answers "what would this cost you", and
--   both of the callers below are asking "what does this ALREADY cost you":
--
--   1. RENEWALS. Under the 'purchase' rule a pro subscriber who is NOT a member
--      would be charged 49.90 at renewal purely because the campaign is open.
--      That is an uncapped discount: it burns no seat, appears in no count, and
--      silently enrols the pre-campaign payers §1.2 says are explicitly NOT
--      enrolled. (The tempting alternative — letting renewals claim seats —
--      enrols them for real, which is the same bug with bookkeeping.) With
--      'current' a renewal quotes the promo IFF the user holds a live seat.
--
--   2. THE UPGRADE CREDIT (plan §4's "money bug", same class as H-4 in
--      security_review_2026-08-07.md). It prices the OLD plan, and it must
--      price it at what that user actually pays:
--        · a member upgrading pro → max is credited 49.90, not 89.90 — crediting
--          89.90 would hand back more than they ever paid;
--        · a FULL-PRICE pro holder upgrading during the campaign is credited
--          89.90, not 49.90 — under the 'purchase' rule they would lose ~34 SAR
--          of credit on a plan they paid full price for.
--      'current' gets both right by construction, and self-corrects after day 90
--      when the member is genuinely paying 89.90 again.
--
-- ⚠ THE BASIC RESIDUAL, named rather than hidden. `basic` is one_time and never
--   renews, so the only caller that ever asks 'current' about it is the upgrade
--   credit — and 'current' has no seat to read for basic (there is no enrolment,
--   §1 rule 9), so it answers the LIST price. A user who bought basic at 39.90
--   and upgrades is therefore credited from 49.90: an over-credit of at most
--   10 SAR, prorated over 7 days, in the customer's favour. The exact answer is
--   in the ledger (payment_transactions.amount_sar + upgrade_credit_sar), not in
--   any price function, and reading it there is a bigger change than this bug is
--   worth. Bounded, one-directional, and deliberately accepted — do not "fix" it
--   by letting 'current' consult early_adopter_open(), which would reintroduce
--   failure mode 1 above for every pro/max renewal.
--
-- Resolution order — §3.4's items 1–4 as AMENDED by the owner on 2026-08-16
-- (§1 wins over §3.4 wherever the plan contradicted itself; both amendments are
-- spelled out in the header):
--   item 1. no promo_price_sar for the plan (or the plan is not purchasable at
--           all) → price_sar. ⚠ enabled = false NO LONGER short-circuits here —
--           see the FLAG note below;
--   item 3. a seat-bearing plan (recurring_30d = pro, max):
--             a. FORFEITED (any seat row released with reason 'cancelled') →
--                price_sar, permanently, even with seats open and even on a
--                brand-new purchase (§1 rule 6). Both contexts;
--             b. else the user holds a LIVE seat still inside its window →
--                promo. THIS BRANCH IGNORES `enabled` (§1 rule 4), and it is the
--                WHOLE of 'current': membership, and nothing else, is what makes
--                a running term cost the promo price;
--             c. else the campaign is still open → promo. ⚠ 'purchase' ONLY —
--                this is the checkout that is ABOUT to claim the seat (quote
--                first, claim after the money lands). 'current' SKIPS THIS
--                BRANCH ENTIRELY, so a non-member reads the list price here even
--                with seats wide open;
--             d. else price_sar;
--   item 2. any other plan with a promo (basic) → promo IFF the campaign is
--           open, and IFF the context is 'purchase'. No enrolment, no
--           forfeiture, no per-user cap, and it reverts for everybody —
--           including people who bought during the campaign — the moment seat
--           100 fills (§1 rule 9). See THE BASIC RESIDUAL above for what
--           'current' answers here and why;
--   item 4. otherwise price_sar.
--
-- p_user_id MAY BE NULL (an anonymous /pricing quote): NULL matches no seat and
-- no forfeiture, so an anonymous caller sees the open-campaign answer, which is
-- the honest one.
--
-- ⚠ FORFEITURE — WHY IT NEEDS NO COLUMN AND NO EXTRA STATE. The test is simply
--   "does this user have ANY seat row carrying release_reason = 'cancelled'".
--   restore_early_adopter_seat clears released_at and release_reason TOGETHER
--   (early_adopter_seats_release_pair_check makes any other combination
--   unrepresentable), so an UNDONE cancellation leaves no such row and forfeits
--   nothing. A refund release carries reason 'refund' and is likewise not a
--   forfeiture — §1 rule 5 explicitly lets a refunded user rejoin while seats
--   remain. The identical predicate is in claim_early_adopter_seat, and the two
--   must be edited together: quoting the promo and then withholding the seat is
--   the one outcome §3.5 forbids.
--
--   OPERATOR REMEDY, for the §10 grandfathering case ("the fix is a manual seat
--   insert, not a rule change"): a manually inserted seat does NOT beat a
--   forfeiture — the gate is checked first, deliberately, because "no promo
--   price on pro/max ever again" is the decision. To genuinely give the price
--   back, un-release the user's own cancelled row instead, i.e. call
--   restore_early_adopter_seat(user_id). That is the same action, it is
--   auditable, and it leaves the table consistent.
--
-- ⚠ THE FLAG DOES NOT REPRICE A LIVE SEAT (owner, 2026-08-16, overriding §3.4
--   item 1). enabled = false means: early_adopter_open() is false, so NOTHING
--   NEW IS QUOTED at the promo price and basic goes back to list — and NOTHING
--   ELSE. In particular it does NOT refuse a claim for a payment that was
--   already CHARGED the promo price (owner, 2026-08-17): a quote outstanding
--   when the switch was flipped still seats, because taking 49.90 and
--   withholding the 90 days it bought is the same broken promise in a different
--   costume. The flag is a valve on new quotes, not a gate on settled money;
--   the supply of claimable payments dries up by itself within one checkout
--   window. Branch (b) above
--   deliberately never reads `enabled`, because the alternative is an operator
--   flipping a switch to stop new signups and silently stepping every existing
--   member's next AUTO-RENEWAL up to full price on a saved card. That charge is
--   the exact failure this whole migration exists to prevent (plan §2), and it
--   would arrive with no warning and no human in the loop.
--   A true emergency full stop is therefore NOT a second flag — it is a
--   deliberate, visible, auditable statement against the seat rows:
--       UPDATE public.early_adopter_seats
--          SET released_at = now(), release_reason = 'cancelled'
--        WHERE released_at IS NULL;
--   (which also forfeits everyone it touches — read §1 rule 6 before running it).

-- ⚠ THE 2-ARGUMENT VERSION MUST BE DROPPED, NOT REPLACED. CREATE OR REPLACE
--   cannot change an argument list, so it would leave
--   effective_plan_price(uuid, text) standing beside the 3-argument one. That is
--   not a harmless duplicate: PostgreSQL prefers an EXACT arity match over a
--   candidate that has to fill in a default, so every existing two-argument call
--   — including the renewal job's — would silently keep resolving to the OLD
--   body and keep pricing renewals under the 'purchase' rule. The bug FIX 3
--   exists to remove would survive the fix, invisibly. IF EXISTS makes this a
--   no-op on a first apply.
DROP FUNCTION IF EXISTS public.effective_plan_price(uuid, text);

CREATE OR REPLACE FUNCTION public.effective_plan_price(
    p_user_id uuid,
    p_plan_id text,
    p_context text DEFAULT 'purchase'
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
    -- A caller bug must not silently become a price. Every other "never raise"
    -- rule in this file is about the WEBHOOK — a retry budget that must not be
    -- spent on a 500 for bookkeeping. This is the opposite situation: the return
    -- value IS the amount charged, so failing loudly is strictly safer than
    -- guessing which of the two questions was being asked.
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

    -- Unknown plan → NULL, exactly as a bad plan_id already reads from the
    -- catalog. create_checkout's PAYMENT_PLAN_NOT_PURCHASABLE branch is keyed on
    -- a NULL price, so this stays a 400 and never becomes a free purchase.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    -- §3.4 item 1. A non-purchasable plan (price NULL) also lands here and returns
    -- NULL, which is the same answer the catalog gives today.
    IF v_plan.price_sar IS NULL OR v_plan.promo_price_sar IS NULL THEN
        RETURN round(v_plan.price_sar, 2);
    END IF;

    -- §3.4 item 3 — seat-bearing plans (pro, max).
    IF v_plan.billing_cycle = 'recurring_30d' THEN
        -- (a) FORFEITURE (§1 rule 6) — checked FIRST, so it beats a live seat
        -- and an open campaign alike: "cancel and let it stand ⇒ full price
        -- forever". An undone cancellation leaves no such row (restore clears
        -- released_at and release_reason together), and a 'refund' release is
        -- not a forfeiture. Same predicate as claim_early_adopter_seat.
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.release_reason = 'cancelled') THEN
            RETURN round(v_plan.price_sar, 2);
        END IF;

        -- (b) THE PROMISE (§1 rule 4), and THE WHOLE OF 'current'. Note what is
        -- NOT in this condition: early_adopter_open(). A live seat inside its
        -- window is priced at the promo even after the campaign has closed AND
        -- even after the switch has been flipped — that is the 90-day
        -- commitment, and the renewal job calling this is what honours it.
        IF EXISTS (SELECT 1
                     FROM public.early_adopter_seats s
                    WHERE s.user_id = p_user_id
                      AND s.released_at IS NULL
                      AND s.promo_ends_at > now()) THEN
            RETURN round(v_plan.promo_price_sar, 2);
        END IF;

        -- (c) ⚠ 'purchase' ONLY: the checkout that is ABOUT to claim a seat.
        -- Reaching this branch from a renewal would charge 49.90 to a pro
        -- subscriber who never enrolled, burning no seat and appearing in no
        -- count — see the two failure modes above.
        IF p_context = 'purchase' AND public.early_adopter_open() THEN
            RETURN round(v_plan.promo_price_sar, 2);
        END IF;

        -- (d)
        RETURN round(v_plan.price_sar, 2);
    END IF;

    -- §3.4 item 2 — everyone's discount while seats remain (basic). Gated on
    -- 'purchase' for the same reason as (c). basic never renews (one_time), so
    -- the only caller that asks 'current' about it is the upgrade credit —
    -- read the BASIC RESIDUAL note above before changing this line.
    IF p_context = 'purchase' AND public.early_adopter_open() THEN
        RETURN round(v_plan.promo_price_sar, 2);
    END IF;

    -- §3.4 item 4.
    RETURN round(v_plan.price_sar, 2);
END;
$$;

COMMENT ON FUNCTION public.effective_plan_price(uuid, text, text) IS
    'THE price of a plan FOR A GIVEN USER AT THIS INSTANT (138) — the single '
    'definition consumed by checkout, the renewal job and the upgrade credit. '
    'p_context says WHICH QUESTION is being asked and is load-bearing: '
    '''purchase'' (default) = "what would this plan cost this user if they '
    'bought it now" — used by create_checkout; ''current'' = "what is this user '
    'already being charged for this plan" — used by the RENEWAL JOB and by '
    '_upgrade_credit when it prices the OLD plan. The only difference is the '
    'open-campaign fallback, which ''current'' does not get. '
    '⚠ DO NOT COLLAPSE THE TWO. Pricing a renewal as a ''purchase'' charges '
    '49.90 to a pro subscriber who never enrolled, every 30 days, burning no '
    'seat and appearing in no count — and it silently enrols the pre-campaign '
    'payers §1.2 says are not enrolled. Pricing the upgrade credit as a '
    '''purchase'' credits a FULL-PRICE pro holder 49.90 for a plan they paid '
    '89.90 for, losing them ~34 SAR (the mirror of plan §4''s money bug). '
    'Returns promo_price_sar when the plan renews and the user holds a live seat '
    'inside its 90-day window — REGARDLESS of the campaign switch (§1 rule 4), '
    'which is the promise the renewal job honours by calling this instead of '
    'reading the catalog — or, under ''purchase'' only, when seats are still '
    'open (pro/max: the purchase about to claim one; basic: everybody, no '
    'enrolment). Returns price_sar otherwise, and ALWAYS for a user with a seat '
    'released with reason ''cancelled'' (§1 rule 6: cancelling forfeits the '
    'price permanently; undoing it clears the row and the forfeiture together). '
    'NULL p_user_id is legal and means "no seat, no forfeiture". Any p_context '
    'other than the two values RAISES 22023 rather than guessing — a wrong '
    'answer here is a wrong amount charged. Rounded to 2dp. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.effective_plan_price(uuid, text, text) TO service_role;


-- ── 4.3 claim_early_adopter_seat — the 100th-seat race (§3.5) ────────────────
--
-- Called AFTER grant_plan returns, NEVER inside it (plan §4: 113/119/120 all
-- established that the live money-path RPC is not edited for a side concern).
--
-- THE LOCK. pg_advisory_xact_lock(hashtext('early_adopter_seats')) is taken as
-- the FIRST statement — earlier than §3.5 strictly requires ("before counting"),
-- and on purpose: it serialises the idempotency READS as well as the count, so
-- the webhook and /verify arriving in the same millisecond cannot both walk past
-- "does this payment already have a seat?" and race into the INSERT. Claims
-- happen ~100 times in the campaign's entire life, so serialising them costs
-- nothing measurable. The lock is transaction-scoped: it is released by COMMIT
-- or ROLLBACK, never left dangling.
--
-- ⚠ ANY OTHER WRITER OF THIS TABLE MUST TAKE THE SAME LOCK WITH THE SAME KEY.
--   restore_early_adopter_seat does. A manual INSERT in the SQL editor does not
--   — which is fine at 3am with nobody paying, and is why the INSERT below still
--   catches unique_violation.
--
-- THE INVARIANT THIS FUNCTION EXISTS TO MAINTAIN, and the sentence every other
-- rule in the file leans on. It has no asterisk and no exceptions:
--
--     A SEAT HOLDER IS PRECISELY SOMEONE WHO WAS CHARGED THE PROMOTIONAL PRICE.
--     No capacity state, flag state or campaign edge changes that.
--
-- Both halves matter. Charged the promo and no seat = we took 49.90 and withheld
-- the 90-day promise it bought, then step them up to 89.90 at the next renewal.
-- Charged full price and given a seat = 90 days of discounted renewals nobody
-- paid for, plus a burnt seat that was meant for a campaign buyer.
--
-- The promo-quote gate in the body is what makes the sentence true, and it is
-- THE LAST REFUSAL IN THE FUNCTION: past it, a seat is always issued and the
-- only remaining question is what to stamp on it. All three campaign edges
-- resolve the same way, because they are the same question —
--   * opening:  a pre-campaign quote (89.90) settling after the flag went up
--               → refused by the gate;
--   * closing:  a promo quote settling after seat 100 filled
--               → seated, stamped over_capacity (§3.5);
--   * switched off: a promo quote settling after the flag went down
--               → seated. The decision was made at checkout, and §3.5 does not
--                 stop applying because an operator flipped a switch.
--
-- OVER-CAPACITY IS DELIBERATE AND IS THE RIGHT FAILURE (§3.5): a quote priced at
-- the promo while seats were open can settle after the campaign closed. Refusing
-- it means refusing a payment that already succeeded; repricing it means
-- charging someone the full price after quoting them the promo. So the seat is
-- granted anyway and stamped. Past the gate there is NO refusal on capacity
-- grounds at all — the gate is what stops payer #101 walking in, because payer
-- #101 was quoted 89.90.
--
-- ACTION VALUES — the discriminator, copied from revoke_plan_grant (113). The
-- three named in the plan are 'claimed' / 'already_claimed' / 'campaign_disabled';
-- five more are needed to describe reality and are listed here so the caller can
-- log them. ⚠ THE ONLY TEST A CALLER NEEDS IS `seat_id IS NOT NULL`: every
-- action other than 'claimed' and 'already_claimed' returns a NULL seat_id.
--
--   claimed            a new seat was issued (check over_capacity too)
--   already_claimed    this payment, or this user's live seat, already exists —
--                      the row returned is the EXISTING one, untouched. This is
--                      both the webhook/verify replay AND §1 rule 8's upgrade:
--                      pro → max mid-window keeps the original window
--   campaign_disabled  NO CAMPAIGN ROW AT ALL — there is no promo_days to
--                      anchor a window with, so the seat cannot be built. A
--                      broken install, not a policy decision; it RAISEs a
--                      WARNING because a promo-priced customer is sitting
--                      unseated. ⚠ enabled = false does NOT produce this: a
--                      payment already charged the promo price is seated
--                      regardless of the flag (the flag stops new promo QUOTES,
--                      one layer up in effective_plan_price)
--   not_promo_priced   this payment was quoted the LIST price, so by the
--                      invariant above it buys no seat — whatever the capacity
--                      state. Covers BOTH edges: a pre-campaign quote settling
--                      after the campaign opened, and payer #101 after it filled
--   forfeited          the user cancelled a previous seat and let it stand
--                      (§1 rule 6). They were quoted the LIST price, so refusing
--                      the seat keeps price and enrolment in agreement
--   plan_not_eligible  the payment's plan does not bear seats (basic, free, …)
--   payment_not_found  unknown p_payment_id — logged, NEVER raised: this runs on
--                      the Moyasar webhook, which has a finite retry budget and
--                      must not spend it on a 500 (113/137's posture)
--   user_mismatch      p_user_id is NULL, or is not the payment's user. A caller
--                      bug, refused rather than recorded
--
-- ⚠ 'campaign_full' NO LONGER EXISTS (removed 2026-08-16 with the gate hoist).
--   It used to mean "seats gone AND quoted at list price" — but that is just the
--   list-price case, which is now caught earlier and universally as
--   'not_promo_priced'. A promo-priced payment arriving after the campaign
--   filled is NOT refused: it gets an over_capacity seat. So a full campaign
--   never produces a distinct action, and a caller still switching on
--   'campaign_full' is switching on a value that can never arrive.

CREATE OR REPLACE FUNCTION public.claim_early_adopter_seat(p_user_id uuid, p_payment_id uuid)
RETURNS TABLE(seat_id uuid, promo_ends_at timestamptz, over_capacity boolean, action text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
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
    -- ⚠ FIRST. See the lock note above. hashtext() is int4 and the cast to
    -- bigint is explicit only so the overload resolved is never in doubt; the
    -- KEY VALUE is unchanged, so any other caller written as
    -- pg_advisory_xact_lock(hashtext('early_adopter_seats')) takes the same one.
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

    -- user_id is nullable on this table (header deviation (a)), so THIS is the
    -- guard that keeps a ghost seat — one nobody can hold and nobody can release
    -- — from ever consuming capacity.
    IF p_user_id IS NULL OR (v_pay.user_id IS NOT NULL AND v_pay.user_id <> p_user_id) THEN
        RAISE NOTICE 'claim_early_adopter_seat: user mismatch (arg=%, payment=%)',
                     p_user_id, v_pay.user_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'user_mismatch'::text;
        RETURN;
    END IF;

    -- Idempotency key 2: the user already holds a live seat. §1 rule 8 — an
    -- upgrade carries the window over: no reset, no new window, and the second
    -- payment does NOT become the seat's payment_id. (early_adopter_seats_one_live
    -- would refuse the insert anyway; returning the existing seat is the useful
    -- answer rather than a 23505 the backend has to interpret.)
    SELECT s.seat_id AS id, s.promo_ends_at AS ends, s.over_capacity AS over_cap
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    IF FOUND THEN
        RETURN QUERY SELECT v_seat.id, v_seat.ends, v_seat.over_cap, 'already_claimed'::text;
        RETURN;
    END IF;

    -- Seat-bearing plan? (billing_cycle, not a hard-coded id list — see §4's
    -- preamble.) basic buyers reach here on every purchase and leave here.
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
    -- payment still reads as 'plan_not_eligible' (basic has no enrolment and
    -- therefore no forfeiture — §1 rule 9).
    --
    -- An undone cancellation is NOT a forfeiture: restore_early_adopter_seat
    -- clears released_at and release_reason together, so no row survives to
    -- match. A 'refund' release is not a forfeiture either (§1 rule 5) — that
    -- user may buy back in while seats remain, and this branch lets them.
    IF EXISTS (SELECT 1
                 FROM public.early_adopter_seats s
                WHERE s.user_id = p_user_id
                  AND s.release_reason = 'cancelled') THEN
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, false, 'forfeited'::text;
        RETURN;
    END IF;

    -- ⚠ THE PROMO-QUOTE GATE — THE INVARIANT, AND IT GATES EVERY CLAIM.
    --
    --     A SEAT HOLDER IS PRECISELY SOMEONE WHO WAS CHARGED THE PROMO PRICE.
    --
    -- gross reconstructs the plan price that was on the screen when the quote was
    -- made: create_checkout stores charge = q2(price - credit) in amount_sar and
    -- credit in upgrade_credit_sar (payment_service.py), so the sum is the price
    -- it quoted — prorated upgrades included. One cent of tolerance covers the
    -- q2 rounding.
    --
    -- WHY IT IS HERE AND NOT INSIDE THE CAPACITY BRANCH (fixed 2026-08-16 —
    -- the campaign has TWO edges, and this used to be checked at only one of
    -- them). A quote created BEFORE the campaign opened is priced at 89.90; if it
    -- settles DURING the open campaign it would otherwise claim a seat, handing
    -- someone who paid full price 90 days of 49.90 renewals they never bought
    -- into AND burning capacity meant for a campaign buyer. That is the exact
    -- mirror of the closing-instant overshoot in §3.5, at the opening instant,
    -- and it takes the same test. Bounded by the open quotes outstanding when the
    -- switch is flipped.
    --
    -- NULL amount_sar fails the gate (v_gross IS NULL): the invariant is a
    -- statement about what the customer was CHARGED, and a row that cannot say
    -- what it charged cannot prove it. Fail closed — a malformed row must not
    -- mint a seat.
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
    -- Everything below this line is a promo-priced payment, so the only question
    -- left is what to STAMP on the seat — never whether to issue one.
    --
    -- ⚠ `is_on`, not `on`: ON is a reserved word and `AS on` is a syntax error.
    SELECT c.seat_limit AS lim, c.promo_days AS days, c.enabled AS is_on
      INTO v_camp
      FROM public.early_adopter_campaign c
     WHERE c.id;

    -- 'campaign_disabled' now means ONE thing: there is no campaign row, so
    -- there is no promo_days to anchor a window with and no seat_limit to
    -- measure against. That is a broken install, not a policy decision — and it
    -- is the only case left in which a promo-priced payment goes unseated.
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
    -- decision was made at checkout: this customer was quoted and charged 49.90,
    -- and §3.5's rule — a completed payment is never repriced and never turned
    -- away — does not stop applying because an operator flipped a switch while
    -- their quote was open. Refusing here would take the money and withhold the
    -- 90 days it bought, then step them up to 89.90 at the next renewal.
    --
    -- The switch is still a complete stop, because it works one layer up:
    -- effective_plan_price branch (c) reads early_adopter_open(), so with the
    -- flag down NOTHING NEW IS EVER QUOTED at the promo price and the supply of
    -- claimable payments dries up by itself. Exposure is the open quotes
    -- outstanding at the flip — the same bounded 0–3 as the other two edges.
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
    -- confirmation paths gets there first. now() is a fallback for the
    -- out-of-order caller only — in the intended order (mark paid → snapshot →
    -- grant_plan → claim) paid_at is always set.
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
$$;

COMMENT ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) IS
    'Enrol a paid pro/max purchase in المشتركون الأوائل (138). THE INVARIANT: a '
    'seat holder is precisely someone who was charged the promotional price, '
    'with no exceptions for capacity, the campaign switch, or either edge of the '
    'campaign. Every claim is gated on amount_sar + upgrade_credit_sar matching '
    'plans.promo_price_sar, so a full-price payment never buys a seat (a '
    'pre-campaign quote settling after the campaign opened; payer #101 after it '
    'filled) and a promo-priced payment is never refused one — including after '
    'the switch is flipped off, because the flag stops new promo QUOTES one '
    'layer up in effective_plan_price rather than stranding money already taken. '
    'Called AFTER grant_plan returns, never inside it. Serialised on '
    'pg_advisory_xact_lock(hashtext(''early_adopter_seats'')) so two payments '
    'settling in the same second cannot both become #100. Idempotent on '
    'payment_id (the webhook/verify double-run) and on the user''s live seat (an '
    'upgrade carries the window over). Refuses a user who forfeited by '
    'cancelling a previous seat (§1 rule 6, action = ''forfeited'') — the same '
    'predicate effective_plan_price uses, so the price they were quoted and the '
    'seat they get can never disagree. Anchors the 90-day window on the '
    'payment''s paid_at, never now(). Grants a seat PAST the limit — stamped '
    'over_capacity — when a promo-priced payment settles after the campaign '
    'closed: a completed payment is never repriced or refused. Returns '
    'an `action` discriminator; seat_id IS NULL means no seat was issued. Never '
    'raises on a missing payment — the webhook must not 500. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.claim_early_adopter_seat(uuid, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.claim_early_adopter_seat(uuid, uuid) TO service_role;


-- ── 4.4 release_early_adopter_seat — refund (rule 5) or cancellation (rule 6) ─
--
-- 'refund'    — the money went back, so the seat goes back to the pool and the
--               status is void. They may buy in again if seats remain, and that
--               purchase claims a NEW seat with a NEW window. NOT restorable,
--               and NOT a forfeiture.
-- 'cancelled' — the user cancelled renewal and let it stand. The seat returns to
--               the pool AND the row becomes the permanent forfeiture record
--               (§1 rule 6): while it stands, effective_plan_price quotes list
--               price for pro/max and claim_early_adopter_seat refuses a new
--               seat. Restorable, and ONLY this reason is: «تراجع عن الإلغاء»
--               calls restore_early_adopter_seat, which clears the row and the
--               forfeiture in the same statement.
--
-- ⚠ THE CANCEL DIALOG MUST SAY THIS BEFORE THE CONFIRM (§6.2). Writing
--   'cancelled' here is not bookkeeping — it is the moment the user loses the
--   price permanently, and the undo deadline is the only thing standing between
--   them and that outcome.
--
-- Returns TRUE only when a live seat was actually released, so the caller can
-- tell "gave the seat back" from "there was nothing to give back" — but the
-- caller must treat FALSE as informational, never as a failure: per plan §4 both
-- call sites run BEST-EFFORT and AFTER their own write (the cancellation flag,
-- the refund), following clear_renewal_cancellation's posture. THE FLAG IS THE
-- CANCELLATION. A seat-bookkeeping failure must never surface to the user as a
-- failed cancel or a failed refund.
--
-- Deliberately NOT idempotency-keyed on a payment: a refund and a cancellation
-- both release "whatever live seat this user holds", of which there is at most
-- one, and running either twice is a no-op returning false.

CREATE OR REPLACE FUNCTION public.release_early_adopter_seat(p_user_id uuid, p_reason text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_n INTEGER;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN false;
    END IF;

    -- The CHECK constraint would raise; the refund path must not 500 over a
    -- typo'd literal, so this is a NOTICE and a false instead.
    IF p_reason IS NULL OR p_reason NOT IN ('refund', 'cancelled') THEN
        RAISE NOTICE 'release_early_adopter_seat: refusing unknown reason %', p_reason;
        RETURN false;
    END IF;

    UPDATE public.early_adopter_seats s
       SET released_at    = now(),
           release_reason = p_reason
     WHERE s.user_id = p_user_id
       AND s.released_at IS NULL;

    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n > 0;
END;
$$;

COMMENT ON FUNCTION public.release_early_adopter_seat(uuid, text) IS
    'Give a المشتركون الأوائل seat back to the pool (138). reason = ''refund'' '
    '(§1 rule 5 — status void, they may buy back in, NOT restorable, NOT a '
    'forfeiture) or ''cancelled'' (§1 rule 6 — the row becomes the permanent '
    'forfeiture record: list price on pro/max from now on, and no new seat, '
    'unless «تراجع عن الإلغاء» restores it). Returns TRUE only if a live seat '
    'was released; FALSE '
    'is informational and must never be surfaced as a failed cancel or a failed '
    'refund — both callers run this best-effort AFTER their own write. '
    'Service-role only.';

REVOKE EXECUTE ON FUNCTION public.release_early_adopter_seat(uuid, text)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.release_early_adopter_seat(uuid, text) TO service_role;


-- ── 4.5 restore_early_adopter_seat — «تراجع عن الإلغاء» ──────────────────────
--
-- UNCONDITIONAL by decision (§1 rule 6: "Undo restores the seat
-- unconditionally"), which means it does NOT consult early_adopter_open() and
-- does NOT care whether the campaign has since closed or been disabled. The user
-- is undoing a cancellation of a promise we made; capacity is our problem, not
-- theirs. If the seat has since been re-issued to somebody else, the restore
-- pushes past the limit and is stamped over_capacity, exactly like a late-
-- settling promo quote.
--
-- ONLY A CANCELLATION IS RESTORABLE, and specifically only the user's MOST
-- RECENT release: if the last thing that happened to this user's seat was a
-- refund, there is nothing to undo — the money went back. (Reading it as "the
-- most recent CANCELLED release, skipping over any refund after it" would let a
-- refunded re-purchase resurrect a seat the refund had already voided. The
-- stricter reading is the one implemented; this is the one place §3.4's one-line
-- description is genuinely ambiguous.)
--
-- Takes the same advisory lock as the claim: it both counts capacity and writes
-- the live-seat index.

CREATE OR REPLACE FUNCTION public.restore_early_adopter_seat(p_user_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_seat  RECORD;
    v_live  INTEGER;
    v_limit INTEGER;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN false;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('early_adopter_seats')::bigint);

    -- Already live → the post-condition the caller wants already holds. TRUE,
    -- and nothing is written (in particular over_capacity is not re-stamped).
    IF EXISTS (SELECT 1 FROM public.early_adopter_seats s
                WHERE s.user_id = p_user_id AND s.released_at IS NULL) THEN
        RETURN true;
    END IF;

    SELECT s.seat_id AS id, s.release_reason AS reason
      INTO v_seat
      FROM public.early_adopter_seats s
     WHERE s.user_id = p_user_id
       AND s.released_at IS NOT NULL
     ORDER BY s.released_at DESC
     LIMIT 1;

    IF NOT FOUND OR v_seat.reason IS DISTINCT FROM 'cancelled' THEN
        RETURN false;
    END IF;

    SELECT count(*) INTO v_live
      FROM public.early_adopter_seats s
     WHERE s.released_at IS NULL;

    SELECT c.seat_limit INTO v_limit
      FROM public.early_adopter_campaign c
     WHERE c.id;

    UPDATE public.early_adopter_seats s
       SET released_at    = NULL,
           release_reason = NULL,
           -- Sticky: a seat that was ever over capacity stays flagged, so the
           -- `WHERE over_capacity` audit query stays complete.
           over_capacity  = s.over_capacity
                            OR (v_limit IS NOT NULL AND v_live >= v_limit)
     WHERE s.seat_id = v_seat.id;

    RETURN true;
END;
$$;

COMMENT ON FUNCTION public.restore_early_adopter_seat(uuid) IS
    'Undo a cancellation-release and put the user back in المشتركون الأوائل '
    '(138) — the «تراجع عن الإلغاء» path. UNCONDITIONAL (§1 rule 6): it ignores '
    'capacity and the campaign switch, and stamps over_capacity if the seat had '
    'already been re-issued. Clearing release_reason is also what LIFTS THE '
    'FORFEITURE — there is no separate flag, so the undo restores the seat and '
    'the price in one statement. Restores the user''s MOST RECENT release and only '
    'if that release was a cancellation — a refund is never restorable. Returns '
    'TRUE if the user holds a live seat afterwards. Best-effort at the call '
    'site, like release_early_adopter_seat. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.restore_early_adopter_seat(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.restore_early_adopter_seat(uuid) TO service_role;


-- ── 4.6 early_adopter_status — the user's OWN state, and no count ────────────
--
-- Feeds GET /payments/subscription's `early_adopter: {is_member, promo_ends_at}`
-- and, through it, the cancellation warning in إعدادات الحساب (§6.2). Returns
-- EXACTLY ONE ROW for any input, including an unknown user.
--
-- has_seat means THE PROMO PRICE IS IN FORCE RIGHT NOW — a live seat, inside its
-- window, not forfeited — and is deliberately the same test effective_plan_price
-- makes, so the dialog can never say «أنت من المشتركين الأوائل» about a price
-- the checkout is no longer honouring. A lapsed member gets has_seat = false
-- with promo_ends_at in the past, which is enough for the UI to say "your
-- promotional price ended on …" if it ever wants to.
--
-- It does NOT read `enabled` beyond what early_adopter_open() already does: a
-- member keeps has_seat = true after the campaign closes or is switched off,
-- because they keep the price (§1 rule 4).
--
-- NOTE FOR THE CANCEL DIALOG (§6.2): the warning is rendered BEFORE the confirm,
-- while the user still holds the seat, so has_seat is true at exactly the moment
-- it is needed. AFTER a cancellation the seat is released and this returns
-- has_seat = false / promo_ends_at NULL — the undo affordance is driven by
-- user_subscriptions.renewal_cancelled_at (120), not by this function.
--
-- campaign_open is included so one round-trip answers both questions the client
-- has. It is a boolean; there is no count here and there must never be one.

CREATE OR REPLACE FUNCTION public.early_adopter_status(p_user_id uuid)
RETURNS TABLE(campaign_open boolean, has_seat boolean, promo_ends_at timestamptz)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
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
    -- has_seat must mean EXACTLY what effective_plan_price charges. In every
    -- reachable state a live seat and a 'cancelled' row cannot coexist (claim
    -- refuses a forfeited user), so this changes nothing in practice — it keeps
    -- the invariant true by construction rather than by argument, including for
    -- a hand-inserted seat.
    v_forfeit := EXISTS (SELECT 1
                           FROM public.early_adopter_seats s
                          WHERE s.user_id = p_user_id
                            AND s.release_reason = 'cancelled');

    RETURN QUERY
        SELECT public.early_adopter_open(),
               (v_ends IS NOT NULL AND v_ends > now() AND NOT v_forfeit),
               v_ends;
END;
$$;

COMMENT ON FUNCTION public.early_adopter_status(uuid) IS
    'One row describing a user''s own المشتركون الأوائل state (138): is the '
    'campaign still taking seats, does this user hold one whose window is still '
    'open, and when does it end. has_seat uses the same test as '
    'effective_plan_price — live seat, inside its window, not forfeited by a '
    'standing cancellation — so the UI can never claim a price checkout is not '
    'honouring, and it stays true after the campaign closes because the price '
    'does. Carries NO count and NO seat total (§1 rule 10). Always returns '
    'exactly one row, including for an unknown user. Service-role only — the '
    'backend surfaces it on GET /payments/subscription.';

REVOKE EXECUTE ON FUNCTION public.early_adopter_status(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.early_adopter_status(uuid) TO service_role;


COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY VERIFICATION — run manually; every check must PASS
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. ⚠ THE INERTNESS CHECK, first and most important. EXPECT one row:
-- --    seat_limit=100, promo_days=90, enabled=FALSE.
-- SELECT * FROM public.early_adopter_campaign;
--
-- -- 2. The catalog carries the promo prices and price_sar is UNTOUCHED.
-- --    EXPECT: basic 49.90/39.90, pro 89.90/49.90, max 189.90/99.90,
-- --            free/dev/marketing_* both NULL.
-- SELECT plan_id, price_sar, promo_price_sar, billing_cycle, duration_days
--   FROM public.plans ORDER BY price_sar NULLS LAST;
--
-- -- 3. NOBODY is priced differently yet, in EITHER context. EXPECT both columns
-- --    = price_sar on every row (pick any real user_id).
-- SELECT p.plan_id, p.price_sar,
--        public.effective_plan_price('<user_id>'::uuid, p.plan_id, 'purchase') AS buy,
--        public.effective_plan_price('<user_id>'::uuid, p.plan_id, 'current')  AS now_
--   FROM public.plans p ORDER BY 1;
--
-- -- 3b. The context argument is validated, not guessed. EXPECT 22023
-- --     (invalid_parameter_value), NOT a price.
-- SELECT public.effective_plan_price('<user_id>'::uuid, 'pro', 'renewal');
--
-- -- 3c. ⚠ EXACTLY ONE effective_plan_price EXISTS. Two rows means the two-arg
-- --     overload survived a prior apply and is silently serving every two-arg
-- --     call — including the renewal job's. EXPECT: 1.
-- SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname = 'public' AND p.proname = 'effective_plan_price';
--
-- -- 4. The campaign is closed. EXPECT: false.
-- SELECT public.early_adopter_open();
--
-- -- 5. Both tables are deny-all for client roles. EXPECT: ZERO ROWS.
-- SELECT table_name, grantee, privilege_type
--   FROM information_schema.role_table_grants
--  WHERE table_schema='public'
--    AND table_name IN ('early_adopter_seats','early_adopter_campaign')
--    AND grantee IN ('anon','authenticated');
--
-- -- 6. RLS on, ZERO policies on both (a policy here could only leak the count).
-- --    EXPECT: two rows, relrowsecurity=true, n_policies=0.
-- SELECT c.relname, c.relrowsecurity,
--        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS n_policies
--   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
--  WHERE n.nspname='public'
--    AND c.relname IN ('early_adopter_seats','early_adopter_campaign');
--
-- -- 7. ⚠ EXECUTE revoked from the client roles on all SIX functions.
-- --    EXPECT: ZERO ROWS. (If a row appears, that function is reachable from a
-- --    browser through PostgREST — and none of them checks who is calling.)
-- SELECT routine_name, grantee, privilege_type
--   FROM information_schema.routine_privileges
--  WHERE routine_schema='public'
--    AND routine_name IN ('early_adopter_open','effective_plan_price',
--                         'claim_early_adopter_seat','release_early_adopter_seat',
--                         'restore_early_adopter_seat','early_adopter_status')
--    AND grantee IN ('anon','authenticated','PUBLIC');
--
-- -- 7b. And the positive half: service_role CAN execute all six. EXPECT: 6.
-- SELECT count(DISTINCT routine_name)
--   FROM information_schema.routine_privileges
--  WHERE routine_schema='public' AND grantee='service_role'
--    AND routine_name IN ('early_adopter_open','effective_plan_price',
--                         'claim_early_adopter_seat','release_early_adopter_seat',
--                         'restore_early_adopter_seat','early_adopter_status');
--
-- -- 8. The signatures are EXACTLY what the backend was written against.
-- SELECT p.proname,
--        pg_get_function_identity_arguments(p.oid) AS args,
--        pg_get_function_result(p.oid)             AS result
--   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
--  WHERE n.nspname='public'
--    AND p.proname IN ('early_adopter_open','effective_plan_price',
--                      'claim_early_adopter_seat','release_early_adopter_seat',
--                      'restore_early_adopter_seat','early_adopter_status')
--  ORDER BY 1;
-- -- EXPECT:
-- --   claim_early_adopter_seat    p_user_id uuid, p_payment_id uuid
-- --                               TABLE(seat_id uuid, promo_ends_at timestamptz,
-- --                                     over_capacity boolean, action text)
-- --   early_adopter_open          (no args)                       → boolean
-- --   early_adopter_status        p_user_id uuid
-- --                               TABLE(campaign_open boolean, has_seat boolean,
-- --                                     promo_ends_at timestamptz)
-- --   effective_plan_price        p_user_id uuid, p_plan_id text,
-- --                               p_context text DEFAULT 'purchase' → numeric
-- --                               (⚠ EXACTLY ONE row for this name. Two rows =
-- --                                the 2-arg overload survived and is shadowing
-- --                                this one for every 2-arg call site.)
-- --   release_early_adopter_seat  p_user_id uuid, p_reason text   → boolean
-- --   restore_early_adopter_seat  p_user_id uuid                  → boolean
--
-- -- 9. The seat table's shape. EXPECT: user_id is_nullable = YES (deviation (a)
-- --    — if it says NO, account deletion is broken and 23502 is waiting).
-- SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_schema='public' AND table_name='early_adopter_seats'
--  ORDER BY ordinal_position;
--
-- -- 10. ⚠ THE FK ACTION on user_id must be 'n' (SET NULL). 'c' = CASCADE would
-- --     erase the enrolment evidence; 'a' = NO ACTION breaks account deletion.
-- SELECT con.conname, con.confdeltype
--   FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
--  WHERE c.relname='early_adopter_seats' AND con.contype='f';
--
-- -- 11. Nobody is enrolled. EXPECT: 0. (§1 rule 2 — the count starts at zero on
-- --     launch day; the three existing payers are NOT enrolled and NOT repriced.)
-- SELECT count(*) FROM public.early_adopter_seats;
--
-- ── THE THREE OWNER AMENDMENTS, PROVEN. Run inside a transaction and ROLLBACK.
-- ── Substitute a real user_id. Every block must end in ROLLBACK: they write to
-- ── the campaign row and the seat table.
--
-- -- 12. ⚠ THE FLAG DOES NOT REPRICE A LIVE SEAT (§1 rule 4) — and the RENEWAL
-- --     context is the one that has to prove it, because the renewal job is the
-- --     charge that would land on a saved card.
-- -- BEGIN;
-- --   UPDATE public.early_adopter_campaign SET enabled = true;
-- --   INSERT INTO public.early_adopter_seats (user_id, claimed_at, promo_ends_at)
-- --   VALUES ('<user_id>', now(), now() + interval '90 days');
-- --   SELECT public.effective_plan_price('<user_id>','pro','current'); -- 49.90
-- --   -- now switch the campaign OFF and re-ask, in BOTH contexts:
-- --   UPDATE public.early_adopter_campaign SET enabled = false;
-- --   SELECT public.early_adopter_open();                              -- false
-- --   SELECT public.effective_plan_price('<user_id>','pro','current'); -- 49.90 STILL
-- --   SELECT public.effective_plan_price('<user_id>','pro','purchase');-- 49.90 STILL
-- --   SELECT public.effective_plan_price('<user_id>','basic');         -- 49.90 (list)
-- --   SELECT * FROM public.early_adopter_status('<user_id>');          -- open=false,
-- --                                                                    -- has_seat=TRUE
-- -- ROLLBACK;
-- -- If pro comes back 89.90 after the flag goes down, FIX 2 is not in and every
-- -- member's next auto-renewal is a silent step-up. Stop and fix before launch.
--
-- -- 12b. ⚠ A NON-MEMBER'S RENEWAL IS NOT DISCOUNTED (the whole point of
-- --      p_context). Use a user with NO seat row at all, campaign OPEN.
-- -- BEGIN;
-- --   UPDATE public.early_adopter_campaign SET enabled = true;
-- --   SELECT public.early_adopter_open();                                -- true
-- --   SELECT public.effective_plan_price('<nonmember>','pro','purchase');-- 49.90
-- --   SELECT public.effective_plan_price('<nonmember>','pro','current'); -- 89.90
-- -- ROLLBACK;
-- -- The second number is what renewal_service and _upgrade_credit must see. If
-- -- 'current' returns 49.90, every existing pro subscriber is being renewed at a
-- -- discount they never enrolled in, and the pre-campaign payers §1.2 excludes
-- -- have just been enrolled by accident. That is the bug this argument exists
-- -- for; it is also invisible in the seat table, because it burns no seat.
--
-- -- 13. ⚠ CANCELLING FORFEITS THE PRICE PERMANENTLY (§1 rule 6), AND UNDOING
-- --     GIVES IT BACK.
-- -- BEGIN;
-- --   UPDATE public.early_adopter_campaign SET enabled = true;   -- seats OPEN
-- --   INSERT INTO public.early_adopter_seats (user_id, claimed_at, promo_ends_at)
-- --   VALUES ('<user_id>', now(), now() + interval '90 days');
-- --   SELECT public.effective_plan_price('<user_id>','pro');  -- EXPECT 49.90
-- --   SELECT public.release_early_adopter_seat('<user_id>','cancelled'); -- true
-- --   SELECT public.effective_plan_price('<user_id>','pro');  -- EXPECT 89.90
-- --   --   …even though seats are open. That is the forfeiture.
-- --   SELECT public.effective_plan_price('<user_id>','basic');-- EXPECT 39.90
-- --   --   …basic is unaffected: discount for everyone, no enrolment (rule 9).
-- --   SELECT public.restore_early_adopter_seat('<user_id>');  -- EXPECT true
-- --   SELECT public.effective_plan_price('<user_id>','pro');  -- EXPECT 49.90 again
-- --   -- and a REFUND release must NOT forfeit:
-- --   SELECT public.release_early_adopter_seat('<user_id>','refund');   -- true
-- --   SELECT public.effective_plan_price('<user_id>','pro');  -- EXPECT 49.90
-- --                                                           -- (seats are open)
-- -- ROLLBACK;
--
-- -- 14. And the claim side agrees with the price side. With a real PAID pro
-- --     payment_id for a user who holds a standing 'cancelled' row:
-- --     SELECT * FROM public.claim_early_adopter_seat('<user_id>','<payment_id>');
-- --     EXPECT action = 'forfeited', seat_id NULL. If it returns 'claimed' while
-- --     effective_plan_price quotes 89.90, the two predicates have drifted apart
-- --     — that is the disagreement §3.5 forbids.
--
-- -- 15. ⚠ THE OPENING EDGE: A FULL-PRICE PAYMENT NEVER BUYS A SEAT. This is the
-- --     case that used to slip through — a quote created BEFORE the campaign
-- --     opened, settling after it opened. Use any real PAID pro payment whose
-- --     amount_sar + upgrade_credit_sar is 89.90, on a user with no seat.
-- -- BEGIN;
-- --   UPDATE public.early_adopter_campaign SET enabled = true;  -- seats OPEN
-- --   SELECT public.early_adopter_open();                       -- true
-- --   SELECT * FROM public.claim_early_adopter_seat('<user_id>','<89.90 payment>');
-- --   -- EXPECT action = 'not_promo_priced', seat_id NULL.
-- --   -- A 'claimed' here means someone who paid 89.90 just collected 90 days of
-- --   -- 49.90 renewals and burnt a seat meant for a campaign buyer.
-- --   SELECT count(*) FROM public.early_adopter_seats;          -- unchanged
-- -- ROLLBACK;
--
-- ── AFTER THE BACKEND DEPLOY, STILL WITH enabled = false (plan §9 steps 3–4) ──
-- -- 16. GET /payments/early-adopter → {"open": false, …}; /pricing shows list
-- --     prices; buy `pro` on a spare account and confirm 89.90 was charged and
-- --     early_adopter_seats is still empty.
--
-- ── FLIPPING IT ON (plan §9 step 5) ──────────────────────────────────────────
-- -- UPDATE public.early_adopter_campaign SET enabled = true;
-- -- …then PURGE THE ISR CACHE for '/' and '/pricing'. ⚠ Purge paths MUST be
-- -- percent-encoded or the 200 is a lie (project_isr_revalidate_percent_encoding).
-- -- Closing is automatic — seat 100 fills and early_adopter_open() goes false —
-- -- but the purge is NOT: add it to the runbook or the public page shows the
-- -- promo for up to `revalidate` seconds after the campaign is over.
--
-- ── THE RACE, PROVEN. Two psql sessions, a spare account, enabled = true and
-- ── seat_limit temporarily set to the current live count (so the next claim is
-- ── the over-capacity one). Session A: BEGIN; SELECT claim…; (do not commit).
-- ── Session B: BEGIN; SELECT claim…; — B must BLOCK on the advisory lock until A
-- ── commits, then return 'already_claimed' or an over_capacity = true seat.
-- ── NEVER two live seats for one user, and never two rows for one payment.
-- ── ROLLBACK both, restore seat_limit.
--
-- ── OPERATOR QUERIES worth keeping ───────────────────────────────────────────
-- -- Live seats and how much room is left (NEVER expose this number):
-- SELECT count(*) FILTER (WHERE released_at IS NULL) AS live,
--        count(*) FILTER (WHERE over_capacity)       AS over,
--        count(*) FILTER (WHERE release_reason='refund')    AS refunded,
--        count(*) FILTER (WHERE release_reason='cancelled') AS forfeited
--   FROM public.early_adopter_seats;
-- -- ⚠ AUDIT THE INVARIANT — "a seat holder is precisely someone who was charged
-- -- the promotional price" — in BOTH directions. Run it after the campaign
-- -- opens and again after it closes.
-- --
-- -- (i) Seats whose claiming payment was NOT promo-priced. EXPECT: ZERO ROWS.
-- --     Any row here is a full-price payer holding a discount they never bought.
-- SELECT s.seat_id, u.email, t.amount_sar, t.upgrade_credit_sar, p.promo_price_sar
--   FROM public.early_adopter_seats s
--   JOIN public.payment_transactions t ON t.payment_id = s.payment_id
--   JOIN public.plans p ON p.plan_id = t.plan_id
--   LEFT JOIN public.users u ON u.user_id = s.user_id
--  WHERE t.amount_sar + COALESCE(t.upgrade_credit_sar, 0) > p.promo_price_sar + 0.01;
-- --
-- -- (ii) The other direction: paid pro/max payments that WERE quoted the promo
-- --      but hold no seat. EXPECT ZERO ROWS IN NORMAL OPERATION — a non-empty
-- --      result is a SIGNAL THAT SOMETHING IS WRONG, not a worklist.
-- --      The `enabled`-off residual this used to catch is CLOSED (2026-08-17):
-- --      the promo-quote gate now precedes the campaign check, so a payment
-- --      charged the promo price seats regardless of the flag. What remains
-- --      here would be a claim that never ran or errored — i.e. a customer who
-- --      paid 49.90 and would renew at 89.90. Remedy per row: INSERT the seat,
-- --      anchored on that payment's paid_at.
-- SELECT t.payment_id, u.email, t.plan_id, t.amount_sar, t.paid_at
--   FROM public.payment_transactions t
--   JOIN public.plans p ON p.plan_id = t.plan_id
--   LEFT JOIN public.users u ON u.user_id = t.user_id
--  WHERE t.status = 'paid' AND t.fulfilled_at IS NOT NULL AND t.revoked_at IS NULL
--    AND p.billing_cycle = 'recurring_30d' AND p.promo_price_sar IS NOT NULL
--    AND t.amount_sar + COALESCE(t.upgrade_credit_sar, 0) <= p.promo_price_sar + 0.01
--    AND NOT EXISTS (SELECT 1 FROM public.early_adopter_seats s
--                     WHERE s.payment_id = t.payment_id)
--  ORDER BY t.paid_at DESC;
--
-- -- Who has forfeited (they now pay list price on pro/max, permanently — the
-- -- only way back is restore_early_adopter_seat, which is «تراجع عن الإلغاء»):
-- SELECT u.email, s.claimed_at, s.released_at, s.promo_ends_at
--   FROM public.early_adopter_seats s
--   LEFT JOIN public.users u ON u.user_id = s.user_id
--  WHERE s.release_reason = 'cancelled'
--  ORDER BY s.released_at DESC;
-- -- Who is a member, and until when:
-- SELECT u.email, s.claimed_at, s.promo_ends_at, s.over_capacity
--   FROM public.early_adopter_seats s
--   LEFT JOIN public.users u ON u.user_id = s.user_id
--  WHERE s.released_at IS NULL
--  ORDER BY s.claimed_at;
