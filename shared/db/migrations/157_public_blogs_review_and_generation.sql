-- 157_public_blogs_review_and_generation.sql
--   1. `review_status`      — the blog is pending and needs human approval.
--   2. `generation_context` — the first draft PLUS the complete context the
--                             aggregator worked from, so a later edit has the
--                             whole picture and not just the finished prose.
--
-- Plan: .claude/plans/blog_subjects.md §2.
--
-- ⚠ APPROVAL ENFORCEMENT IS NOT BUILT (operator: out of scope for now). This
-- migration stores the STATE only. Nothing reads `review_status` yet, so a
-- `pending` row is still publicly visible exactly as it is today. Whoever wires
-- the gate must add `review_status = 'approved'` to the visibility predicate in
-- `public_blog_service` — there are four places (the gallery, the subject feed,
-- the by-slug read and the sitemap feed) and missing one leaks a pending draft.
--
-- Existing rows are backfilled to 'approved': they were published under the old
-- rules and adding a gate must not retroactively un-publish live articles.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- 🚨 WHY `generation_context` IS REVOKED FROM anon/authenticated
-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 153 gives anon a row-level SELECT policy on this table. RLS filters
-- ROWS, not COLUMNS — so without an explicit column revoke, anyone holding the
-- publishable anon key could `select generation_context` through PostgREST and
-- read the retrieval verbatim.
--
-- That is not hypothetical volume: `retrieval_artifacts` averages 51 kB and
-- peaks at 154 kB per turn, against a ~6 kB article — the bulk of it corpus
-- chunk bodies. Storing it unguarded would mint a fresh, unmetered corpus read
-- on a PUBLIC table, which is the same class of hole the access-tiers work
-- exists to close and which `strip_frozen_source_views` closes on the sibling
-- column.
--
-- The column is service-role only. It is never reader-facing, never rendered,
-- and never included in any public response model. It exists for the editor
-- (human or the SEO agent) who comes back to modify the article later.
--
-- Idempotent: safe to re-run.

ALTER TABLE public.public_blogs
    ADD COLUMN IF NOT EXISTS review_status      text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS generation_context jsonb;

COMMENT ON COLUMN public.public_blogs.review_status IS
    'pending | approved. Whether a human has cleared this blog. ⚠ NOT ENFORCED YET — no read path filters on it, so a pending row is still visible. Wiring the gate means adding it to all four visibility predicates in public_blog_service.';
COMMENT ON COLUMN public.public_blogs.generation_context IS
    'The first draft + the complete aggregator context (its input AND output), frozen at generation. SERVICE-ROLE ONLY — SELECT is revoked from anon/authenticated because this carries retrieval bodies and the table has an anon row policy. Never reader-facing.';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'public_blogs_review_status_check'
    ) THEN
        ALTER TABLE public.public_blogs
            ADD CONSTRAINT public_blogs_review_status_check
            CHECK (review_status IN ('pending', 'approved'));
    END IF;
END $$;

-- Already-live articles were published before the gate existed.
UPDATE public.public_blogs
   SET review_status = 'approved'
 WHERE review_status = 'pending';

-- Column-level privilege. PostgREST honours these, so a request naming the
-- column fails rather than returning it.
REVOKE SELECT (generation_context) ON public.public_blogs FROM anon;
REVOKE SELECT (generation_context) ON public.public_blogs FROM authenticated;

-- Browsing by review state (a future moderation queue) without scanning.
CREATE INDEX IF NOT EXISTS idx_public_blogs_review_status
    ON public.public_blogs(review_status)
    WHERE is_current AND deleted_at IS NULL;
