-- ════════════════════════════════════════════════════════════════════════════
-- 132 — subscription auto-renewal (التجديد التلقائي): the schema half
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/subscription_auto_renewal.md §5 "Phase 2 — schema",
--       constrained by §7 (idempotency), §10 (interactions) and §11 (traps).
-- Depends on: 079 (user_subscriptions + handle_subscription_assignment),
--             092 (payment_transactions, plans.price_sar/billing_cycle,
--                  grant_plan), 113 (refund/VAT/proration columns, one_time
--                  catalog), 114 (receipt_no trigger), 117 (payments outlive the
--                  account: user_id nullable + ON DELETE SET NULL),
--             118 (RLS lockdown posture + default-privilege guard),
--             119 (partial-unique-index pattern on the money table),
--             120 (renewal_cancelled_at — the opt-out this file finally makes
--                  chargeable), 129/131 (current shape of the quota RPCs + view).
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
--             DROP CONSTRAINT IF EXISTS + ADD, guarded DO blocks, value-stable
--             UPDATEs. Re-runnable.
--
-- ⚠ APPLY THIS BEFORE DEPLOYING THE BACKEND THAT WRITES THESE COLUMNS.
--   Never the reverse. This is the 119/Moyasar lesson restated: a backend that
--   inserts `initiated_by` / `period_start` against a database without them
--   42703s on every renewal tick, and the tick that fails is a charge that did
--   not happen for a customer whose /terms says it would.
--
-- ⚠ THIS FILE MOVES NO MONEY AND STARTS NO JOB. Everything here is inert until
--   Phases 3–6 of the plan ship. The one statement with a semantic effect the
--   day it is applied is §5 (plans.billing_cycle) — read its note before running.
--
-- ⚠ LIVE INTROSPECTION WAS NOT AVAILABLE WHEN THIS WAS WRITTEN ────────────────
-- The project rule is that the files in this directory are NOT the prod schema
-- (memory: project_migration_drift), and this file was authored in a session
-- with no Supabase MCP access — the same situation 113's header records. Every
-- statement below is therefore written to be drift-tolerant rather than to
-- assume a shape: constraint domains are WIDENED not replaced, the
-- billing_cycle CHECK is discovered by name from pg_constraint instead of being
-- guessed, and `user_subscriptions_live` is deliberately NOT rebuilt (§6).
--
-- Run these FOUR reads before applying. Each one can change a statement below:
--
--   -- a) Does the billing_cycle CHECK still hold 092's narrow domain, and is it
--   --    still named plans_billing_cycle_check? §5's DO block handles any name,
--   --    but confirm nothing else constrains the column.
--   SELECT con.conname, pg_get_constraintdef(con.oid)
--     FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
--     JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname='public' AND c.relname='plans' AND con.contype='c';
--
--   -- b) Is the assignment trigger still BEFORE UPDATE **OF plan_id**? Every
--   --    "write it alone" comment in this file rests on that. (Verified live
--   --    2026-08-08 for 120; re-verify.)
--   SELECT t.tgname, pg_get_triggerdef(t.oid)
--     FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
--    WHERE c.relname='user_subscriptions' AND NOT t.tgisinternal;
--
--   -- c) Does payment_transactions already carry any of the four new columns
--   --    (a hand-applied hotfix would make the ADDs no-ops and leave the
--   --    constraints in §2 as the only thing that lands)?
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema='public' AND table_name='payment_transactions';
--
--   -- d) Does public.update_updated_at() still exist (070/086 use it)? §1's
--   --    trigger is guarded on it, but a missing function means
--   --    payment_methods.updated_at is hand-maintained and the backend must know.
--   SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
--    WHERE n.nspname='public' AND p.proname='update_updated_at';
--
-- WHAT ───────────────────────────────────────────────────────────────────────
--   1. public.payment_methods — the stored-credential record. Token + consent
--      artefact only; never a PAN, never a CVV. 118 lockdown posture.
--   2. payment_transactions — initiated_by / renewal_attempt / payment_method_id
--      / period_start, and the constraints that make a malformed renewal row
--      unrepresentable.
--   3. THE IDEMPOTENCY GUARD — one partial unique index. The most important
--      object in this file; read its rationale before changing anything.
--   4. user_subscriptions — renewal_attempt_at / renewal_failed_count, plus the
--      index the selection query needs.
--   5. plans.billing_cycle — domain widened, pro/max flipped to recurring_30d.
--   6. user_subscriptions_live — deliberately NOT recreated. Reasons in §6.

BEGIN;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. payment_methods — the stored credential, and the consent that makes it
--    chargeable
-- ════════════════════════════════════════════════════════════════════════════
--
-- WHAT IS AND IS NOT IN THIS TABLE ────────────────────────────────────────────
-- In: a provider token, four display fields the provider hands back, and the
-- consent artefact. Out: the PAN, the CVV, the cardholder name, and any expiry
-- the USER typed. We are not a card vault and must never become one by
-- accident — the CHECK constraints below exist so that "by accident" fails
-- loudly at INSERT instead of quietly succeeding.
--
-- WHY consent_given_at / consent_text_hash ARE NOT NULL ───────────────────────
-- A token with no consent is not a payment method — it is a credential we hold
-- with no right to use. Making the columns nullable would put that judgement in
-- the renewal job, i.e. in Python, i.e. one `if` away from being skipped. NOT
-- NULL puts it in the table: an unconsented token cannot be stored at all, so
-- there is no state in which the job has to be trusted to notice.
--
-- Neither column gets a DEFAULT, and consent_given_at deliberately does NOT
-- default to now(). A default would let a caller that forgot to pass consent
-- manufacture it — the schema would then be recording, in good faith, a consent
-- moment that never happened. That is worse than no column at all.
--
-- ⚠ DELETE-ACCOUNT TRAP (plan §10) ────────────────────────────────────────────
-- user_id is ON DELETE CASCADE, so this row vanishes with the account. That is
-- the PDPL-correct default — but it means the purge path MUST revoke the token
-- AT THE PROVIDER **BEFORE** the cascade runs. After the cascade there is no
-- record that the token ever existed, and a live token on a deleted account is
-- the worst version of this bug. See backend/app/services/account_purge_service.py.

CREATE TABLE IF NOT EXISTS public.payment_methods (
    payment_method_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    provider          text NOT NULL DEFAULT 'moyasar',
    provider_token    text NOT NULL,
    -- Display only. Enough to render «مدى ••1234» in إعدادات الحساب and in a
    -- dunning email, and nothing more.
    brand             text,
    last4             text,
    exp_month         integer,
    exp_year          integer,
    -- The consent artefact. Both NOT NULL — see above.
    consent_given_at  timestamptz NOT NULL,
    consent_text_hash text        NOT NULL,
    revoked_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Constraints added separately from CREATE TABLE: if the table already exists
-- (a hand-applied hotfix, a re-run after a partial apply) the CREATE is skipped
-- whole, constraints included — the same trap 113 documented for ADD COLUMN
-- IF NOT EXISTS. DROP-then-ADD so a re-run converges on exactly this domain.

ALTER TABLE public.payment_methods
    DROP CONSTRAINT IF EXISTS payment_methods_last4_check,
    DROP CONSTRAINT IF EXISTS payment_methods_token_not_pan_check,
    DROP CONSTRAINT IF EXISTS payment_methods_exp_month_check,
    DROP CONSTRAINT IF EXISTS payment_methods_exp_year_check,
    DROP CONSTRAINT IF EXISTS payment_methods_consent_hash_check;

ALTER TABLE public.payment_methods
    -- Exactly four digits or nothing. A PAN pasted into last4 fails here.
    ADD CONSTRAINT payment_methods_last4_check
        CHECK (last4 IS NULL OR last4 ~ '^[0-9]{4}$'),
    -- TRIPWIRE, not PCI compliance: a bare 12–19 digit string in provider_token
    -- is a card number, not a token. Moyasar tokens are prefixed alphanumerics.
    -- This cannot stop a determined mistake; it stops the careless one, which is
    -- the one that actually happens.
    ADD CONSTRAINT payment_methods_token_not_pan_check
        CHECK (length(provider_token) > 0 AND provider_token !~ '^[0-9]{12,19}$'),
    ADD CONSTRAINT payment_methods_exp_month_check
        CHECK (exp_month IS NULL OR exp_month BETWEEN 1 AND 12),
    ADD CONSTRAINT payment_methods_exp_year_check
        CHECK (exp_year IS NULL OR exp_year BETWEEN 2000 AND 2100),
    -- Lowercase hex sha256, i.e. exactly what
    --   hashlib.sha256(disclosure.encode("utf-8")).hexdigest()
    -- returns. The constraint is here so the column cannot quietly become "the
    -- disclosure text itself" or "whatever the frontend sent" — a consent record
    -- you cannot verify is not a consent record. If the backend author changes
    -- the digest, change this CHECK in the same migration.
    ADD CONSTRAINT payment_methods_consent_hash_check
        CHECK (consent_text_hash ~ '^[0-9a-f]{64}$');

COMMENT ON TABLE public.payment_methods IS
    'Stored payment credential for auto-renewal (132) — a PROVIDER TOKEN plus '
    'the consent that makes it chargeable. Never a PAN, never a CVV. RLS '
    'enabled with ZERO policies = deny-all for anon and authenticated; '
    'service_role bypasses RLS, so the backend is unaffected (the 118 lockdown '
    'posture, same as subscription_cancellations/120 and library_unlocks/104). '
    'There is NO user-facing SELECT of provider_token, ever — the settings '
    'surface returns brand/last4 through FastAPI. Do NOT add a policy here: on '
    'this table a policy could only widen access to a payment credential. '
    'DELETE-ACCOUNT TRAP: user_id is ON DELETE CASCADE, so the purge path must '
    'revoke the token AT THE PROVIDER before the cascade removes the only record '
    'that it exists.';

COMMENT ON COLUMN public.payment_methods.provider_token IS
    'The provider''s reusable token (Moyasar). This is the credential — treat '
    'every read of it as a money-moving operation. Not exposed to any client '
    'role by grant or by policy.';
COMMENT ON COLUMN public.payment_methods.brand IS
    'Display only, as returned by the provider (e.g. mada / visa). Never used '
    'to decide anything — network rules for MIT are a provider concern.';
COMMENT ON COLUMN public.payment_methods.last4 IS
    'Display only, exactly four digits (CHECK). The whole user-facing identity '
    'of a stored card: «مدى ••1234».';
COMMENT ON COLUMN public.payment_methods.consent_given_at IS
    'When the user ticked the recurring disclosure at purchase (Phase 6). NOT '
    'NULL and deliberately WITHOUT a default: a default would let a caller that '
    'never collected consent manufacture it. A row cannot exist without this, so '
    'the renewal job never has to check for it — the check is the schema.';
COMMENT ON COLUMN public.payment_methods.consent_text_hash IS
    'sha256 (lowercase hex, CHECK-enforced) of the EXACT Arabic disclosure the '
    'user was shown. When the wording changes you can still prove what a given '
    'user agreed to. Cheap now, impossible to reconstruct later — this is the '
    'artefact a chargeback is defended with.';
COMMENT ON COLUMN public.payment_methods.revoked_at IS
    'Set when the credential stops being usable: the user removed or replaced '
    'the card, the provider rejected it terminally, or the subscription was '
    'cancelled. NULL = active, and exactly one such row may exist per user '
    '(uniq_payment_method_active_per_user). Stamping this locally is NOT the '
    'same as revoking at the provider — do both.';

-- ── 1a. One active method per user ───────────────────────────────────────────
-- "Active" is revoked_at IS NULL. Replacing a card is therefore: stamp
-- revoked_at on the old row, insert the new one — never an in-place token
-- overwrite, so the consent record of the previous card survives.
--
-- This index is also the DOUBLE-WRITE GUARD for Phase 3: the token is persisted
-- from BOTH the /verify path and the webhook (plan §6), and only one of them can
-- win. The backend should not race and catch 23505 — it should name this index
-- as the conflict target:
--
--     INSERT INTO payment_methods (...) VALUES (...)
--     ON CONFLICT (user_id) WHERE revoked_at IS NULL DO UPDATE
--        SET provider_token = EXCLUDED.provider_token, ... , updated_at = now();
--
-- (PostgreSQL infers a partial unique index from an index predicate given in the
-- ON CONFLICT clause, so this works and needs no named constraint.)

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_method_active_per_user
    ON public.payment_methods (user_id)
    WHERE revoked_at IS NULL;

COMMENT ON INDEX public.uniq_payment_method_active_per_user IS
    'At most one usable stored credential per user (132). Also the idempotency '
    'target for the Phase 3 double-write (/verify + webhook both persist the '
    'token): ON CONFLICT (user_id) WHERE revoked_at IS NULL DO UPDATE.';

-- FK index (project rule): supports the ON DELETE CASCADE scan from users and
-- the "this user's methods, newest first" operator read. The partial unique
-- index above only covers the active row.
CREATE INDEX IF NOT EXISTS idx_payment_methods_user
    ON public.payment_methods (user_id, created_at DESC);

-- ── 1b. Lockdown (118 posture) ───────────────────────────────────────────────
-- RLS on with ZERO policies. 118 §6 already cleared the blanket default grant
-- for anon/authenticated on tables created by `postgres` in this schema, so a
-- table created today should be born closed — but the REVOKEs are explicit
-- anyway, because "born closed" is a default someone has already edited once and
-- H-2 (cases_content_backup) is exactly what happens when you rely on it.

ALTER TABLE public.payment_methods ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.payment_methods FROM anon;
REVOKE ALL ON TABLE public.payment_methods FROM authenticated;
GRANT  ALL ON TABLE public.payment_methods TO service_role;

-- ── 1c. updated_at ───────────────────────────────────────────────────────────
-- Reuses public.update_updated_at() (014, still used by blog_posts/070 and
-- blog_post_jobs/086). Guarded rather than assumed: this session could not read
-- pg_proc, and a missing function must degrade to a WARNING, not abort a
-- migration whose real payload is the money-path constraints below.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = 'update_updated_at'
    ) THEN
        DROP TRIGGER IF EXISTS trg_payment_methods_updated_at ON public.payment_methods;
        CREATE TRIGGER trg_payment_methods_updated_at
            BEFORE UPDATE ON public.payment_methods
            FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
    ELSE
        RAISE WARNING
            '132: public.update_updated_at() not found — payment_methods.updated_at '
            'will NOT auto-bump; the backend must set it on every UPDATE.';
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 2. payment_transactions — telling a renewal apart from a purchase
-- ════════════════════════════════════════════════════════════════════════════
--
-- Columns bare first, constraints after: ADD COLUMN IF NOT EXISTS skips the
-- WHOLE clause when the column already exists, constraint included (113's note).
-- Writing them inline would mean a re-run, or a hand-applied hotfix, silently
-- leaves the table unconstrained — which on this table is the difference between
-- "the guard in §3 holds" and "the guard in §3 is decorative".

ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS initiated_by      text        NOT NULL DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS renewal_attempt   integer     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS payment_method_id uuid,
    ADD COLUMN IF NOT EXISTS period_start      timestamptz;

ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_initiated_by_check;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_initiated_by_check
    CHECK (initiated_by IN ('user', 'renewal'));

ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_renewal_attempt_check;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_renewal_attempt_check
    CHECK (renewal_attempt >= 0
           AND (initiated_by = 'renewal' OR renewal_attempt = 0));

-- The biconditional that makes §3's guard total. See §3 for why this, and not
-- the index alone, is what closes the double-charge hole.
ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_renewal_period_check;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_renewal_period_check
    CHECK ((initiated_by = 'renewal') = (period_start IS NOT NULL));

-- ⚠ ON DELETE **SET NULL**, NOT CASCADE — AND NOT THE DEFAULT EITHER ──────────
-- The instruction is "no ON DELETE CASCADE; payment rows outlive everything
-- (117)". Leaving the action unspecified would give NO ACTION, and NO ACTION is
-- not neutral here — it is RESTRICT with a delay, and it BREAKS ACCOUNT
-- DELETION:
--
--     auth.admin.delete_user
--       → auth.users deleted
--       → public.users deleted (cascade)
--       → payment_methods row deleted (cascade, §1)
--       → but payment_transactions still references it  → 23503, purge aborts.
--
-- SET NULL satisfies both requirements at once: the financial row is never
-- deleted (117's whole point — receipt_no must stay hole-free), and the
-- credential row is free to go with the account. It is also literally the
-- posture 117 chose for user_id on this same table, and 090 chose for
-- plan_codes.redeemed_by: the record outlives the thing it points at.
--
-- What is lost on purge is "which card paid this" — acceptable, and the same
-- trade 117 already made for "which user paid this". If a retained row ever
-- needs to name the instrument, snapshot brand/last4 ONTO the payment row the
-- way 117 snapshots the customer; do NOT weaken this FK to keep the pointer.
ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_payment_method_id_fkey;
ALTER TABLE public.payment_transactions
    ADD  CONSTRAINT payment_transactions_payment_method_id_fkey
    FOREIGN KEY (payment_method_id) REFERENCES public.payment_methods(payment_method_id)
    ON DELETE SET NULL;

COMMENT ON COLUMN public.payment_transactions.initiated_by IS
    'Who started this charge (132): ''user'' = a browser checkout the cardholder '
    'was present for (the default, and the value every pre-132 row carries); '
    '''renewal'' = a merchant-initiated charge against a stored token with '
    'nobody present. The distinction is not cosmetic — it selects a different '
    'consent basis, a different refund conversation, and a different idempotency '
    'rule (uniq_payment_renewal_period). '
    '⚠ _expire_open_checkouts (payment_service.py) supersedes a user''s open '
    '`initiated` rows on every new checkout; a renewal row is `initiated` too, '
    'so that sweep MUST filter initiated_by <> ''renewal'' or a user who opens '
    '/pay during a renewal window kills their own renewal.';
COMMENT ON COLUMN public.payment_transactions.renewal_attempt IS
    'Dunning ladder position (132): 0 = first try, 1..n = retries (plan §8: day '
    '0, +1, +3). Each attempt is its own row. CHECK-pinned to 0 for '
    'initiated_by = ''user'' — a purchase the cardholder made is never a retry.';
COMMENT ON COLUMN public.payment_transactions.payment_method_id IS
    'Which stored credential was charged (132). NULL for browser-form purchases, '
    'and NULL again after the account is purged (ON DELETE SET NULL — a payment '
    'row is never deleted, see 117). Never a substitute for the token itself: '
    'this is a FK, readable by the owning user under '
    'payment_transactions_select_self, and it leaks nothing.';
COMMENT ON COLUMN public.payment_transactions.period_start IS
    'THE RENEWAL PERIOD KEY (132). For initiated_by = ''renewal'': the instant '
    'the term being paid for begins — i.e. user_subscriptions.expires_at AS IT '
    'STOOD when the job selected this user, which is also the boundary the new '
    'term is extended FROM (plan §7 step 3: extend from expires_at, never from '
    'now()). NULL for user-initiated purchases, and CHECK-enforced both ways: a '
    'renewal without a period is impossible, and only a renewal may carry one. '
    'This column exists solely so uniq_payment_renewal_period can exist — read '
    'that index''s comment before changing how it is computed.';

-- FK index (project rule) — also what the ON DELETE SET NULL scan walks when a
-- payment_methods row is removed.
CREATE INDEX IF NOT EXISTS idx_payment_transactions_method
    ON public.payment_transactions (payment_method_id)
    WHERE payment_method_id IS NOT NULL;

-- Operator surface + the dunning read: "this user's renewal charges, newest
-- first". Mirrors 119's idx_payment_open_checkouts in shape and intent.
CREATE INDEX IF NOT EXISTS idx_payment_renewals
    ON public.payment_transactions (user_id, created_at DESC)
    WHERE initiated_by = 'renewal';


-- ════════════════════════════════════════════════════════════════════════════
-- 3. ⚠ THE IDEMPOTENCY GUARD — a second charge for the same period cannot exist
-- ════════════════════════════════════════════════════════════════════════════
--
-- This is the most important object in the file. Everything else here is
-- bookkeeping; this is the thing that stands between a redeploy mid-run and
-- charging a customer twice.
--
-- WHY IT IS IN THE DATABASE AND NOT IN PYTHON ─────────────────────────────────
-- Plan §11.5: every scheduled job in main.py rests on the backend running ONE
-- worker. That assumption is invisible, unenforced, and one Railway slider away
-- from being false — and this job moves money. A Python "check then insert" is
-- not a guard under any concurrency at all; it is a comment with a race in it.
--
-- WHAT THE KEY IS, AND WHY period_start HAD TO BE ADDED ───────────────────────
-- The plan suggests (user_id, plan_id, period_start). payment_transactions had
-- no period_start, so the choice was: add the column, or key on something
-- already present. Everything already present was rejected, and for the record:
--
--   * created_at        — not stable; two ticks produce two values. Useless.
--   * provider_ref      — already UNIQUE (092), but it is assigned BY the
--                         provider AFTER the charge. It cannot deduplicate the
--                         insert that precedes the charge, which is the only
--                         insert that matters (crash-safe ordering, plan §7.1).
--   * prior_expires_at  — the only existing timestamp with the right shape, but
--                         it means "the subscription this grant REPLACED" (113)
--                         and is stamped at GRANT time by
--                         stamp_payment_prior_snapshot, not at insert time.
--                         Overloading it would make a refund restore the wrong
--                         term. Rejected outright.
--   * renewal_attempt   — a counter, not an identity.
--
-- So the column was added. period_start = the subscription's expires_at at
-- selection time, which is the honest name for "the period this money buys" and
-- is computed identically by every concurrent tick (they all read the same
-- committed row). Two ticks therefore collide on the same key and exactly one
-- survives; the loser raises 23505 and logs, which is the loud failure the plan
-- asked for. After a successful renewal expires_at moves 30 days forward, so the
-- NEXT cycle's key is naturally different and nothing is blocked.
--
-- WHY THE PREDICATE IS `status <> 'failed'` AND NOT `status IN (...)` ─────────
-- A renewal slot for a period is released by exactly ONE event: a definitive
-- decline. That is what makes the dunning ladder (plan §8 — attempt 0, +1, +3,
-- each its own row) representable while a double-charge is not:
--
--   initiated → occupies the slot. A second tick cannot insert. ✅
--   paid      → occupies the slot forever. The period is bought; nothing may
--               charge for it again. ✅
--   refunded  → still occupies it. A refunded renewal must NOT be silently
--               re-charged by tomorrow's tick. ✅
--   expired   → still occupies it. This is the load-bearing one: 119's
--               _expire_open_checkouts sweeps `initiated` rows with no
--               initiated_by filter today. If it ever expires a renewal row, the
--               damage is a renewal that did not happen — NOT a renewal that
--               happened twice. The index protects the money even when the
--               sweep is wrong. (Fix the sweep too; see initiated_by's comment.)
--   failed    → drops out of the index. Only now may attempt N+1 be inserted. ✅
--
-- The predicate is mutable (status changes), which is fine and intentional:
-- rows enter and leave the index as they transition, and an UPDATE that would
-- move a row back INTO an occupied slot fails with 23505 — again, loudly.
--
-- WHY THE CHECK CONSTRAINT IN §2 IS PART OF THIS GUARD ────────────────────────
-- A partial index is only as total as its predicate. `period_start IS NOT NULL`
-- inside the predicate means a renewal row inserted WITHOUT a period_start would
-- simply not be indexed — and unlimited such rows could coexist. That is not a
-- theoretical hole; it is one forgotten field in one INSERT. So
-- payment_transactions_renewal_period_check (§2) makes it impossible:
--   (initiated_by = 'renewal') = (period_start IS NOT NULL).
-- The index and that CHECK are one mechanism. Do not remove either alone.
--
-- user_id IS NOT NULL is explicit for 119's reason: 117 nulls it on a purged
-- account, several NULLs never conflict in a unique index, and relying on that
-- silently would make the invariant read stronger than it is. (A purged account
-- has no subscription to renew, so nothing is lost.)
--
-- NOT CONSTRAINED, DELIBERATELY: initiated_by = 'user'. Repeat purchases are a
-- FEATURE — same-plan buys STACK (grant_plan, 092) — and 119 already governs the
-- only user-side abuse (one open CREDITED quote per user).

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_renewal_period
    ON public.payment_transactions (user_id, plan_id, period_start)
    WHERE initiated_by = 'renewal'
      AND status <> 'failed'
      AND user_id IS NOT NULL
      AND period_start IS NOT NULL;

COMMENT ON INDEX public.uniq_payment_renewal_period IS
    'THE double-charge guard (132). At most ONE non-failed renewal charge per '
    '(user, plan, period_start), where period_start is the subscription''s '
    'expires_at at selection time. Makes two scheduler ticks, a mid-run '
    'redeploy, or a scaled-out backend physically unable to charge the same '
    'period twice: the second INSERT raises 23505 instead of the second charge '
    'succeeding quietly. Only status = ''failed'' releases the slot, so the '
    'dunning ladder can retry a decline while paid/refunded/expired rows keep '
    'the period closed. Depends on '
    'payment_transactions_renewal_period_check to be total — a renewal row with '
    'a NULL period_start would fall out of this index entirely.';


-- ════════════════════════════════════════════════════════════════════════════
-- 4. user_subscriptions — dunning bookkeeping
-- ════════════════════════════════════════════════════════════════════════════
--
-- ⚠ TO THE BACKEND AUTHOR: WRITE THESE TWO COLUMNS ALONE ──────────────────────
-- trg_user_subscriptions_assignment is BEFORE UPDATE **OF plan_id** (079;
-- verified live 2026-08-08 for 120 — re-verify per the header) and its body
-- re-derives expires_at from plans.duration_days whenever plan_id changes. An
-- UPDATE that sets renewal_attempt_at AND touches plan_id would therefore
-- silently re-stamp the term — the same "set expiry ALONE" trap that shaped 120
-- and 131. Two consequences, both mandatory:
--
--   * the dunning write is its own statement:
--       UPDATE user_subscriptions
--          SET renewal_attempt_at = <attempt time>,
--              renewal_failed_count = renewal_failed_count + 1,
--              updated_at = now()
--        WHERE user_id = ...;
--     (updated_at by hand — a statement that touches neither plan_id nor
--     expires_at does not fire the trigger at all, so nothing else sets it.)
--
--   * the SUCCESS write must NOT be hand-written here. ⚠ CORRECTED 2026-08-11,
--     after the backend was built against this comment — the hand-written form
--     below is WRONG and is kept only so nobody re-derives it:
--
--       -- DO NOT USE:
--       -- UPDATE user_subscriptions
--       --    SET expires_at = expires_at + make_interval(days => <n>), ...
--
--     It extends the term without stamping payment_transactions.fulfilled_at,
--     which grant_plan is the only thing that sets. revoke_plan_grant branches
--     on that stamp and answers 'not_fulfilled' — "paid but never granted,
--     nothing to take back" — so a REFUNDED RENEWAL WOULD RETURN THE MONEY AND
--     LEAVE THE 30 DAYS STANDING. That silently breaks the refund requirement
--     in auto_renewal plan §10.
--
--     Call ``grant_plan`` instead: identical term arithmetic (it extends from
--     the old expires_at, so renewing at 03:30 does not shave hours off the
--     cycle) AND it stamps fulfilled_at. Write renewal_attempt_at /
--     renewal_failed_count = 0 as their own statement afterwards, per the
--     trigger rule above.

ALTER TABLE public.user_subscriptions
    ADD COLUMN IF NOT EXISTS renewal_attempt_at   timestamptz,
    ADD COLUMN IF NOT EXISTS renewal_failed_count integer NOT NULL DEFAULT 0;

ALTER TABLE public.user_subscriptions
    DROP CONSTRAINT IF EXISTS user_subscriptions_renewal_failed_count_check;
ALTER TABLE public.user_subscriptions
    ADD  CONSTRAINT user_subscriptions_renewal_failed_count_check
    CHECK (renewal_failed_count >= 0);

COMMENT ON COLUMN public.user_subscriptions.renewal_attempt_at IS
    'When the renewal job last attempted a charge for this subscription (132). '
    'NULL = never attempted, which is the state of every row before the engine '
    'ships. Together with renewal_failed_count this is the whole dunning state — '
    'deliberately two plain columns and NOT a status enum, for the reason 120 '
    'recorded: the quota gate must not learn a fourth answer it would then have '
    'to enforce. Access during the retry window is governed by expires_at exactly '
    'as it always was. WRITE IT ALONE — see the block above.';
COMMENT ON COLUMN public.user_subscriptions.renewal_failed_count IS
    'Consecutive failed renewal attempts (132). 0 = healthy. Incremented per '
    'declined attempt, RESET TO 0 on any successful renewal or new paid grant — '
    'if it is not reset, a user who fails once and then pays is permanently one '
    'step up the dunning ladder. Drives the retry ladder (plan §8: 0, +1, +3, '
    'then let the term lapse into the existing expired→free fallback). '
    'WRITE IT ALONE — see the block above.';

-- ── 4a. The selection index ──────────────────────────────────────────────────
-- The job's hard gates (plan §7) are: source = 'payment', renewal_cancelled_at
-- IS NULL, and expires_at within the next 24 hours. The first two are immutable
-- predicates and go INTO the index, which keeps it tiny — the vast majority of
-- rows are free/code/signup and never enter it. expires_at is the ordered key
-- the BETWEEN scans.
--
-- plan_id is deliberately NOT in the predicate even though the gate also filters
-- pro/max: hard-coding plan ids into an index is exactly the drift that §5's
-- billing_cycle flip is meant to end. Let the query filter on billing_cycle.

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_renewal_due
    ON public.user_subscriptions (expires_at)
    WHERE source = 'payment' AND renewal_cancelled_at IS NULL;

COMMENT ON INDEX public.idx_user_subscriptions_renewal_due IS
    'Serves the renewal job''s selection scan (132): source = ''payment'' AND '
    'renewal_cancelled_at IS NULL AND expires_at BETWEEN now() AND now() + 24h. '
    'The two immutable gates are in the predicate so the index holds only rows '
    'that could ever be charged — code grants, marketing grants, signup grants '
    'and opted-out users are not merely filtered, they are absent.';


-- ════════════════════════════════════════════════════════════════════════════
-- 5. plans.billing_cycle — the column stops being decorative
-- ════════════════════════════════════════════════════════════════════════════
--
-- ⚠ READ BEFORE RUNNING. This is the ONE section with a semantic effect the day
--   it is applied. 113 set all three paid plans to `one_time` with the note that
--   «a billing_cycle that lies is the exact bug 091 was written to kill», and
--   flipping pro/max here asserts they renew BEFORE the engine that renews them
--   exists. Two things make that acceptable rather than a fresh lie:
--
--     * nothing branches on the column today — payment_service.py:595 SELECTs it
--       and never reads it (plan §2), so the flip changes no behaviour;
--     * /terms §5.2 has publicly said pro/max auto-renew since 2026-08-10. The
--       schema is currently the one place that contradicts the live contract,
--       not the one place that would start over-promising.
--
--   Still: the honest window is small. Apply 132 shortly before the Phase 3–6
--   deploy, not months ahead of it. If it must be applied early, run §1–§4 and
--   hold §5 back — the sections are independent.
--
-- WHY THE DOMAIN IS WIDENED, NOT REPLACED ─────────────────────────────────────
-- 092 created the column as CHECK (billing_cycle IN ('one_time',
-- 'recurring_monthly')), so a bare UPDATE to 'recurring_30d' would 23514. The
-- constraint is discovered from pg_constraint rather than dropped by its assumed
-- auto-generated name: this session could not read the live catalog, and a
-- DROP CONSTRAINT IF EXISTS against the wrong name is the worst outcome — it
-- succeeds silently, the ADD below succeeds too, and the OLD narrow constraint
-- keeps rejecting the value.
--
-- 'recurring_monthly' is KEPT in the domain. It is legacy (092) and nothing
-- should be set to it again, but the live catalog could not be read and a plan
-- row still holding it would make ADD CONSTRAINT fail and roll back the whole
-- migration. Tightening it is a one-line follow-up once prod is confirmed clean.
--
-- WHY 'recurring_30d' AND NOT 'recurring_monthly' ─────────────────────────────
-- The term is 30 days, anchored on the purchase, not a calendar month. 076/079
-- already made every term a duration_days count; naming the cycle "monthly"
-- invites someone to bill on the 1st and shave two days off February.

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_class     c ON c.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname = 'plans'
           AND con.contype = 'c'
           AND pg_get_constraintdef(con.oid) ILIKE '%billing_cycle%'
    LOOP
        EXECUTE format('ALTER TABLE public.plans DROP CONSTRAINT %I', r.conname);
        RAISE NOTICE '132: dropped billing_cycle CHECK %', r.conname;
    END LOOP;
END $$;

ALTER TABLE public.plans
    ADD CONSTRAINT plans_billing_cycle_check
    CHECK (billing_cycle IS NULL
           OR billing_cycle IN ('one_time', 'recurring_30d', 'recurring_monthly'));

-- Value-stable: the WHERE makes a re-run a genuine no-op rather than a churn of
-- updated_at (which the backend's 5-minute plan cache reads).
UPDATE public.plans
   SET billing_cycle = 'recurring_30d',
       updated_at    = now()
 WHERE plan_id IN ('pro', 'max')
   AND billing_cycle IS DISTINCT FROM 'recurring_30d';

-- basic is stated, not assumed. «بدون تجديد تلقائي · فترة الاشتراك ٧ أيام فقط»
-- is printed on its card permanently (plan §9), and this row is what that
-- sentence is true because of.
UPDATE public.plans
   SET billing_cycle = 'one_time',
       updated_at    = now()
 WHERE plan_id = 'basic'
   AND billing_cycle IS DISTINCT FROM 'one_time';

COMMENT ON COLUMN public.plans.billing_cycle IS
    'one_time = pay once, access lasts duration_days, no further charge (basic, '
    'permanently — its card says so). recurring_30d (132) = the renewal job may '
    'charge a stored token every duration_days (pro, max), which /terms §5.2 has '
    'promised publicly since 2026-08-10. NULL = not purchasable (free, dev, '
    'marketing_*). recurring_monthly is LEGACY from 092, kept in the CHECK domain '
    'only so a stale row cannot break a migration — never set it. '
    '⚠ THIS COLUMN MUST BE READ, NOT JUST WRITTEN (plan §11.6): it has been '
    'decorative since 076. The renewal job''s plan gate should be '
    'billing_cycle = ''recurring_30d'', NOT plan_id IN (''pro'',''max'') — that '
    'is what stops the third copy of the ladder from drifting, and it is why '
    'idx_user_subscriptions_renewal_due deliberately omits plan_id.';


-- ════════════════════════════════════════════════════════════════════════════
-- 6. user_subscriptions_live — DELIBERATELY NOT RECREATED
-- ════════════════════════════════════════════════════════════════════════════
-- No DDL in this section. The decision and its reasons, so the next author does
-- not have to re-derive them:
--
--   1. Nothing needs to surface yet. renewal_attempt_at and renewal_failed_count
--      describe DUNNING, and dunning does not exist until Phase 5. Until the job
--      writes them, every row would read NULL / 0 — an operator column that can
--      only say "nothing has happened" is noise on a view that is already 33
--      columns wide.
--   2. Rebuilding this view REQUIRES reading its live definition first, and this
--      session had no MCP access. 120's header and 129's header BOTH carry the
--      same warning, in the same words, because it has already gone wrong once:
--      reconstructing it from an older migration file silently drops operator
--      columns. 131 declined to touch it for exactly this reason and said so.
--      A view rebuilt blind is a worse outcome than two columns an operator has
--      to SELECT by hand for one more phase.
--   3. There is a SUSPECTED LIVE REGRESSION here that must be settled with a
--      live read, NOT smuggled into this file: 120 created the view
--      `WITH (security_invoker = true)`; 129 dropped and recreated it WITHOUT
--      that option. If prod now has a security-definer view, that is a change of
--      security posture on a view that joins users to subscriptions, and it
--      deserves its own migration and its own verification — not a silent
--      re-add inside an auto-renewal change. Check it:
--
--        SELECT c.reloptions
--          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--         WHERE n.nspname='public' AND c.relname='user_subscriptions_live';
--        -- EXPECT {security_invoker=true}. If NULL, 129 regressed it.
--
-- THE RECIPE FOR PHASE 5, when the columns are worth surfacing:
--   a. SELECT pg_get_viewdef('public.user_subscriptions_live'::regclass, true);
--      — start from THAT text, never from this directory.
--   b. DROP VIEW + CREATE VIEW WITH (security_invoker = true), adding
--      s.renewal_attempt_at and s.renewal_failed_count beside
--      s.renewal_cancelled_at (they are read together: "opted out / last tried /
--      how many declines").
--   c. Re-grant: a recreated view does NOT inherit the old ACL.
--        REVOKE SELECT ... FROM anon, authenticated;
--        GRANT  SELECT ... TO service_role;
--   d. Count the columns before and after and assert +2. 120's check 4 is the
--      template.


COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY VERIFICATION — run manually; every check must PASS
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. payment_methods is deny-all for client roles. EXPECT: ZERO ROWS.
-- SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--  WHERE table_schema='public' AND table_name='payment_methods'
--    AND grantee IN ('anon','authenticated');
--
-- -- 2. RLS on, zero policies (a policy here could only widen access to a
-- --    payment credential). EXPECT: relrowsecurity = true, n_policies = 0.
-- SELECT c.relrowsecurity,
--        (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS n_policies
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname='public' AND c.relname='payment_methods';
--
-- -- 3. The consent columns really are NOT NULL and really have no default.
-- --    EXPECT: two rows, is_nullable='NO', column_default IS NULL.
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_schema='public' AND table_name='payment_methods'
--    AND column_name IN ('consent_given_at','consent_text_hash');
--
-- -- 4. ⚠ THE FK ACTION. confdeltype must be 'n' (SET NULL). 'c' = CASCADE would
-- --    delete financial records; 'a' = NO ACTION would make account deletion
-- --    fail with 23503. EXPECT: exactly 'n'.
-- SELECT con.conname, con.confdeltype
--   FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
--  WHERE c.relname='payment_transactions'
--    AND con.conname='payment_transactions_payment_method_id_fkey';
--
-- -- 5. Every new constraint and index landed. EXPECT: 4 constraints, 4 indexes.
-- SELECT conname FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
--  WHERE c.relname IN ('payment_transactions','user_subscriptions')
--    AND conname IN ('payment_transactions_initiated_by_check',
--                    'payment_transactions_renewal_attempt_check',
--                    'payment_transactions_renewal_period_check',
--                    'user_subscriptions_renewal_failed_count_check');
-- SELECT indexname FROM pg_indexes
--  WHERE schemaname='public'
--    AND indexname IN ('uniq_payment_method_active_per_user',
--                      'uniq_payment_renewal_period',
--                      'idx_payment_renewals',
--                      'idx_user_subscriptions_renewal_due');
--
-- -- 6. Nothing was retro-labelled a renewal. EXPECT: initiated_by='user' for
-- --    every existing row, n_periods = 0.
-- SELECT initiated_by, count(*), count(period_start) AS n_periods
--   FROM public.payment_transactions GROUP BY 1;
--
-- -- 7. The catalog. EXPECT: basic=one_time, pro=recurring_30d, max=recurring_30d,
-- --    everything else NULL, and NOTHING on 'recurring_monthly'.
-- SELECT plan_id, price_sar, duration_days, billing_cycle
--   FROM public.plans ORDER BY price_sar NULLS LAST;
--
-- -- 8. ⚠ PROVE THE GUARD FIRES. Substitute a real user_id, then run the whole
-- --    block — the SECOND insert must raise 23505 and the ROLLBACK must leave
-- --    prod untouched. A guard nobody has watched fail is not a guard.
-- -- BEGIN;
-- --   INSERT INTO public.payment_transactions
-- --       (user_id, plan_id, amount_sar, status, initiated_by, period_start)
-- --   VALUES ('<user_id>','pro', 89.90, 'initiated', 'renewal', '2030-01-01 00:00:00+00');
-- --   -- same user, same plan, same period → EXPECT 23505 on this line:
-- --   INSERT INTO public.payment_transactions
-- --       (user_id, plan_id, amount_sar, status, initiated_by, period_start)
-- --   VALUES ('<user_id>','pro', 89.90, 'initiated', 'renewal', '2030-01-01 00:00:00+00');
-- -- ROLLBACK;
--
-- -- 9. ⚠ PROVE THE CHECK CLOSES THE INDEX'S BLIND SPOT. EXPECT 23514, because a
-- --    renewal with a NULL period would otherwise sit outside the guard.
-- -- BEGIN;
-- --   INSERT INTO public.payment_transactions
-- --       (user_id, plan_id, amount_sar, status, initiated_by)
-- --   VALUES ('<user_id>','pro', 89.90, 'initiated', 'renewal');
-- -- ROLLBACK;
--
-- -- 10. ⚠ PROVE A FAILED ATTEMPT RELEASES THE SLOT (the dunning ladder must work).
-- --     EXPECT: insert 1 ok, insert 2 raises 23505, then after marking failed,
-- --     insert 3 (renewal_attempt = 1) succeeds.
-- -- BEGIN;
-- --   INSERT INTO public.payment_transactions
-- --       (user_id, plan_id, amount_sar, status, initiated_by, period_start, renewal_attempt)
-- --   VALUES ('<user_id>','pro', 89.90, 'initiated', 'renewal', '2030-01-01 00:00:00+00', 0);
-- --   UPDATE public.payment_transactions SET status='failed'
-- --    WHERE user_id='<user_id>' AND period_start='2030-01-01 00:00:00+00';
-- --   INSERT INTO public.payment_transactions
-- --       (user_id, plan_id, amount_sar, status, initiated_by, period_start, renewal_attempt)
-- --   VALUES ('<user_id>','pro', 89.90, 'initiated', 'renewal', '2030-01-01 00:00:00+00', 1);
-- -- ROLLBACK;
--
-- -- 11. Account deletion still works end to end. On a THROWAWAY account with a
-- --     payment_methods row and a payment_transactions row pointing at it, run
-- --     the purge path. EXPECT: users row gone, payment_methods row gone,
-- --     payment_transactions row PRESENT with user_id NULL and
-- --     payment_method_id NULL. If this 23503s, check 4 failed.
-- --
-- -- 12. Operator view untouched by this migration (§6). EXPECT: the same column
-- --     count as before 132 — 33 after 129.
-- SELECT count(*) FROM information_schema.columns
--  WHERE table_schema='public' AND table_name='user_subscriptions_live';
