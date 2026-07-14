-- ============================================================
-- Migration 090: Account deletion (30-day grace + hard purge)
-- APPLIED TO PROD 2026-07-13
-- ============================================================
-- Adds the grace-period marker, makes the users row actually deletable,
-- and installs the transactional purge RPC used by the daily sweep.
--
-- Why the FK flips: `users.auth_id -> auth.users(id)` is ON DELETE CASCADE, so
-- deleting the GoTrue user *tries* to delete public.users. Six FKs pointed at
-- users(user_id) with NO ACTION, which aborted that cascade for any real user —
-- i.e. auth.admin.delete_user() could never have worked before this migration.
--
-- Live constraint names (pg_constraint, 2026-07-13) — note workspace_items still
-- carries its pre-rename name `artifacts_user_id_fkey` (table renamed in 026,
-- constraint was not), and task_state exists only in prod (migration drift), so
-- it is handled dynamically below.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Grace-period marker
-- ------------------------------------------------------------
-- NULL = active account. Set = deletion requested; the account is deactivated
-- immediately (backend gate) and hard-purged once it is > 30 days old.
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS deletion_requested_at timestamptz;

COMMENT ON COLUMN public.users.deletion_requested_at IS
    'Set when the user requests account deletion. 30-day grace period, then the '
    'daily purge sweep hard-deletes the account. NULL = active. Cleared by restore.';

-- Partial index: the sweep only ever scans pending rows.
CREATE INDEX IF NOT EXISTS idx_users_deletion_requested_at
    ON public.users (deletion_requested_at)
    WHERE deletion_requested_at IS NOT NULL;

-- ------------------------------------------------------------
-- 2. FK flips — unblock the users-row delete
-- ------------------------------------------------------------
-- workspace_items (constraint still named artifacts_user_id_fkey — pre-026 name)
ALTER TABLE public.workspace_items
    DROP CONSTRAINT IF EXISTS artifacts_user_id_fkey,
    DROP CONSTRAINT IF EXISTS workspace_items_user_id_fkey;
ALTER TABLE public.workspace_items
    ADD CONSTRAINT workspace_items_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;

ALTER TABLE public.user_preferences
    DROP CONSTRAINT IF EXISTS user_preferences_user_id_fkey;
ALTER TABLE public.user_preferences
    ADD CONSTRAINT user_preferences_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;

ALTER TABLE public.retrieval_artifacts
    DROP CONSTRAINT IF EXISTS retrieval_artifacts_user_id_fkey;
ALTER TABLE public.retrieval_artifacts
    ADD CONSTRAINT retrieval_artifacts_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;

ALTER TABLE public.blog_posts
    DROP CONSTRAINT IF EXISTS blog_posts_owner_user_id_fkey;
ALTER TABLE public.blog_posts
    ADD CONSTRAINT blog_posts_owner_user_id_fkey
    FOREIGN KEY (owner_user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;

-- plan_codes: the code itself outlives the redeemer — SET NULL, never delete.
ALTER TABLE public.plan_codes
    DROP CONSTRAINT IF EXISTS plan_codes_redeemed_by_fkey;
ALTER TABLE public.plan_codes
    ADD CONSTRAINT plan_codes_redeemed_by_fkey
    FOREIGN KEY (redeemed_by) REFERENCES public.users(user_id) ON DELETE SET NULL;

-- task_state: prod-only table (no migration file — drift). Guarded so this
-- migration still applies cleanly to a fresh DB built from the numbered files.
DO $$
DECLARE
    v_con text;
BEGIN
    IF to_regclass('public.task_state') IS NOT NULL THEN
        SELECT conname INTO v_con
          FROM pg_constraint
         WHERE conrelid = 'public.task_state'::regclass
           AND contype = 'f'
           AND confrelid = 'public.users'::regclass;

        IF v_con IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.task_state DROP CONSTRAINT %I', v_con);
        END IF;

        EXECUTE 'ALTER TABLE public.task_state
                 ADD CONSTRAINT task_state_user_id_fkey
                 FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 3. purge_user_data(uuid) — transactional child-table teardown
-- ------------------------------------------------------------
-- Called by the daily sweep BEFORE auth.admin.delete_user(). Empties the heavy
-- child tables in one transaction so the final GoTrue delete (which cascades
-- public.users) stays small.
--
-- It deliberately does NOT delete the users row: that row is the sweep's
-- idempotency marker. It dies only in the same transaction as the auth.users
-- row, so any failure here is retried whole by tomorrow's sweep.
--
-- RETAINED BY DESIGN:
--   llm_calls  — cost/token ledger (source of truth for consumption reports).
--                No FK; user_id becomes a dangling UUID that resolves to no
--                personal data once the user row is gone => pseudonymized.
--   audit_logs — append-only compliance trail; its FK is ON DELETE SET NULL.
CREATE OR REPLACE FUNCTION public.purge_user_data(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    -- retrieval_artifacts BEFORE workspace_items: its artifact_id -> workspace_items
    -- FK is NO ACTION, so the reverse order raises a FK violation.
    DELETE FROM public.retrieval_artifacts WHERE user_id = p_user_id;  -- cascades reranker_runs
    DELETE FROM public.workspace_items     WHERE user_id = p_user_id;  -- cascades workspace_item_references
    DELETE FROM public.conversations       WHERE user_id = p_user_id;  -- cascades messages -> attachments, consultation_articles
    DELETE FROM public.lawyer_cases        WHERE lawyer_user_id = p_user_id;  -- cascades case_memories, case_documents
    DELETE FROM public.message_feedback    WHERE user_id = p_user_id;
    DELETE FROM public.pii_mappings        WHERE user_id = p_user_id;
    DELETE FROM public.user_templates      WHERE user_id = p_user_id;
    DELETE FROM public.user_preferences    WHERE user_id = p_user_id;
    DELETE FROM public.blog_posts          WHERE owner_user_id = p_user_id;

    -- No FK on these — they would orphan silently.
    DELETE FROM public.paused_runs         WHERE user_id = p_user_id;

    IF to_regclass('public.task_state') IS NOT NULL THEN
        EXECUTE 'DELETE FROM public.task_state WHERE user_id = $1' USING p_user_id;
    END IF;

    -- Multi-use plan codes track redeemers in a uuid[] with no FK. Scrub the id,
    -- but do NOT decrement uses_count: the redemption really happened and that
    -- capacity stays consumed.
    UPDATE public.plan_codes
       SET redeemed_by_users = array_remove(redeemed_by_users, p_user_id)
     WHERE p_user_id = ANY(redeemed_by_users);

    RETURN jsonb_build_object('user_id', p_user_id, 'purged', true);
END;
$$;

REVOKE ALL ON FUNCTION public.purge_user_data(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.purge_user_data(uuid) TO service_role;

COMMENT ON FUNCTION public.purge_user_data(uuid) IS
    'Transactional teardown of one user''s child rows. Service-role only. Does NOT '
    'delete the users row (idempotency marker for the purge sweep). Retains llm_calls '
    'and audit_logs by design.';
