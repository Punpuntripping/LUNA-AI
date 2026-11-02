-- ============================================================================
-- Migration 210 -- `seo_item_meta.indexable` now defaults to false.
--
-- WHY. Publishing must not mean "hand it to Google". Migration 130 defaulted the
-- column to true so the older wings kept their behaviour, which left a trap: every
-- slug script writes only the slug, so a bulk publish entered the sitemap by itself,
-- and a new judgment rendered `index, follow`.
--
-- WHAT. New rows default to false, and rows with no slug are set to false, because
-- publishing one later only writes its slug and would otherwise inherit the old
-- true. Rows that already have a slug are NOT touched: every live page stays exactly
-- as it is in Google.
--
-- Scripts that DO mean Google set the flag themselves: build_compliance_slugs.py,
-- publish_articles.py, build_seo_slugs.py --indexable, build_judgment_slugs.py
-- --indexable.
--
-- Safe to re-run.
-- ============================================================================

BEGIN;

ALTER TABLE public.seo_item_meta ALTER COLUMN indexable SET DEFAULT false;

UPDATE public.seo_item_meta
   SET indexable = false
 WHERE slug IS NULL
   AND indexable;

COMMIT;
