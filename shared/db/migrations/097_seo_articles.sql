-- 097_seo_articles.sql
-- Date: 2026-07-23
-- Part of SEO Public Library Phase 3 — .claude/plans/seo_public_library.md
--   (§ "Phase 3" → seo_articles index; § "Content sources" → مادة text;
--    § "Locked decisions" → Gating).
--
-- Purpose:
--   public.seo_articles — the DERIVED per-مادة index that powers the highest-
--   value template of the whole library: /regulations/{slug}/{article-slug}
--   (~38k long-tail «المادة {N} من {نظام}» pages). One row per (regulation,
--   article number). Each row points at the owning chunk (chunk_id) and carries
--   the per-مادة text extracted from that chunk's body.
--
--   This table is DERIVED + FULLY REBUILDABLE by
--   scripts/build_seo_article_index.py — it holds NO source-of-truth data:
--     * source is chunks_v2 (a VIEW over the pipeline-owned schema
--       `regulation_v2` — never ALTER the corpus surface; cf. 095/096) whose
--       `owns` jsonb enumerates the article numbers a chunk covers, shape
--       {"BAB":[1],"FASL":[2],"MADDA":[14,15,...]}. Live 2026-07-23: 52,685
--       MADDA refs across 11,912 chunks in 1,769 regulations.
--     * the build script may DELETE + REBUILD this table per regulation on each
--       run (safe: everything here can be regenerated from chunks_v2). Because
--       rows are delete+re-inserted per rebuild, updated_at is refreshed at
--       insert time and NO BEFORE UPDATE trigger is added (deviates from the
--       generic updated_at-trigger convention on purpose).
--     * article_text is EXTRACTED per-مادة from the owning chunk. When per-مادة
--       extraction fails, extraction_status='chunk_fallback' and code renders
--       the whole owning chunk (chunk_id) as the body fallback; article_text may
--       be NULL in that case. extraction_status='extracted' means article_text
--       holds the isolated مادة text.
--     * NEVER render titles from chunk_titles_v2 (known wrong-title bug — plan
--       § "Data inventory"); article_label is derived from the article number.
--
--   GATING KEY CONVENTION (read carefully — this table intentionally stores NO
--   gate state): an article's gate is resolved through the sidecar seo_item_meta
--   (095) under content_type='article' with
--       content_id = '{regulation_id}#{article_no}'
--   (e.g. 'a1b2...c3#80'). A seo_item_meta row for an article exists ONLY when an
--   operator sets a per-item override (scripts/set_gate.py). With no such row,
--   library_service.resolve_gate() falls back to the PARENT regulation's
--   seo_tier (seo_item_meta content_type='regulation'), then to
--   seo_gate_defaults('article'). So the common case = zero article rows in
--   seo_item_meta; overrides are the rare exception.
--
-- No FK on regulation_id / chunk_id: both point into corpus VIEWS
--   (regulations_v2 / chunks_v2 over schema regulation_v2) — a Postgres FK to a
--   view is impossible, and re-ingest would break any such constraint anyway.
--   Referential integrity is the build script's responsibility.
--
-- Security / RLS:
--   NEW table → RLS ENABLED, no policies (RLS-with-no-policies = default-deny for
--   anon/authenticated). Read ONLY by the backend service role (public مادة pages
--   fetch via anon endpoints → service role). REVOKE ALL from anon, authenticated
--   belt-and-suspenders (same deny-all convention as 087 / 095 / 096).
--
-- Dependencies:
--   - 001_extensions.sql (pgcrypto → gen_random_uuid).
--   - 095_seo_gate_defaults_and_item_meta.sql (resolve_gate fallback layer).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; RLS enable + REVOKE re-runnable.

BEGIN;

------------------------------------------------------------------------
-- 1. Derived per-مادة index.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.seo_articles (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    regulation_id      uuid NOT NULL,
    article_no         int  NOT NULL,
    article_label      text NOT NULL,
    slug               text NOT NULL,
    chunk_id           uuid NOT NULL,
    article_text       text,
    extraction_status  text NOT NULL DEFAULT 'chunk_fallback'
                          CHECK (extraction_status IN ('extracted', 'chunk_fallback')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.seo_articles IS
    'Derived, fully rebuildable per-مادة index for /regulations/{slug}/{article} '
    '(097). Built by scripts/build_seo_article_index.py from chunks_v2.owns '
    '(MADDA[] refs); the script may delete+rebuild per regulation. Holds no '
    'source-of-truth data. Gate is NOT stored here: resolved via seo_item_meta '
    'content_type=article, content_id=''{regulation_id}#{article_no}'' (override '
    'only) -> parent regulation seo_tier -> seo_gate_defaults.';
COMMENT ON COLUMN public.seo_articles.regulation_id IS
    'regulations_v2.id (a VIEW over regulation_v2 schema) — no FK possible.';
COMMENT ON COLUMN public.seo_articles.article_no IS
    'Article (مادة) number from chunks_v2.owns->MADDA. Unique per regulation.';
COMMENT ON COLUMN public.seo_articles.article_label IS
    'Arabic display label derived from the number, e.g. «المادة 80». Never taken '
    'from chunk_titles_v2 (known wrong-title bug).';
COMMENT ON COLUMN public.seo_articles.slug IS
    'Stored URL key for the article segment, e.g. «المادة-80» (unique per '
    'regulation; never recomputed at render time).';
COMMENT ON COLUMN public.seo_articles.chunk_id IS
    'Owning chunks_v2.id — the context/fallback body rendered when per-مادة '
    'extraction failed (extraction_status=chunk_fallback).';
COMMENT ON COLUMN public.seo_articles.article_text IS
    'Per-مادة text extracted from the owning chunk. NULL = extraction failed '
    '(see extraction_status).';
COMMENT ON COLUMN public.seo_articles.extraction_status IS
    'extracted = article_text holds the isolated مادة text; chunk_fallback = '
    'extraction failed, render the whole owning chunk (chunk_id) instead.';

------------------------------------------------------------------------
-- 2. Uniqueness + lookup indexes.
------------------------------------------------------------------------
-- One row per (regulation, article number).
CREATE UNIQUE INDEX IF NOT EXISTS idx_seo_articles_reg_no_unique
    ON public.seo_articles (regulation_id, article_no);

-- Article-segment slug is unique within its parent regulation.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seo_articles_reg_slug_unique
    ON public.seo_articles (regulation_id, slug);

-- Fetch all articles of a regulation (TOC / prev-next). Technically covered by
-- the leftmost prefix of idx_seo_articles_reg_no_unique, but kept explicit per
-- plan spec and to survive future changes to that composite index.
CREATE INDEX IF NOT EXISTS idx_seo_articles_regulation_id
    ON public.seo_articles (regulation_id);

------------------------------------------------------------------------
-- 3. RLS: deny-all, service-role only (no policies).
------------------------------------------------------------------------
ALTER TABLE public.seo_articles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.seo_articles FROM anon, authenticated;

COMMIT;
