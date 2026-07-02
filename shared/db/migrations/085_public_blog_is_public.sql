-- 085_public_blog_is_public.sql
-- Feature: public blog v2 — the curated public gallery.
--
-- Adds ``blog_posts.is_public`` — the third orthogonal flag on a blog post:
--   * display_mode  : 'question' | 'title'   (which template; migration 084)
--   * is_published  : the post/link exists; owner kill switch (migration 070)
--   * is_public     : NEW — present in the PUBLIC gallery at /blog
--
-- Access model (v2, inverted from 084's gated directory):
--   * /blog gallery + every /blog/<token> post are PUBLIC (anon, indexable).
--   * ``users.can_access_blog`` no longer gates VIEWING; it now gates the
--     CURATION action — who may flip is_public=true (push a post into the
--     gallery). Enforced in application code on POST/DELETE /blogs/{id}/publish.
--   * A post is in the gallery only when is_public AND is_published AND not deleted.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.

BEGIN;

ALTER TABLE public.blog_posts
    ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.blog_posts.is_public IS
    'Present in the PUBLIC blog gallery (/blog). Default false. Flipped only by curators (users.can_access_blog) via POST/DELETE /blogs/{post_id}/publish. Anon gallery read requires is_public AND is_published AND deleted_at IS NULL.';

-- Public gallery hot path: published, public, non-deleted posts, newest first.
CREATE INDEX IF NOT EXISTS idx_blog_posts_public_gallery
    ON public.blog_posts (created_at DESC)
    WHERE is_public AND is_published AND deleted_at IS NULL;

COMMIT;
