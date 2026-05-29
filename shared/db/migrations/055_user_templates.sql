-- 055_user_templates.sql
-- Per-user, user-editable markdown template library ("قوالبي" / My Templates).
--
-- This is a NEW feature table and is DISTINCT from:
--   * public.system_templates (046) — global, system-owned, semantic-search library.
--   * the OLD public.user_templates (020, DROPPED in 034) — different shape
--     (prompt_template / agent_family / is_active). This migration does NOT
--     reverse 034; it creates a fresh table with the markdown-body shape below.
--
-- Ownership model:
--   * Each row belongs to one user (user_id FK -> users, ON DELETE CASCADE).
--   * Users own their rows via RLS (same pattern as workspace_items / artifacts).
--   * created_by distinguishes user-authored rows from agent-authored ones:
--     the service-role agent tool (which bypasses RLS) inserts rows with
--     created_by = 'agent'; the UI inserts created_by = 'user' (the default).
--
-- Reuse notes (matching existing project conventions):
--   * created_by reuses the existing `workspace_creator` enum ('user','agent')
--     created in 026_workspace_items.sql — no new enum is introduced.
--   * updated_at auto-update reuses the existing public.update_updated_at()
--     trigger function defined in 014_triggers.sql.
--
-- Idempotent: re-runs are safe (IF NOT EXISTS / DROP ... IF EXISTS guards).

-- ============================================
-- 0. RECONCILE LEGACY SHAPE (migration drift guard)
-- ============================================
-- An OLD user_templates table (migration 020 shape: prompt_template /
-- agent_family / is_active) still exists in environments where the drop
-- migration 034 was never applied (production is one such case). That defunct
-- concept is unrelated to this feature. If the legacy shape is detected, drop
-- it so the canonical markdown-template table below can be created cleanly.
-- Keyed on the legacy-only `prompt_template` column, so once THIS table is in
-- use the block never fires again — it will NOT touch real markdown templates.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_templates'
          AND column_name = 'prompt_template'
    ) THEN
        DROP TABLE public.user_templates CASCADE;
    END IF;
END $$;

-- ============================================
-- 1. TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS public.user_templates (
    template_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    content_md   TEXT NOT NULL DEFAULT '',           -- the editable markdown body
    created_by   workspace_creator NOT NULL DEFAULT 'user',
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ                          -- soft delete; NULL means active
);

-- ============================================
-- 2. INDEX
-- ============================================
-- Per-user list query: SELECT ... WHERE user_id = $1 AND deleted_at IS NULL.
CREATE INDEX IF NOT EXISTS idx_user_templates_user_id
    ON public.user_templates (user_id)
    WHERE deleted_at IS NULL;

-- ============================================
-- 3. updated_at TRIGGER (reuses public.update_updated_at from 014)
-- ============================================
DROP TRIGGER IF EXISTS trg_user_templates_updated_at ON public.user_templates;
CREATE TRIGGER trg_user_templates_updated_at
    BEFORE UPDATE ON public.user_templates
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();

-- ============================================
-- 4. ROW LEVEL SECURITY
-- Rows are scoped to the current user, mirroring workspace_items / artifacts.
-- ============================================
ALTER TABLE public.user_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_templates_select ON public.user_templates;
CREATE POLICY user_templates_select ON public.user_templates FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS user_templates_insert ON public.user_templates;
CREATE POLICY user_templates_insert ON public.user_templates FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS user_templates_update ON public.user_templates;
CREATE POLICY user_templates_update ON public.user_templates FOR UPDATE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS user_templates_delete ON public.user_templates;
CREATE POLICY user_templates_delete ON public.user_templates FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- ============================================
-- 5. COMMENTS
-- ============================================
COMMENT ON TABLE public.user_templates IS
    'Per-user, user-editable markdown templates ("قوالبي" / My Templates). Distinct from global system_templates (046) and the old dropped user_templates (020/034). The service-role agent tool inserts rows with created_by = ''agent''.';

COMMENT ON COLUMN public.user_templates.content_md IS
    'The editable markdown body of the template.';

COMMENT ON COLUMN public.user_templates.created_by IS
    'Origin of the row (reuses the workspace_creator enum). ''user'' = authored in the UI (default); ''agent'' = inserted by the service-role agent tool.';

COMMENT ON COLUMN public.user_templates.deleted_at IS
    'Soft delete timestamp. NULL means active. Legal data is never permanently removed.';
