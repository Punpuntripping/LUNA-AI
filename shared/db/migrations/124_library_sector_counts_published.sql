-- ============================================================================
-- Migration 124 — library_sector_counts_published(): counts of what is SERVABLE
-- Plan: .claude/plans/library_court_sections_publish_ramp.md §1.3
-- Sibling of migration 109 (`library_sector_counts()`), which is NOT modified.
--
-- 109 COUNTS THE CORPUS. THAT WAS ONLY EVER CORRECT BY ACCIDENT.
-- `sector_counts()` and `library_corpus_counts()` count the PUBLISHED set while a
-- wing sits under `SAMPLE_MODE_MAX_IDS` (1000 —
-- backend/app/services/library_service.py:154), by scanning the sidecar id list.
-- Above that ceiling `_published_ids()` returns NULL and the counts fall through
-- to `library_sector_counts()`, the migration-109 RPC — which counts CORPUS
-- rows. The two agreed only because every published wing was small enough that
-- nobody reached the fall-through.
--
-- The plan takes judgments to ~10,000 published of 30,531 and regulations to
-- 1,188 of 3,951. Both cross the ceiling. At that moment /library silently
-- starts advertising 3,951 regulations and 30,531 judgments while the wings
-- serve 1,188 and ~10,000.
--
-- WHY THAT IS WORSE THAN A COSMETIC MISMATCH — the block comment at
-- library_service.py:1744-1786 states the contract these numbers carry, and it
-- is not "roughly how big the library is". The FRONTEND derives both its D9
-- thin-page `noindex` decision and its `generateStaticParams` filter from these
-- counts. A sector reporting 695 rows of which 0 are servable therefore passes
-- the "fat enough to index" test and gets PRERENDERED as a static, indexable,
-- EMPTY page. Measured live on 2026-08-01, before the servable-counts rule
-- existed:
--
--     sector (أنظمة)          corpus   servable   a corpus-based paginator says
--     المواصفات والمقاييس        695          0   78 pages, every one EMPTY
--     الأمن الغذائي              406          0   46 pages, every one EMPTY
--     المعاملات التجارية         693         24   77 pages, 3 of them real
--
-- Soft-404s at scale, which is the exact failure the servable-counts rule exists
-- to prevent. This is the SECOND thing the publish ramp breaks (migration 123 is
-- the first) and it is the harder one to notice, because nothing errors: the
-- numbers just quietly stop describing the product.
--
-- ⚠ THE COUNTS AND THE WING TOTALS STILL DO NOT SUM, IN EITHER DIRECTION. This
-- function inherits every caveat 109's header records, and the publish ramp
-- sharpens one of them:
--   * `unnest` counts a row ONCE PER SECTOR and rows are multi-sector, so these
--     columns OVER-count the wing.
--   * `cases.legal_domains` is populated PER SOURCE FEED, not per row. The
--     ديوان المظالم / ZATCA / تأمين feeds carry NONE, and §3.1 publishes ~2,224
--     of them, so the `judgments` column here will report roughly 7,776 of the
--     ~10,000 judgments the wing actually serves. Those rows are not lost —
--     they are reachable through the unfiltered /judgments hub and through their
--     court section — they simply belong to no sector.
-- No caller may derive a wing total from these columns or a sector count from a
-- wing total. `library_corpus_counts()` counts wing totals separately, on
-- purpose.
--
-- ⚠ `compliance` STAYS IN THE SIGNATURE. The /compliance wing left
-- `_SECTION_SOURCES` on 2026-08-03 and nothing reads that column any more (see
-- library_service.py:1792-1796). It is kept because §1.3 requires the IDENTICAL
-- return signature to 109 — a caller must be able to swap one RPC for the other
-- without touching its row unpacking. It is sourced from `services` ⋈
-- content_type 'service', which is where 109 sources it.
--
-- ⚠ SECURITY: SERVICE_ROLE ONLY, AND THIS FILE DOES NOT REPEAT 109's MISTAKE.
-- 109 granted EXECUTE to `anon, authenticated` on the reasoning that the counts
-- are public information; migration 110 took it back, because the counts being
-- PUBLISHABLE says nothing about who may COMPUTE them. An anon grant is an
-- unauthenticated, unmetered, un-memoised trigger for four full `unnest`
-- aggregations reachable on the Supabase hostname — outside the origin lock,
-- outside the rate limiter, outside Cloudflare, outside the backend's 5-minute
-- memo — against the Postgres that serves chat. This function goes straight to
-- 110's end state in one statement. SECURITY DEFINER stays: it is what lets the
-- function read the pipeline-owned corpora at all, and it was never the
-- exposure. The grant was.
--
-- Idempotent (CREATE OR REPLACE). Zero arguments, no row data ever returned,
-- `SET search_path` pins the tables it can see.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.library_sector_counts_published()
RETURNS TABLE (
    sector      text,
    regulations bigint,
    judgments   bigint,
    compliance  bigint,
    circulars   bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    -- Each CTE is 109's grouped `unnest` with the sidecar join added. The join is
    -- INNER and `seo_item_meta`'s PK is (content_type, content_id), so it matches
    -- at most one sidecar row per corpus row: it can only ever REMOVE unpublished
    -- rows, never duplicate published ones. A fan-out here would inflate every
    -- number on the /library grid with no error.
    WITH r AS (
        SELECT unnest(x.sectors) AS s, count(*) AS c
        FROM public.regulations_v2 x
        JOIN public.seo_item_meta m
          ON m.content_type = 'regulation'
         AND m.content_id = x.id::text
        WHERE m.slug IS NOT NULL
        GROUP BY 1
    ),
    -- Judgments group on `legal_domains`, NOT `sectors` — `cases` has no
    -- `sectors` column. See the under-count caveat in the header.
    j AS (
        SELECT unnest(x.legal_domains) AS s, count(*) AS c
        FROM public.cases x
        JOIN public.seo_item_meta m
          ON m.content_type = 'judgment'
         AND m.content_id = x.id::text
        WHERE m.slug IS NOT NULL
        GROUP BY 1
    ),
    v AS (
        SELECT unnest(x.sectors) AS s, count(*) AS c
        FROM public.services x
        JOIN public.seo_item_meta m
          ON m.content_type = 'service'
         AND m.content_id = x.id::text
        WHERE m.slug IS NOT NULL
        GROUP BY 1
    ),
    k AS (
        SELECT unnest(x.sectors) AS s, count(*) AS c
        FROM public.circulars x
        JOIN public.seo_item_meta m
          ON m.content_type = 'circular'
         AND m.content_id = x.id::text
        WHERE m.slug IS NOT NULL
        GROUP BY 1
    )
    SELECT coalesce(r.s, j.s, v.s, k.s) AS sector,
           coalesce(r.c, 0) AS regulations,
           coalesce(j.c, 0) AS judgments,
           coalesce(v.c, 0) AS compliance,
           coalesce(k.c, 0) AS circulars
    FROM r
    FULL JOIN j ON j.s = r.s
    FULL JOIN v ON v.s = coalesce(r.s, j.s)
    FULL JOIN k ON k.s = coalesce(r.s, j.s, v.s);
$$;

COMMENT ON FUNCTION public.library_sector_counts_published() IS
  'Per-sector counts of PUBLISHED items (seo_item_meta.slug IS NOT NULL) across '
  'the four public-library corpora (regulations_v2 / cases / services / '
  'circulars) — identical return signature to library_sector_counts(), which '
  'counts the CORPUS. Use this one wherever the number is shown to a reader or '
  'feeds an indexability/prerender decision: above SAMPLE_MODE_MAX_IDS the '
  'corpus RPC reports rows the wing cannot serve, and an empty sector page then '
  'passes the thin-page check. SERVICE_ROLE ONLY (see migration 110). Takes no '
  'arguments and never returns row data. Columns do NOT sum to the wing totals '
  '— see migration 124''s header before doing arithmetic on them.';

REVOKE ALL ON FUNCTION public.library_sector_counts_published() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.library_sector_counts_published() TO service_role;

-- ============================================================================
-- ⚠ NOT AUTO-APPLIED. Migration files in this directory are run by hand in the
-- Supabase SQL Editor; nothing in the repo executes them. This function is inert
-- until someone does — and inert is safe, because it ADDS an RPC rather than
-- changing one: `library_sector_counts()` keeps working untouched, and no caller
-- moves onto this function until §1.3 repoints `_published_sample_counts`'s
-- fall-through at it.
--
-- TOGETHER WITH MIGRATION 123 THIS RETIRES `SAMPLE_MODE_MAX_IDS`
-- (backend/app/services/library_service.py:154) FOR THE TWO WINGS THE PUBLISH
-- RAMP TOUCHES: 123 removes the ceiling from the judgments LISTER, this file
-- removes it from the regulations + judgments COUNTS. The constant stays for
-- circulars and services (100 published each, untouched by the ramp), which are
-- still counted by the sidecar id-list scan.
-- ============================================================================
