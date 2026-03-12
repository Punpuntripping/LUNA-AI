-- 017_rls_fix_users_authuid.sql
-- Fix: Wrap bare auth.uid() in subquery (SELECT auth.uid()) on users table policies.
-- Reason: Bare auth.uid() prevents PostgreSQL planner from using index scans;
-- the subquery form evaluates once per query as a stable constant.
-- Also updates get_current_user_id() to use SECURITY INVOKER since the users
-- table is readable by authenticated role via RLS.

-- ============================================
-- FIX HELPER FUNCTION: SECURITY INVOKER
-- ============================================
-- The function only reads from public.users which is accessible via RLS
-- for the authenticated role, so SECURITY DEFINER is unnecessary.
CREATE OR REPLACE FUNCTION public.get_current_user_id()
RETURNS UUID AS $$
    SELECT user_id FROM public.users WHERE auth_id = (SELECT auth.uid())
$$ LANGUAGE sql SECURITY INVOKER STABLE;

-- ============================================
-- FIX USERS TABLE POLICIES: bare auth.uid() → (SELECT auth.uid())
-- ============================================

-- Drop existing policies
DROP POLICY IF EXISTS "users_select_own" ON public.users;
DROP POLICY IF EXISTS "users_update_own" ON public.users;
DROP POLICY IF EXISTS "users_insert_own" ON public.users;

-- Recreate with subquery-wrapped auth.uid()
CREATE POLICY "users_select_own"
ON public.users
FOR SELECT
TO authenticated
USING (auth_id = (SELECT auth.uid()));

CREATE POLICY "users_update_own"
ON public.users
FOR UPDATE
TO authenticated
USING (auth_id = (SELECT auth.uid()))
WITH CHECK (auth_id = (SELECT auth.uid()));

CREATE POLICY "users_insert_own"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (auth_id = (SELECT auth.uid()));

-- ============================================
-- FIX MESSAGES POLICIES: add deleted_at filter on conversations subquery
-- ============================================

DROP POLICY IF EXISTS "messages_select_own" ON public.messages;
DROP POLICY IF EXISTS "messages_insert_own" ON public.messages;

CREATE POLICY "messages_select_own"
ON public.messages
FOR SELECT
TO authenticated
USING (
    conversation_id IN (
        SELECT conversation_id FROM public.conversations
        WHERE user_id = (SELECT user_id FROM public.users WHERE auth_id = (SELECT auth.uid()))
        AND deleted_at IS NULL
    )
);

CREATE POLICY "messages_insert_own"
ON public.messages
FOR INSERT
TO authenticated
WITH CHECK (
    conversation_id IN (
        SELECT conversation_id FROM public.conversations
        WHERE user_id = (SELECT user_id FROM public.users WHERE auth_id = (SELECT auth.uid()))
        AND deleted_at IS NULL
    )
);
