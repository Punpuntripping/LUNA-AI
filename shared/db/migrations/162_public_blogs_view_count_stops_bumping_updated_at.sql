-- 162_public_blogs_view_count_stops_bumping_updated_at.sql
-- Stops the view counter from masquerading as a content-modification date.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- THE BUG
-- ═══════════════════════════════════════════════════════════════════════════
-- `public_blog_service.get_by_slug` bumps `view_count` on every public read
-- (its own docstring: "Best-effort view_count increment"). That UPDATE fired
-- `trg_public_blogs_updated_at`, which set `updated_at = now()`. And
-- `updated_at` is the wing's PUBLISHED CONTENT DATE in two places:
--
--     library_service.sitemap_blog_urls        -> <lastmod> in /sitemaps/blog
--     app/blog/[slug]/page.tsx (buildArticle)  -> JSON-LD `dateModified`
--
-- So every article announced "modified just now" to every crawler, forever,
-- over content that had not changed since it was published. Measured on
-- 2026-09-17 before this migration: all 18 approved articles carried a
-- `lastmod` inside the preceding 48 hours, and the article published on
-- 2026-09-02 reported `updated_at = 2026-09-17 13:58` — 15 days of drift on
-- 31 views. Three fetches of one article three seconds apart returned three
-- different `dateModified` values.
--
-- The loop is self-reinforcing and that is the damaging part: Googlebot's own
-- crawl IS a page view, so the crawl bumped the counter, which bumped the
-- date, which told the next crawl the page had changed again. The wing could
-- never settle into a stable state for the discovery->index decision.
--
-- Compare the wings that ARE indexed, same day, same sitemap index:
--     /sitemaps/regulations  1,682 URLs @ 2026-08-14, 7 @ 08-29   (stable)
--     /sitemaps/compliance   spread across Aug 19-29             (stable)
--     /sitemaps/blog         all 18 within 48h                   (churning)
--
-- Google honours `lastmod` only while it is "consistently and verifiably
-- accurate". A section where every URL reports a fresh timestamp on every
-- fetch is provably not, so the signal gets discounted and the section drops
-- to the lowest-confidence crawl schedule.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ORDER MATTERS: DROP, THEN BACKFILL, THEN RECREATE
-- ═══════════════════════════════════════════════════════════════════════════
-- The backfill below sets `updated_at = created_at` and does NOT touch
-- `view_count` — which means the new trigger's WHEN clause would match it and
-- re-stamp `now()`, silently undoing the repair. Running the UPDATE in the gap
-- where no trigger exists is why these three statements are in this order, and
-- is simpler and safer than DISABLE/ENABLE (a failure mid-migration cannot
-- leave a disabled trigger behind).

DROP TRIGGER IF EXISTS trg_public_blogs_updated_at ON public.public_blogs;

-- ═══════════════════════════════════════════════════════════════════════════
-- THE BACKFILL IS NOT OPTIONAL
-- ═══════════════════════════════════════════════════════════════════════════
-- Fixing the trigger stops the bleeding but leaves every existing row stamped
-- with the time of its last page view. Without this UPDATE the sitemap keeps
-- claiming "modified today" for all 18 articles until each one happens to get
-- a genuine edit — i.e. the defect would outlive its own fix.
--
-- `created_at` is the correct restoration target, and exactly for `is_current`
-- rows. The wing is VERSIONED (migration 155): a real content change appends a
-- NEW row and flips `is_current`, so the current row's `created_at` IS the
-- moment its bytes came into existence. That is precisely what `lastmod` and
-- `dateModified` are supposed to report.
--
-- Scoped to `is_current` because only current rows are ever served — superseded
-- versions keep their historical `updated_at` for audit.
--
-- Idempotent: once restored, `updated_at > created_at` is false and a re-run
-- touches nothing.

UPDATE public.public_blogs
   SET updated_at = created_at
 WHERE is_current
   AND updated_at > created_at;

-- ═══════════════════════════════════════════════════════════════════════════
-- WHY A `WHEN` CLAUSE AND NOT A NEW FUNCTION
-- ═══════════════════════════════════════════════════════════════════════════
-- `update_updated_at()` is shared by many tables across this schema. Teaching
-- it about `view_count` would give every table a column it does not have, so
-- the discrimination belongs in the trigger that knows the table, not in the
-- function that does not.
--
-- `WHEN (OLD.view_count IS NOT DISTINCT FROM NEW.view_count)` fires the bump
-- only for updates that left the counter alone — i.e. genuine edits. A
-- view-only bump no longer touches `updated_at`.
--
-- `IS NOT DISTINCT FROM`, not `=`: `view_count` is nullable and `NULL = NULL`
-- is NULL, which a WHEN clause reads as false. With `=` a row whose counter
-- was NULL on both sides would silently stop recording real edits.
--
-- ⚠ KNOWN EDGE: an UPDATE that changes content AND `view_count` in the same
-- statement will not bump `updated_at`. No caller does this today — the bump
-- is issued alone by `get_by_slug`, approval writes `review_status` alone, and
-- a content rewrite goes through `append_public_blog_version()`, which INSERTs
-- a new row rather than updating this one. If a future caller ever combines
-- them, it must set `updated_at` explicitly.

CREATE TRIGGER trg_public_blogs_updated_at
    BEFORE UPDATE ON public.public_blogs
    FOR EACH ROW
    WHEN (OLD.view_count IS NOT DISTINCT FROM NEW.view_count)
    EXECUTE FUNCTION update_updated_at();

COMMENT ON COLUMN public.public_blogs.updated_at IS
    'CONTENT modification time — the public <lastmod> and JSON-LD dateModified '
    'for this article. Migration 162 detached it from view_count: the trigger '
    'carries WHEN (OLD.view_count IS NOT DISTINCT FROM NEW.view_count), so a '
    'page view no longer re-dates the article. Before 162 every crawl re-'
    'stamped every URL and the whole blog sitemap was untrustworthy to Google. '
    'For an is_current row this equals created_at until a genuine edit lands; '
    'a content rewrite appends a new version rather than updating this row.';

COMMENT ON COLUMN public.public_blogs.view_count IS
    'Display-only counter, rendered on the gallery card (BlogCard.tsx). '
    'NOTHING ranks or orders by it. Bumped best-effort by '
    'public_blog_service.get_by_slug. Since migration 162 it deliberately does '
    'NOT touch updated_at. Note it counts SERVER RENDERS, not humans: the '
    'article route is ISR since 2026-09-17, so a cached hit never reaches the '
    'backend and this under-counts. True per-path view data is in '
    'analytics_events.';
