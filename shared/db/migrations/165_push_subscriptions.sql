-- ════════════════════════════════════════════════════════════════════════════
-- 165 — push_subscriptions: Web Push endpoints for «إجابتك جاهزة»
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/pwa_step1.md §1D (the plan calls this file 164; 164 was
--       taken by 164_public_blogs_english_slugs.sql, so it lands as 165).
-- Depends on: public.users (user_id PK, auth_id = Supabase auth UUID).
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
--             DROP POLICY IF EXISTS + CREATE POLICY. Re-runnable.
--
-- One row per browser PushSubscription (one device/browser = one endpoint).
-- The endpoint URL is globally unique: if a device is re-subscribed under a
-- different account, the backend upserts ON CONFLICT (endpoint) and moves the
-- row to the new owner, so a shared device never notifies the previous user.
--
-- PRIVACY: this table stores NO content — only the push endpoint and its
-- encryption keys. The payload sent (backend/app/services/push_service.py) is
-- a fixed «إجابتك جاهزة» + a /chat/<id> URL; no question/answer text ever
-- reaches Apple/Google/Mozilla push servers (PDPL, وضع السرية).
--
-- ACCESS: the backend writes and sends with the service role (bypasses RLS).
-- The policies below only matter if a client role ever touches the table
-- directly: an authenticated user can see / add / remove ONLY their own rows.
-- No UPDATE policy — delivery bookkeeping (last_success_at, failure_count) is
-- service-role only.
--
-- ACCOUNT DELETION: ON DELETE CASCADE from public.users removes the rows;
-- add push_subscriptions to the nuke-account checklist.
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    endpoint         text        NOT NULL UNIQUE,
    p256dh           text        NOT NULL,
    auth             text        NOT NULL,
    user_agent       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_success_at  timestamptz,
    failure_count    int         NOT NULL DEFAULT 0
);

-- The sender loads "all subscriptions of user X" on every notified turn.
CREATE INDEX IF NOT EXISTS push_subscriptions_user_id_idx
    ON public.push_subscriptions (user_id);

ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS push_subscriptions_select_self ON public.push_subscriptions;
CREATE POLICY push_subscriptions_select_self ON public.push_subscriptions
    FOR SELECT TO authenticated
    USING (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );

DROP POLICY IF EXISTS push_subscriptions_insert_self ON public.push_subscriptions;
CREATE POLICY push_subscriptions_insert_self ON public.push_subscriptions
    FOR INSERT TO authenticated
    WITH CHECK (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );

DROP POLICY IF EXISTS push_subscriptions_delete_self ON public.push_subscriptions;
CREATE POLICY push_subscriptions_delete_self ON public.push_subscriptions
    FOR DELETE TO authenticated
    USING (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );

-- Anonymous callers get nothing (no anon policy + no grant).
REVOKE ALL ON public.push_subscriptions FROM anon;

COMMENT ON TABLE public.push_subscriptions IS
  'Web Push subscriptions for «إجابتك جاهزة» (pwa_step1.md §1D). One row per '
  'browser endpoint; stores no content. Backend sends via service role; '
  '404/410 from the push service deletes the row, other failures bump '
  'failure_count and the row is deleted at 5.';
