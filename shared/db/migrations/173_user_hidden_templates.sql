-- 173_user_hidden_templates.sql
-- Per-user hide list for SYSTEM templates (قالب عام).
--
-- System templates are NOT database rows: they ship as markdown files in
-- agents/writer/templates/<subtype>/*.md and get a stable uuid5 id from their
-- path (agents/writer/system_templates.py). Every user sees all of them, read-only.
-- "Deleting" one from قوالبي inserts a row here; the writer planner and the
-- /templates list both skip hidden ids. Restoring = deleting the row.
--
-- template_id has no FK: it references a file, not a table.
-- Plan: .claude/plans/writer_planner_templates_v2.md §2.2 (option a).
--
-- Idempotent: re-runs are safe.

CREATE TABLE IF NOT EXISTS public.user_hidden_templates (
    user_id      UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    template_id  UUID NOT NULL,
    hidden_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, template_id)
);

ALTER TABLE public.user_hidden_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_hidden_templates_select ON public.user_hidden_templates;
CREATE POLICY user_hidden_templates_select ON public.user_hidden_templates FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS user_hidden_templates_insert ON public.user_hidden_templates;
CREATE POLICY user_hidden_templates_insert ON public.user_hidden_templates FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS user_hidden_templates_delete ON public.user_hidden_templates;
CREATE POLICY user_hidden_templates_delete ON public.user_hidden_templates FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

COMMENT ON TABLE public.user_hidden_templates IS
    'System templates (repo files, uuid5 ids) a user removed from their قوالبي. Hidden ids are skipped by GET /templates and the writer planner.';
