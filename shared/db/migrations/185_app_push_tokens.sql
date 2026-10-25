-- ════════════════════════════════════════════════════════════════════════════
-- 185 — app_push_tokens: Expo push tokens for «إجابتك جاهزة» (native app)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/mobile/06_phase3_push.md §3 B1. Web counterpart: 165.
-- Depends on: public.users (user_id PK, auth_id = Supabase auth UUID).
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
--             DROP POLICY IF EXISTS + CREATE POLICY. Re-runnable.
-- Apply BEFORE deploying the backend that serves POST /api/v1/push/app-token
-- (the route upserts into this table; a missing table = 500 on registration).
--
-- One row per installed app (one device = one Expo push token,
-- "ExponentPushToken[...]"). The token is globally unique: if the same phone
-- signs in under a different account, the backend upserts ON CONFLICT (token)
-- and moves the row to the new owner, so a shared phone never notifies the
-- previous user.
--
-- A separate table, not columns on push_subscriptions: that table has
-- p256dh / auth NOT NULL and a browser-endpoint allowlist model that does not
-- apply to Expo (one fixed host, exp.host).
--
-- PRIVACY: this table stores NO content — only the device token, platform and
-- app version. The payload sent (backend/app/services/app_push_service.py) is
-- a fixed «ريحان — إجابتك جاهزة» + data.conversation_id; no question/answer
-- text or conversation title ever reaches Expo / Google / Apple (PDPL,
-- وضع السرية). The token is a device identifier AND a send capability: never
-- log it.
--
-- ACCESS: the backend writes and sends with the service role (bypasses RLS).
-- The policies below only matter if a client role ever touches the table
-- directly: an authenticated user can see / remove ONLY their own rows.
-- No INSERT or UPDATE policy — delivery bookkeeping (last_success_at, failure_count) is
-- service-role only.
--
-- ACCOUNT DELETION: ON DELETE CASCADE from public.users removes the rows;
-- add app_push_tokens to the nuke-account checklist.
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.app_push_tokens (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    token            text        NOT NULL UNIQUE,
    platform         text        NOT NULL CHECK (platform IN ('android', 'ios')),
    app_version      text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_success_at  timestamptz,
    failure_count    int         NOT NULL DEFAULT 0
);

-- The sender loads "all tokens of user X" on every notified turn.
CREATE INDEX IF NOT EXISTS app_push_tokens_user_id_idx
    ON public.app_push_tokens (user_id);

ALTER TABLE public.app_push_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS app_push_tokens_select_self ON public.app_push_tokens;
CREATE POLICY app_push_tokens_select_self ON public.app_push_tokens
    FOR SELECT TO authenticated
    USING (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );

-- No INSERT policy (unlike 165): only the backend writes, through the
-- validated POST /api/v1/push/app-token — a client must not plant a token row.
DROP POLICY IF EXISTS app_push_tokens_insert_self ON public.app_push_tokens;

DROP POLICY IF EXISTS app_push_tokens_delete_self ON public.app_push_tokens;
CREATE POLICY app_push_tokens_delete_self ON public.app_push_tokens
    FOR DELETE TO authenticated
    USING (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );

-- Anonymous callers get nothing (no anon policy + no grant).
REVOKE ALL ON public.app_push_tokens FROM anon;

COMMENT ON TABLE public.app_push_tokens IS
  'Expo push tokens for «إجابتك جاهزة» in the native app (mobile/06_phase3_push.md '
  '§3 B1). One row per device token; stores no content. Backend sends via '
  'service role; a DeviceNotRegistered ticket deletes the row, other failures '
  'bump failure_count and the row is deleted at 5.';
