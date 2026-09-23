-- ════════════════════════════════════════════════════════════════════════════
-- 163 — payment_methods.source_type: WHICH instrument a stored credential is
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/applepay_auto_renewal_fix.md §5 "Phase 3 — Teach the
--       backend that a wallet token is a wallet token", item 1.
-- Depends on: 132 (public.payment_methods — the table this adds a column to).
-- Idempotent: ADD COLUMN IF NOT EXISTS, DROP CONSTRAINT IF EXISTS + ADD,
--             COMMENT (always overwrites). Re-runnable, and a no-op on a
--             database where it has already landed.
--
-- ⚠ THIS MIGRATION MOVES NO MONEY, BACK-FILLS NOTHING, AND BREAKS NOTHING.
--   The column is nullable with no DEFAULT, so applying it changes the
--   behaviour of exactly zero existing rows and zero existing queries. It may
--   be applied before OR after the backend that writes it — see "ORDERING".
--
-- ════════════════════════════════════════════════════════════════════════════
-- WHY ────────────────────────────────────────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE INCIDENT (verified in production 2026-09-20). `pro`/`max` are sold as
-- 30-day auto-renewing plans. Renewal charges a stored Moyasar token, and that
-- token only exists if `save_card` was passed at checkout. We passed it inside
-- `credit_card` and never inside `apple_pay`, so every Apple Pay buyer completed
-- a normal purchase, got a normal grant, and was silently never enrolled in
-- renewal. 3 of the first 10 paid purchases were Apple Pay. Nothing warned
-- anyone: the capture logged "nothing stored" at INFO, indistinguishable from a
-- `basic` purchase or a flag-off deploy.
--
-- THE PART THIS COLUMN FIXES is not that bug — the frontend fix (pass
-- `apple_pay.save_card`) is what fixes that. This column fixes the bug's SECOND
-- half, which would otherwise outlive it:
--
--   Nothing in the backend branches on `source.type`. The capture is a pure
--   duck-type on `source.token`. So the moment Apple Pay starts returning a
--   token, that token is stored as a plain card — brand from `company` →
--   'visa', last4 from the funding PAN → '2796' — and from that instant the
--   wallet population and the card population are INDISTINGUISHABLE IN OUR
--   DATABASE, permanently and unrecoverably.
--
-- Two consequences make that worth a column rather than a log line:
--
--   1. An Apple Pay credential is a DEVICE PAN (the `dpan`, ...2764) behind a
--      funding card (the `number`, ...2796). Moyasar states MIT is supported for
--      both, and Visa has postponed the DPAN standing-instruction restriction
--      indefinitely (VBN AI15042) — but "supported" and "declines at the same
--      rate" are different claims, and only one of them has been tested. If
--      wallet renewals ever start declining differently, this column is the
--      only thing that makes the question answerable at all.
--   2. The settings dialog currently renders a wallet credential as «فيزا
--      ••2796» with no hint that the user paid with Apple Pay. With this column
--      it can say «Apple Pay» and the funding card underneath — which is what
--      the customer actually recognises.
--
-- ════════════════════════════════════════════════════════════════════════════
-- WHY EXISTING ROWS STAY NULL (and are NOT back-labelled) ────────────────────
-- ════════════════════════════════════════════════════════════════════════════
--
-- Every row that exists when this is applied gets NULL, and stays NULL forever.
-- That is deliberate, and it is NOT laziness about a back-fill:
--
--   * WE CANNOT KNOW. Provenance lives on the PAYMENT's `source.type`, and the
--     payment that minted an existing token is reachable only through
--     `payment_transactions.raw_payload` — if it is still there, if the row was
--     not purged, if the token was not stored by an operator or by a path that
--     predates raw_payload retention. A back-fill would therefore be a
--     reconstruction, and a reconstructed provenance that is 95% right is worse
--     than an honest NULL: the one query this column exists to answer ("do
--     wallet credentials decline more often?") would be answered with data
--     partly invented by this migration.
--   * DEFAULTING TO 'creditcard' WOULD BE A LIE THAT LOOKS LIKE A FACT. It is
--     also, as of today, PROBABLY TRUE — every stored credential right now came
--     from a card, because Apple Pay never returned a token. "Probably true
--     today" is exactly the kind of assumption that gets frozen into a column
--     and read as gospel two years later. No DEFAULT, and no back-fill.
--
-- So NULL means "written before 163, or by a backend running ahead of it" —
-- never "not a card". Any consumer MUST treat NULL as unknown-but-fine, and the
-- settings UI in particular must render a NULL row exactly as it renders one
-- today, because that is what all existing rows are.
--
-- ════════════════════════════════════════════════════════════════════════════
-- WHY A CHECK RATHER THAN AN ENUM, AND WHY IT IS THE RISK IN THIS FILE ───────
-- ════════════════════════════════════════════════════════════════════════════
--
-- CHECK, not a PG enum: widening a CHECK is one DROP+ADD in the next migration,
-- widening an enum inside a transaction is the thing that goes wrong at 3am.
-- Same posture as 132's own last4/expiry constraints.
--
-- ⚠ THE CONSTRAINT IS THE RISK. `source_type` is written on the SAME INSERT that
--   stores the credential. If Moyasar invents a fourth source type, a naive
--   backend would send it, the CHECK would reject it, and the INSERT would fail
--   WHOLE — costing us the stored card, and therefore the renewal, in order to
--   refuse a label. That trade is absurd, so the backend never makes it:
--   `payment_method_service.KNOWN_SOURCE_TYPES` mirrors this domain exactly and
--   an unrecognised value is logged and stored as NULL. KEEP THE TWO IN SYNC —
--   if you widen the CHECK here, widen that frozenset in the same change.
--
-- The three values are Moyasar's own source types, confirmed in writing
-- 2026-09-22: `save_card` is supported on `creditcard`, `applepay` and
-- `samsungpay`, and those are the three sources a token can come from.
--
-- ════════════════════════════════════════════════════════════════════════════
-- ORDERING — this one is genuinely order-free, unlike 132/133 ────────────────
-- ════════════════════════════════════════════════════════════════════════════
--
-- 132's header says, correctly, "APPLY THIS BEFORE DEPLOYING THE BACKEND THAT
-- WRITES THESE COLUMNS" — because a missing `initiated_by` 42703s a renewal
-- charge, and a charge that does not happen is a customer promise broken.
--
-- This file is the OTHER case, and says so explicitly so nobody generalises the
-- wrong rule: the backend tolerates its absence in BOTH directions.
--
--   * WRITE: the INSERT retries without `source_type` on 42703 — the same
--     pattern `payment_service._mark_failed` uses for `decline_reason` (133).
--     A backend deployed ahead of this migration stores credentials that simply
--     carry no label.
--   * READ: the SELECT asks for `source_type` and retries without it on 42703.
--     That retry is load-bearing, not tidiness — without it a 42703 would reach
--     `get_active_method`'s missing-relation handler, which answers "no stored
--     card", and a user with a perfectly good card would be told in إعدادات
--     الحساب that they have none.
--
-- Applying it is still preferable to not applying it (the fallbacks cost an
-- extra round-trip per settings read, and until it lands no provenance is
-- recorded and the ability is lost for those rows forever). But nothing breaks
-- in either order, and that is the point of the fallbacks.
--
-- ════════════════════════════════════════════════════════════════════════════
-- VERIFY BEFORE APPLYING ─────────────────────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════════════
-- The files in this directory are NOT the production schema (project rule:
-- migration drift) — confirm the shape live before running:
--
--   -- a) Does payment_methods exist, and does it already carry source_type
--   --    (a hand-applied hotfix would make the ADD a no-op and leave only the
--   --    constraint below to land)?
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema='public' AND table_name='payment_methods'
--    ORDER BY ordinal_position;
--
--   -- b) What constraints are on the table now (163 must not collide with a
--   --    name 132 already used)?
--   SELECT con.conname, pg_get_constraintdef(con.oid)
--     FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
--     JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname='public' AND c.relname='payment_methods' AND con.contype='c';
--
--   -- c) How many rows will be left NULL (i.e. how much history this column
--   --    can never describe)? Expect this to equal the total row count.
--   SELECT count(*) AS total, count(*) FILTER (WHERE revoked_at IS NULL) AS active
--     FROM public.payment_methods;
-- ════════════════════════════════════════════════════════════════════════════


-- ════════════════════════════════════════════════════════════════════════════
-- 1. The column
-- ════════════════════════════════════════════════════════════════════════════
--
-- Nullable, no DEFAULT. Both properties are load-bearing — see "WHY EXISTING
-- ROWS STAY NULL" above. text rather than an enum — see the CHECK note above.

ALTER TABLE public.payment_methods
    ADD COLUMN IF NOT EXISTS source_type text;


-- ════════════════════════════════════════════════════════════════════════════
-- 2. The domain
-- ════════════════════════════════════════════════════════════════════════════
--
-- DROP-then-ADD (not a bare ADD) so a re-run, or a hand-applied hotfix that
-- created the column with a different or missing constraint, CONVERGES on
-- exactly this domain rather than erroring or silently leaving the old one in
-- place. Same technique 132 §1 uses on this same table, for the same reason.
--
-- `source_type IS NULL OR …` is not defensive padding: NULL is the state of
-- every pre-163 row and of every row written by a backend that has not been
-- deployed yet, so it must be a first-class legal value, not an accident the
-- constraint happens to permit.

ALTER TABLE public.payment_methods
    DROP CONSTRAINT IF EXISTS payment_methods_source_type_check;

ALTER TABLE public.payment_methods
    ADD CONSTRAINT payment_methods_source_type_check
        CHECK (
            source_type IS NULL
            OR source_type IN ('creditcard', 'applepay', 'samsungpay')
        );


-- ════════════════════════════════════════════════════════════════════════════
-- 3. Documentation that travels with the schema
-- ════════════════════════════════════════════════════════════════════════════
--
-- The NULL semantics are the part a future reader will get wrong, so they are
-- stated on the column itself and not only in this file's header.

COMMENT ON COLUMN public.payment_methods.source_type IS
    'Which INSTRUMENT this credential came from: the Moyasar payment''s '
    'source.type — creditcard | applepay | samsungpay (163). NULL means '
    'UNKNOWN, never "a card": every row written before 163, and every row '
    'written by a backend running ahead of it, is NULL, and they are '
    'deliberately NOT back-filled because provenance cannot be reconstructed '
    'after the fact. Display provenance plus a forensic key — a wallet '
    'credential is a device PAN (source.dpan) behind a funding card, and brand/'
    'last4 describe the FUNDING card, so «Apple Pay» and «فيزا ••2796» are both '
    'true of the same row and the UI shows both. If wallet renewals ever start '
    'declining differently from card renewals, this column is the only thing '
    'that makes the question answerable. Written on the same INSERT as the '
    'token; the backend stores NULL rather than risk this CHECK rejecting the '
    'whole credential over an unrecognised value — keep '
    'payment_method_service.KNOWN_SOURCE_TYPES in sync with the CHECK above.';


-- ════════════════════════════════════════════════════════════════════════════
-- 4. Post-apply verification
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. The column is there, nullable, with no default. EXPECT: text / YES / NULL.
-- SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_schema='public' AND table_name='payment_methods'
--    AND column_name='source_type';
--
-- -- 2. The CHECK is there and admits exactly four states. EXPECT: one row.
-- SELECT con.conname, pg_get_constraintdef(con.oid)
--   FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname='public' AND c.relname='payment_methods'
--    AND con.conname='payment_methods_source_type_check';
--
-- -- 3. Nothing was back-labelled. EXPECT: every existing row NULL.
-- SELECT source_type, count(*) FROM public.payment_methods GROUP BY 1;
--
-- -- 4. The constraint actually bites (run in a transaction and ROLL BACK).
-- --    EXPECT: ERROR 23514 payment_methods_source_type_check.
-- -- BEGIN;
-- --   UPDATE public.payment_methods SET source_type='stcpay'
-- --    WHERE payment_method_id = (SELECT payment_method_id
-- --                                 FROM public.payment_methods LIMIT 1);
-- -- ROLLBACK;
--
-- -- 5. After the frontend ships `apple_pay.save_card` and one live Apple Pay
-- --    purchase completes (plan Phase 4), THIS is the query that proves the
-- --    whole chain worked — and afterwards, the one that answers whether
-- --    wallet credentials renew as reliably as card ones.
-- SELECT source_type, count(*) AS stored,
--        count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked
--   FROM public.payment_methods
--  GROUP BY 1 ORDER BY 1;
--
-- -- 6. The OTHER half of Phase 0: renewable purchases that stored nothing, now
-- --    that the capture writes an audit row for them. Before this change the
-- --    population was invisible; this is what makes it queryable.
-- SELECT metadata->>'source_type' AS source_type,
--        metadata->>'plan_id'     AS plan_id,
--        count(*), min(created_at), max(created_at)
--   FROM public.audit_logs
--  WHERE resource_type='payment_transaction'
--    AND metadata->>'event'='card_token_missing'
--  GROUP BY 1, 2 ORDER BY 3 DESC;
-- ════════════════════════════════════════════════════════════════════════════
