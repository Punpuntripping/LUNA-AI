-- 084_public_blog.sql
-- Feature: blog templates (سؤال / مدونة) + the gated public blog directory.
--
-- Two additive, non-destructive changes layered on the existing share-by-link
-- feature (070_blog_posts). No new table — per product decision the existing
-- blog_posts table "accepts" both share templates; the public blog is the
-- access-gated /blog directory built from those rows.
--
--   1. blog_posts.display_mode — which template a share renders as:
--        'question' (default, today's behavior: السؤال block + answer)
--        'title'    (the مدونة blog-article format: centered title, no question)
--      Both templates are open to EVERY authenticated user (share-by-link).
--
--   2. users.can_access_blog — gates VIEWING the public blog directory (/blog).
--      Default false. Only accounts the owner authorizes (flip via SQL) may
--      browse the collection. Publishing is NOT gated; access is.
--
-- Verified against live prod schema (2026-06-30):
--   * blog_posts: question_text + content_md are NOT NULL -> title-mode posts
--     store '' for question_text (no nullability change required here).
--   * users has no role/admin column (subscription cols dropped in 080) -> a
--     dedicated boolean flag is the lightest authorization surface.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, guarded CHECK constraint, CREATE INDEX
-- IF NOT EXISTS.

BEGIN;

------------------------------------------------------------------------
-- 1. blog_posts.display_mode — share template
------------------------------------------------------------------------
ALTER TABLE public.blog_posts
    ADD COLUMN IF NOT EXISTS display_mode TEXT NOT NULL DEFAULT 'question';

-- Constrain to the two known templates. Guarded so re-running is a no-op.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'blog_posts_display_mode_check'
    ) THEN
        ALTER TABLE public.blog_posts
            ADD CONSTRAINT blog_posts_display_mode_check
            CHECK (display_mode IN ('question', 'title'));
    END IF;
END $$;

COMMENT ON COLUMN public.blog_posts.display_mode IS
    'Share template: ''question'' (السؤال + answer) or ''title'' (مدونة blog-article: centered title, no question block). Both open to all users.';

-- Directory hot path: the public blog lists published, non-deleted مدونة
-- (title-mode) posts, newest first.
CREATE INDEX IF NOT EXISTS idx_blog_posts_directory
    ON public.blog_posts (created_at DESC)
    WHERE display_mode = 'title' AND is_published AND deleted_at IS NULL;

------------------------------------------------------------------------
-- 2. users.can_access_blog — gate for viewing the public blog directory
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS can_access_blog BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.users.can_access_blog IS
    'Authorization to BROWSE the public blog directory (/blog). Default false; flip to true via SQL for authorized accounts. Does NOT gate publishing — both share templates are open to all.';

COMMIT;
