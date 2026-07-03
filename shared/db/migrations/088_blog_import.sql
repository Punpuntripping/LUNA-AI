-- 088_blog_import.sql
-- Blog import (.claude/plans/blog_import.md):
--   1. blog_posts.source_post_id — root provenance for snapshot copies imported
--      into a user's مدوناتي, + per-(owner, source) dedup index.
--   2. Re-point message_attachments.document_id FK case_documents → workspace_items.
-- Idempotent; safe to re-run.

-- ============================================================
-- 1. blog_posts.source_post_id (root provenance)
-- ============================================================
-- Always stores the ROOT original post_id, propagated through copy chains
-- (copying a copy carries the copy's source_post_id forward), so dedup by
-- (owner, source) collapses "imported the same content via different copies'
-- tokens". No FK, matching the source_item_id provenance convention (the
-- original may be soft-deleted; the copy must survive it).
ALTER TABLE public.blog_posts
  ADD COLUMN IF NOT EXISTS source_post_id UUID;

COMMENT ON COLUMN public.blog_posts.source_post_id IS
  'Root original blog_posts.post_id this row was imported from (propagated through copy chains). NULL = authored, not imported.';

-- One live copy of a given source per owner. Partial: deleting your copy
-- allows re-import; authored posts (NULL source) are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_blog_posts_owner_source
  ON public.blog_posts (owner_user_id, source_post_id)
  WHERE deleted_at IS NULL AND source_post_id IS NOT NULL;

-- ============================================================
-- 2. message_attachments.document_id FK → workspace_items
-- ============================================================
-- The chat send path has only ever written workspace_items.item_id values into
-- document_id (ChatInput → SendMessageRequest.attachment_ids →
-- _insert_attachment_links), but the 009 FK pointed at case_documents — every
-- insert was silently rejected (best-effort try/except) and the table is EMPTY
-- in prod (verified 2026-07-02). Re-pointing the FK makes user-message
-- attachment chips actually persist across reloads, and lets blog-note chips
-- ride the same rail. Forward-only: links for old messages were never written
-- and are not reconstructable.
ALTER TABLE public.message_attachments
  DROP CONSTRAINT IF EXISTS message_attachments_document_id_fkey;

ALTER TABLE public.message_attachments
  ADD CONSTRAINT message_attachments_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.workspace_items(item_id)
  ON DELETE CASCADE;

COMMENT ON COLUMN public.message_attachments.document_id IS
  'workspace_items.item_id of the attached item (column name is legacy — FK re-pointed from case_documents to workspace_items in 088).';
