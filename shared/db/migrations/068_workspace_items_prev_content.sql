-- 068_workspace_items_prev_content.sql
-- (Renumbered from 067 — a parallel security-fix session took 067_security_rls_lockdown.
--  Applied to prod 2026-06-11 via Supabase MCP as "workspace_items_prev_content".)
-- Pre-edit snapshot column for the artifact editor (plan: .claude/plans/artifact_editor.md).
-- The editor's batch tool (edit_supabase_md) writes the pre-edit content_md here
-- in the SAME version-guarded UPDATE that writes the new content_md, so the
-- snapshot and the content can never diverge. One-level undo: overwritten on
-- each subsequent edit. No RLS change — column rides workspace_items' policies.

ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS prev_content_md text;

COMMENT ON COLUMN public.workspace_items.prev_content_md IS
    'Pre-edit snapshot written by the artifact editor in the same UPDATE as content_md. One-level undo; overwritten on each edit.';
