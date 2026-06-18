-- 070_blog_posts.sql
-- Feature: مدونة / Public Share-by-Link for تحليل قانوني
-- Plan: .claude/plans/blog_share_links.md  (§ "Data model")
--
-- Purpose:
--   An authenticated user publishes a written artifact (workspace_items where
--   kind = 'agent_writing') to a PUBLIC, UNLISTED, READ-ONLY page at an
--   unguessable URL (/blog/<token>) that anyone may open without signing in.
--   We use the SNAPSHOT model: at publish time the backend freezes content_md +
--   the fully-resolved Reference[] into this row. The public page reads ONLY the
--   snapshot — immutable, no anon access to live workspace data, and it survives
--   later edits/deletes of the original artifact.
--
-- Dependencies:
--   - 001_extensions.sql   (pgcrypto -> gen_random_bytes / gen_random_uuid)
--   - 003_users.sql        (users.user_id PK, users.auth_id UNIQUE = auth.uid())
--   - 014_triggers.sql     (public.update_updated_at() trigger function)
--   - 026_workspace_items.sql (workspace_items.item_id — provenance ref only)
--
-- Verified live-state facts (canonical migration files; pgcrypto enabled in 001):
--   * workspace_items PK = item_id (UUID); has kind/message_id/conversation_id/
--     title/content_md; "subtype" lives in metadata->>'subtype'.
--   * users PK = user_id (UUID); owner-scope maps via users.auth_id = auth.uid().
--   * pgcrypto IS installed -> gen_random_bytes(16) available for the token.
--
-- Security (2026-06-11 audit lesson — NO anonymous DML):
--   * RLS enabled.
--   * SELECT policy for anon + authenticated reads ONLY published, non-deleted
--     rows. This is the entire public surface.
--   * Owner-scoped UPDATE/DELETE for authenticated (revoke / unpublish from app).
--   * NO anon INSERT/UPDATE/DELETE. All writes happen via the backend
--     service-role client (bypasses RLS) on ownership-checked endpoints.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS before CREATE POLICY, guarded trigger creation.

BEGIN;

------------------------------------------------------------------------
-- 1. Table
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.blog_posts (
    post_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Unguessable 32-char hex slug -> URL. pgcrypto verified present (001).
    -- Backend MAY pass its own token; otherwise this default mints one.
    token           TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex'),

    -- Publisher. FK + RLS owner scope.
    owner_user_id   UUID NOT NULL REFERENCES public.users(user_id),

    -- Provenance ref to workspace_items.item_id. INTENTIONALLY NO FK / NO
    -- cascade: the snapshot is independent and MUST survive deletion of the
    -- source artifact. Informational only.
    source_item_id  UUID,

    -- e.g. 'legal_synthesis' -> label "تحليل قانوني" (from item metadata->>'subtype').
    subtype         TEXT,

    -- The السؤال shown on the public page (publish-time editable; verbatim).
    question_text   TEXT NOT NULL,

    -- Page heading + OG title. Defaults (in app) to the artifact title.
    title           TEXT,

    -- SNAPSHOT of the artifact body at publish time.
    content_md      TEXT NOT NULL,

    -- SNAPSHOT of the resolved Reference[] (Reference.model_dump(mode="json")).
    references_json JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Unpublish toggle. Anon read filters on this (kill switch for leaked link).
    is_published    BOOLEAN NOT NULL DEFAULT true,

    -- Cheap analytics; best-effort increment on public GET.
    view_count      INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

------------------------------------------------------------------------
-- 2. Indexes
------------------------------------------------------------------------
-- Unique lookup key (also enforced by the UNIQUE constraint, but named index
-- documents the access path).
CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_posts_token
    ON public.blog_posts (token);

-- Hot path: public page resolves a published, non-deleted post by token.
CREATE INDEX IF NOT EXISTS idx_blog_posts_published
    ON public.blog_posts (token)
    WHERE is_published AND deleted_at IS NULL;

-- Owner-scoped listing / RLS owner checks.
CREATE INDEX IF NOT EXISTS idx_blog_posts_owner_user_id
    ON public.blog_posts (owner_user_id);

------------------------------------------------------------------------
-- 3. updated_at trigger
------------------------------------------------------------------------
DROP TRIGGER IF EXISTS update_blog_posts_updated_at ON public.blog_posts;
CREATE TRIGGER update_blog_posts_updated_at
    BEFORE UPDATE ON public.blog_posts
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

------------------------------------------------------------------------
-- 4. RLS
------------------------------------------------------------------------
ALTER TABLE public.blog_posts ENABLE ROW LEVEL SECURITY;

-- Public unlisted read: anon + authenticated may read ONLY published,
-- non-deleted rows. This is the whole point of the feature.
DROP POLICY IF EXISTS "blog_posts_public_read" ON public.blog_posts;
CREATE POLICY "blog_posts_public_read"
ON public.blog_posts
FOR SELECT
TO anon, authenticated
USING (is_published AND deleted_at IS NULL);

-- Owner-scoped UPDATE (unpublish / edit) for authenticated. Maps the JWT to a
-- public.users row exactly like artifacts_update in 019. NO anon UPDATE.
DROP POLICY IF EXISTS "blog_posts_owner_update" ON public.blog_posts;
CREATE POLICY "blog_posts_owner_update"
ON public.blog_posts
FOR UPDATE
TO authenticated
USING (
    owner_user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
)
WITH CHECK (
    owner_user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
);

-- Owner-scoped DELETE (hard revoke) for authenticated. NO anon DELETE.
DROP POLICY IF EXISTS "blog_posts_owner_delete" ON public.blog_posts;
CREATE POLICY "blog_posts_owner_delete"
ON public.blog_posts
FOR DELETE
TO authenticated
USING (
    owner_user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
);

-- NOTE: deliberately NO INSERT policy. Creating a post is a backend
-- service-role operation (ownership of the source artifact is checked in
-- application code), which bypasses RLS. No anon/authenticated INSERT path.

------------------------------------------------------------------------
-- 5. Comments
------------------------------------------------------------------------
COMMENT ON TABLE public.blog_posts IS
    'Public share-by-link snapshots of agent_writing artifacts (مدونة). Immutable snapshot of content_md + resolved references at publish time. Read publicly (anon) only when is_published AND deleted_at IS NULL. Writes via backend service-role only.';
COMMENT ON COLUMN public.blog_posts.token IS
    'Unguessable 32-char hex slug -> /blog/<token>. Default encode(gen_random_bytes(16),''hex''); backend may supply its own.';
COMMENT ON COLUMN public.blog_posts.source_item_id IS
    'Provenance ref to workspace_items.item_id. NO FK / NO cascade — snapshot must survive deletion of the source artifact.';
COMMENT ON COLUMN public.blog_posts.subtype IS
    'Artifact subtype (e.g. legal_synthesis) for the page label, copied from workspace_items.metadata->>''subtype''.';
COMMENT ON COLUMN public.blog_posts.question_text IS
    'The السؤال shown on the public page. Pre-filled from the triggering user message but editable at publish; stored verbatim.';
COMMENT ON COLUMN public.blog_posts.content_md IS
    'Snapshot of the artifact body (Markdown) at publish time.';
COMMENT ON COLUMN public.blog_posts.references_json IS
    'Snapshot of the resolved Reference[] (Reference.model_dump(mode="json")) at publish time.';
COMMENT ON COLUMN public.blog_posts.is_published IS
    'Unpublish toggle / kill switch. Anon read requires is_published = true.';
COMMENT ON COLUMN public.blog_posts.view_count IS
    'Best-effort public-view counter, incremented on public GET.';

COMMIT;
