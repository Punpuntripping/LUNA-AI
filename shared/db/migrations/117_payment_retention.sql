-- ════════════════════════════════════════════════════════════════════════════
-- 117 — financial-record retention: a purged account leaves its payments behind
-- ════════════════════════════════════════════════════════════════════════════
--
-- Depends on: 090 (deletion grace + purge_user_data), 092 (payment_transactions),
--             113 (refund/VAT/proration columns), 114 (sequential receipt_no).
-- Idempotent: ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF EXISTS + ADD /
--             NULL-guarded backfill. Re-runnable.
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- `payment_transactions.user_id` was NOT NULL with ON DELETE CASCADE. The daily
-- sweep in account_purge_service hard-deletes an account 30 days after the user
-- asks for it, and its terminal step (auth.admin.delete_user) cascades
-- auth.users -> public.users -> and, until now, straight through every payment
-- that user ever made. A subscription bought, then an account deleted, and 30
-- days later the sale never happened.
--
-- Two things make that unacceptable rather than merely untidy:
--
--   1. Saudi record-retention expects financial records kept for ~6 years. The
--      privacy policy already reserves exactly this (privacy-ar.md §5: «مع مراعاة
--      المدد التي قد يفرضها النظام للاحتفاظ ببعض السجلّات (كالسجلّات المالية
--      والأمنية)»), so retention here is the promise being kept, not broken.
--   2. receipt_no (114) is a SEQUENTIAL series off a dedicated sequence, kept
--      continuous from day one so receipts can become ZATCA invoices later
--      without a numbering break. Deleting rows punches holes in that series
--      with nothing underneath them — the single most challengeable artefact an
--      auditor can be handed. A gap you can explain is a row; a gap you cannot
--      is a missing sale.
--
-- The shape is ANONYMIZE AND RETAIN, not export-and-delete: the row stands, the
-- person is detached from it. `user_id` goes nullable and the FK flips to ON
-- DELETE SET NULL (the same posture 090 chose for plan_codes.redeemed_by and
-- audit_logs.user_id — the record outlives the account).
--
-- WHY THE SNAPSHOT COLUMNS ─────────────────────────────────────────────────────
-- SET NULL alone retains an amount with no one attached to it, which is not a
-- financial record — it is a number. Name and email are therefore captured ONTO
-- the payment row at purchase time (backend stamps them in create_checkout), so
-- a retained row can still answer "whose payment was this?" after the users row
-- is gone. Snapshot, not lookup: this is what was true when the money moved, and
-- a later profile edit must not rewrite a settled receipt — the same discipline
-- 113 applied to vat_amount_sar (stamped once, never recomputed at display time).
--
-- This is deliberately the MINIMUM identity a financial record needs. Nothing
-- else about the user is retained; purge_user_data still erases every
-- conversation, document, template, preference and case as before.
--
-- WHY purge_user_data IS NOT TOUCHED ───────────────────────────────────────────
-- SET NULL only saves these rows if nothing deletes them by explicit statement
-- first. The live definition was read before writing this file
-- (`select prosrc from pg_proc where proname = 'purge_user_data'`, 2026-08-07)
-- and it does NOT reference payment_transactions at all — it deletes
-- retrieval_artifacts, workspace_items, conversations, lawyer_cases,
-- message_feedback, pii_mappings, user_templates, user_preferences, blog_posts,
-- paused_runs and (dynamically) task_state, then scrubs plan_codes.
-- redeemed_by_users. Payments reached it only through the FK cascade, so
-- flipping the FK is the whole fix and the RPC needs no amendment. Per 113's
-- rule — additive beats destructive on the money path — it is left alone rather
-- than blind-replaced. Nothing in backend/ or shared/ issues a DELETE against
-- payment_transactions either (verified by grep), and the only trigger on the
-- table is 114's BEFORE UPDATE OF status receipt-number assigner.
--
-- RLS ─────────────────────────────────────────────────────────────────────────
-- The one policy on this table is payment_transactions_select_self, permissive
-- SELECT, `user_id IN (SELECT u.user_id FROM users u WHERE u.auth_id =
-- auth.uid())`. An anonymized row has user_id NULL, and `NULL IN (…)` is NULL,
-- never true — so a detached row is invisible to every authenticated caller and
-- readable only by the service role, which is exactly the intent. No policy
-- change is needed and none is made here.

-- ── 1. Customer identity, captured at purchase time ──────────────────────────

ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS customer_name_snapshot  text,
    ADD COLUMN IF NOT EXISTS customer_email_snapshot text;

COMMENT ON COLUMN public.payment_transactions.customer_name_snapshot IS
    'users.full_name_ar as it stood when this payment was initiated (117). '
    'Stamped once by create_checkout, never refreshed — a later profile edit '
    'must not rewrite a settled financial record. Survives account deletion, '
    'which is the entire point: it is what lets a retained row still name its '
    'customer after user_id has been nulled.';
COMMENT ON COLUMN public.payment_transactions.customer_email_snapshot IS
    'users.email at initiation (117), same stamp-once rule as the name. NOT the '
    'receipt recipient — receipt_service resolves that live off users, and only '
    'ever sends while the account still exists.';

-- Backfill: every row predating this migration gets the identity it should have
-- carried from the start. NULL-guarded on the name column so a re-run cannot
-- overwrite a stamped snapshot with a since-edited profile, and so rows whose
-- user is already gone (nothing to join to) are simply left NULL rather than
-- churned. Rows created after this migration are stamped by the backend and are
-- never seen by this statement.
UPDATE public.payment_transactions t
   SET customer_name_snapshot  = u.full_name_ar,
       customer_email_snapshot = u.email
  FROM public.users u
 WHERE u.user_id = t.user_id
   AND t.customer_name_snapshot IS NULL;

-- ── 2. The row outlives the account ──────────────────────────────────────────
-- Order matters: DROP NOT NULL first, or the FK below could never fire SET NULL.

ALTER TABLE public.payment_transactions
    ALTER COLUMN user_id DROP NOT NULL;

COMMENT ON COLUMN public.payment_transactions.user_id IS
    'Buyer, NULL once that account has been purged (117). NULL does NOT mean '
    '"anonymous purchase" — it means the customer exercised deletion and the '
    'financial record was retained without them, per privacy-ar.md §5. Use '
    'customer_name_snapshot / customer_email_snapshot to identify such a row. '
    'Every user-facing query filters on user_id, so a detached row is reachable '
    'by the service role only.';

-- FK flip — same posture 090 gave plan_codes.redeemed_by: the record outlives
-- the redeemer. DROP-then-ADD (090's own pattern) rather than a guarded ADD,
-- because the constraint already exists with the WRONG action and a guarded ADD
-- would silently leave CASCADE in place.
ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_user_id_fkey;
ALTER TABLE public.payment_transactions
    ADD CONSTRAINT payment_transactions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE SET NULL;
