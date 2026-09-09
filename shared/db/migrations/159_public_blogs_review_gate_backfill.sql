-- 159_public_blogs_review_gate_backfill.sql
-- Closes the review gate that migration 157 deliberately left open.
--
-- Plan: .claude/plans/blog_subjects.md §2 · request R2 from the marketing repo
-- (C:\Programming\marketing\plans\marketing_dashboard\07_request_to_product.md).
--
-- 157 added `review_status` and stored the STATE only, with an explicit note
-- that nothing read it. This migration is the DATA half of switching it on; the
-- CODE half is the `review_status = 'approved'` predicate added, in the same
-- change, to all five public read paths:
--
--     public_blog_service.list_gallery            the /blog gallery
--     public_blog_service._visible_root_ids       the subject counts + hub cap
--     public_blog_service.list_blogs_for_subject  a subject listing
--     public_blog_service._fetch_current_row      the by-slug article + sources
--     library_service.sitemap_blog_urls           the `blog` sitemap section
--
-- ═══════════════════════════════════════════════════════════════════════════
-- WHY THIS BACKFILL IS SCOPED AND 157's WAS NOT
-- ═══════════════════════════════════════════════════════════════════════════
-- 157 could approve every pending row unconditionally: the gate did not exist,
-- so `pending` carried no meaning and no row could have been deliberately held.
-- That is no longer true. Between that migration and this one the marketing
-- dashboard began submitting articles it intends to HOLD, and under its
-- `HOLD_STRATEGY=unlisted` a held draft is exactly a row with `is_public=false`.
--
-- So this backfill approves only what is ALREADY PUBLICLY VISIBLE — the rows
-- that would otherwise vanish from a live site the moment the predicate lands.
-- A held draft keeps its honest `pending`. Approving it would forge the human
-- decision the whole gate exists to record, and would publish, on deploy, an
-- article nobody has read.
--
-- Idempotent: re-running approves nothing new once the set is empty.
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE public.public_blogs
   SET review_status = 'approved'
 WHERE review_status <> 'approved'
   AND is_current
   AND is_public
   AND is_published
   AND deleted_at IS NULL;

-- The gate's read shape: every public predicate now filters `review_status`
-- alongside the three booleans, so the partial index carries it. 157's index is
-- keyed on `review_status` alone for the moderation queue; this one serves the
-- visibility predicate itself.
CREATE INDEX IF NOT EXISTS idx_public_blogs_visible
    ON public.public_blogs(created_at DESC)
    WHERE is_current
      AND is_public
      AND is_published
      AND review_status = 'approved'
      AND deleted_at IS NULL;

COMMENT ON COLUMN public.public_blogs.review_status IS
    'pending | approved. ENFORCED since migration 159: ''approved'' is a '
    'precondition of every public read (gallery, subject counts, subject '
    'listing, by-slug article, sitemap). A pending row 404s by slug rather '
    'than serving noindex — the editorial hold has to be invisible. NOT '
    'enforced on get_by_job_id, the service-authed publisher read-back, which '
    'must see the pending row it just wrote. append_public_blog_version() '
    'carries the value forward, so an SEO rewrite of an approved article stays '
    'approved.';
