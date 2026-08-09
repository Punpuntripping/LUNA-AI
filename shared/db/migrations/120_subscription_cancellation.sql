-- ════════════════════════════════════════════════════════════════════════════
-- 120 — subscription cancellation (إلغاء الاشتراك) + exit survey
-- ════════════════════════════════════════════════════════════════════════════
--
-- Plan: .claude/plans/subscription_cancellation.md §2.
-- Depends on: 091 (status dropped, handle_subscription_assignment), 093 (the
--             quota-state RPC + the operator view), 105 (the view's CURRENT
--             column list — see the drift note below), 092 (user_subscriptions).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS,
--             CREATE INDEX IF NOT EXISTS, DROP VIEW IF EXISTS + CREATE.
--             Re-runnable.
--
-- ⚠ APPLY THIS BEFORE DEPLOYING THE BACKEND. The backend writes
--   `renewal_cancelled_at` and inserts into `subscription_cancellations`; a
--   backend deployed first 42703s on every cancel (the 119 lesson).
--
-- WHAT ───────────────────────────────────────────────────────────────────────
--   1. `user_subscriptions.renewal_cancelled_at` — the user's opt-out of
--      renewal. NULL = renewal on (the default for every existing row).
--   2. `subscription_cancellations` — the append-only exit-survey ledger.
--   3. `user_subscriptions_live` recreated so the operator glance shows who
--      opted out (a view cannot gain a column without a DROP + CREATE).
--
-- WHY A FLAG AND NOT A STATE MACHINE ─────────────────────────────────────────
-- Wave 1 sells ONE-TIME purchases: nothing renews, so cancelling stops no
-- charge today. The term itself is untouched — access runs to `expires_at` and
-- the existing expired→free fallback (093's `effective_plan_id`) takes over
-- exactly as it does for a subscription nobody cancelled. So there is no new
-- lifecycle state to model, and deliberately none is added: `status` in
-- `user_subscriptions_live` still reads active/expired/locked, and the quota
-- gate is not taught a fourth answer it would have to enforce.
--
-- What the flag IS load-bearing for is Wave 2: the renewal job MUST charge only
-- where `renewal_cancelled_at IS NULL`. Recording the intent now means the day
-- auto-renewal ships there is already an honest opt-out list, instead of a
-- migration that has to invent one.
--
-- THE TRIGGER TRAP (why the backend writes this column ALONE) ────────────────
-- `trg_user_subscriptions_assignment` is BEFORE UPDATE **OF plan_id** (verified
-- live 2026-08-08), and its body re-derives `expires_at` from
-- `plans.duration_days` whenever plan_id changes. Writing the flag in an UPDATE
-- that also touches plan_id would therefore silently re-stamp the term. The
-- backend writes `renewal_cancelled_at` (+ `updated_at`) and nothing else —
-- see backend/app/services/subscription_service.py.
--
-- ⚠ MIGRATION-FILE DRIFT (project rule: the files are NOT the prod schema) ────
-- `user_subscriptions_live` is recreated below from the LIVE definition
-- (`pg_get_viewdef`, read 2026-08-08), which is migration **105**'s shape — the
-- library meter (library_unlocks_used/limit, library_period_key,
-- library_period_resets_at, s.library_unlocks_override) plus the widened
-- LATERAL alias list get_user_quota_state now returns. Recreating it from
-- 093's column list, as the feature brief assumed, would have SILENTLY DROPPED
-- those four operator columns. Anything that touches this view again must
-- re-read the live definition first.

-- ── 1. The opt-out flag ──────────────────────────────────────────────────────

ALTER TABLE public.user_subscriptions
    ADD COLUMN IF NOT EXISTS renewal_cancelled_at timestamptz;

COMMENT ON COLUMN public.user_subscriptions.renewal_cancelled_at IS
    'When the user opted OUT of renewal (120). NULL = renewal on (default). '
    'Declarative in Wave 1 — one-time purchases renew nothing, so setting it '
    'stops no charge and does NOT shorten the term: access runs to expires_at '
    'and then falls back to free like any other lapsed subscription. It becomes '
    'load-bearing in Wave 2, where the renewal job may charge ONLY rows with '
    'renewal_cancelled_at IS NULL. Cleared by an explicit undo and by a new '
    'paid grant (buying again is re-opting in). WRITE IT ALONE: '
    'trg_user_subscriptions_assignment is BEFORE UPDATE OF plan_id and '
    're-derives expires_at, so an UPDATE that also touches plan_id would '
    're-stamp the term.';

-- ── 2. The exit-survey ledger ────────────────────────────────────────────────
-- Append-only: rows are never deleted and never re-written except to stamp
-- `revoked_at` when the user undoes the cancellation. The survey recorded a
-- true moment — that someone chose to leave, and why — so a later re-purchase
-- does NOT revoke it (only an explicit undo does).
--
-- ON DELETE CASCADE, unlike payment_transactions (117, ON DELETE SET NULL):
-- this is product feedback, not a financial record. Nothing must survive the
-- account it describes, and a deleted account taking its survey answer with it
-- is the PDPL-friendlier default.

CREATE TABLE IF NOT EXISTS public.subscription_cancellations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    plan_id             text NOT NULL,          -- what they were cancelling
    reason              text NOT NULL
        CHECK (reason IN ('expensive', 'no_longer_needed', 'something_wrong', 'other')),
    comment             text,                   -- optional free text, any reason
    expires_at_snapshot timestamptz,            -- term end AT CANCEL TIME
    created_at          timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz             -- stamped by «تراجع عن الإلغاء»
);

COMMENT ON TABLE public.subscription_cancellations IS
    'Exit survey: why a paid subscriber cancelled renewal (120). APPEND-ONLY — '
    'one row per cancel action, never deleted; `revoked_at` is stamped when the '
    'user undoes the cancellation and is the only UPDATE this table takes. This '
    'is the product value of the feature in Wave 1 (nothing renews yet, so the '
    'flag itself stops no money). RLS enabled with ZERO policies = deny-all for '
    'anon and authenticated; service_role bypasses RLS, so the backend is '
    'unaffected. There is no user-facing read of this table — do NOT add a '
    'policy (118 lockdown posture).';

COMMENT ON COLUMN public.subscription_cancellations.plan_id IS
    'The plan being cancelled, copied at cancel time. Deliberately NOT a FK and '
    'never re-read from user_subscriptions: a later grant, refund or expiry '
    'rewrites that row, and the survey must keep saying what was true when the '
    'answer was given.';

COMMENT ON COLUMN public.subscription_cancellations.expires_at_snapshot IS
    'The term end as it stood at cancel time — what the user was told in '
    '«تبقى باقتك فعّالة حتى …». Snapshotted for the same reason as plan_id.';

COMMENT ON COLUMN public.subscription_cancellations.revoked_at IS
    'Set when the user reactivates («تراجع عن الإلغاء»). NULL = the '
    'cancellation still stands. A re-PURCHASE clears the subscription flag but '
    'leaves this NULL on purpose: the user did cancel, and buying again later '
    'does not un-say why they left.';

-- The one access pattern: "this user's newest un-revoked survey row" (the undo
-- path) and "this user's answers, newest first" (analytics).
CREATE INDEX IF NOT EXISTS idx_subscription_cancellations_user
    ON public.subscription_cancellations (user_id, created_at DESC);

ALTER TABLE public.subscription_cancellations ENABLE ROW LEVEL SECURITY;
-- No policies: service-role only, matching library_unlocks (104) and the
-- llm_calls ledger (058).
REVOKE ALL ON public.subscription_cancellations FROM anon, authenticated;

-- ── 3. Operator view — recreated with renewal_cancelled_at ───────────────────
-- Verbatim the LIVE 105 definition (see the drift note in the header) with ONE
-- added column: s.renewal_cancelled_at, placed beside s.expires_at because the
-- two are read together — "runs until X, and the user has/hasn't opted out".

DROP VIEW IF EXISTS public.user_subscriptions_live;
CREATE VIEW public.user_subscriptions_live
WITH (security_invoker = true)
AS
SELECT
    s.user_id,
    u.email,
    q.plan_id,
    q.plan_name_ar,
    CASE
        WHEN q.locked     THEN 'locked'
        WHEN q.is_expired THEN 'expired'
        ELSE 'active'
    END AS status,
    q.is_expired,
    q.effective_plan_id,
    q.effective_name_ar,
    round((q.session_cost * 100::double precision)::numeric, 2) AS points_session_used,
    q.points_session      AS points_session_limit,
    round((q.weekly_cost  * 100::double precision)::numeric, 2) AS points_weekly_used,
    q.points_weekly       AS points_weekly_limit,
    q.ocr_pages           AS ocr_pages_used,
    q.ocr_pages_monthly   AS ocr_pages_limit,
    q.library_unlocks_used  AS library_unlocks_used,
    q.library_unlocks_limit AS library_unlocks_limit,
    q.library_period_key,
    q.library_period_resets_at,
    s.source,
    s.started_at,
    s.expires_at,
    s.renewal_cancelled_at,
    s.redeemed_code,
    s.points_session_override,
    s.points_weekly_override,
    s.points_monthly_override,
    s.ocr_pages_monthly_override,
    s.web_calls_monthly_override,
    s.library_unlocks_override,
    s.created_at,
    s.updated_at
FROM public.user_subscriptions s
JOIN public.users u ON u.user_id = s.user_id
CROSS JOIN LATERAL public.get_user_quota_state(s.user_id) q(
    locked, plan_id, plan_name_ar, expires_at, is_expired,
    effective_plan_id, effective_name_ar,
    points_session, points_weekly, points_monthly,
    ocr_pages_monthly, web_calls_monthly,
    session_cost, weekly_cost, ocr_pages,
    session_oldest, weekly_oldest, ocr_oldest,
    library_unlocks_limit, library_unlocks_used, library_period_key,
    library_period_resets_at
);

COMMENT ON VIEW public.user_subscriptions_live IS
  'Operator-facing subscription state: derived status, every limit and its '
  'current usage (points, OCR, library unlocks), and renewal_cancelled_at — '
  'who has opted out of renewal (120). service_role only.';

-- Permissions — mirrors migrations 093 and 105. A recreated view does NOT
-- inherit the old one's ACL, so this REVOKE is not optional: without it the
-- view would be born with whatever the schema default grants.
REVOKE SELECT ON public.user_subscriptions_live FROM anon, authenticated;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY VERIFICATION — run manually; every check must PASS
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. The column exists and every existing row is opted IN. EXPECT: 0.
-- SELECT count(*) FROM public.user_subscriptions WHERE renewal_cancelled_at IS NOT NULL;
--
-- -- 2. The survey table is deny-all for client roles. EXPECT: ZERO ROWS.
-- SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--  WHERE table_schema = 'public' AND table_name = 'subscription_cancellations'
--    AND grantee IN ('anon', 'authenticated');
--
-- -- 3. RLS on, zero policies (a policy here could only widen access).
-- --    EXPECT: relrowsecurity = true, n_policies = 0.
-- SELECT c.relrowsecurity,
--        (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS n_policies
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relname = 'subscription_cancellations';
--
-- -- 4. The view kept ALL of 105's columns and gained exactly one. Measured
-- --    live before this migration was written: 30 columns, 5 of them
-- --    library_* (4 meter columns + library_unlocks_override).
-- --    EXPECT: n_cols = 31, n_library = 5, has_flag = 1.
-- SELECT count(*)                                                    AS n_cols,
--        count(*) FILTER (WHERE column_name LIKE 'library_%')         AS n_library,
--        count(*) FILTER (WHERE column_name = 'renewal_cancelled_at') AS has_flag
--   FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'user_subscriptions_live';
--
-- -- 5. The view is still operator-only. EXPECT: ZERO ROWS.
-- SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--  WHERE table_schema = 'public' AND table_name = 'user_subscriptions_live'
--    AND grantee IN ('anon', 'authenticated');
--
-- -- 6. Live smoke, AFTER the backend deploy (dev account):
-- --    cancel from إعدادات الحساب, then
-- SELECT plan_id, source, expires_at, renewal_cancelled_at
--   FROM public.user_subscriptions_live WHERE email = '<dev account>';
-- SELECT plan_id, reason, comment, expires_at_snapshot, created_at, revoked_at
--   FROM public.subscription_cancellations ORDER BY created_at DESC LIMIT 5;
-- --    then undo, and re-run both: the flag is NULL and revoked_at is stamped.
