-- 100_seo_sharh.sql
-- Date: 2026-07-23  (APPLIED to prod 2026-07-23 via Supabase MCP)
-- Part of SEO Public Library Phase 3 — .claude/plans/seo_public_library.md
--   (§ "Phase 3" → seo_sharh cache; § "Content sources").
--
-- Purpose:
--   public.seo_sharh — cache of the AI شرح (explanation) rendered as the gated
--   value-add on مادة pages (/regulations/{slug}/{article}). One row per
--   seo_articles row (keyed by regulation_id + article_no, NOT by seo_articles.id
--   — that table is delete+rebuilt by its index script, so its uuids are not
--   stable; the (regulation, article_no) pair is).
--
--   Generation policy (scripts/generate_sharh.py):
--     * PREGENERATE only open-tier regulations (seo_item_meta.seo_tier='open',
--       curated ~54 regs) — never batch-generate all 52k articles.
--     * Long-tail: generated on demand later (NOT in the anon request path —
--       anon pages render the شرح teaser only when a cached row exists).
--   Ledger slot for generation calls: 'sharh_generator' (tier_2 flash).
--
-- Security / RLS: NEW table → RLS ENABLED, no policies, REVOKE ALL (deny-all,
--   service-role only — same convention as 095-099).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS.

BEGIN;

CREATE TABLE IF NOT EXISTS public.seo_sharh (
    regulation_id  uuid NOT NULL,
    article_no     int  NOT NULL,
    sharh_md       text NOT NULL,
    model          text,
    generated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (regulation_id, article_no)
);

COMMENT ON TABLE public.seo_sharh IS
    'Cached AI شرح per مادة (100). Keyed (regulation_id, article_no) — stable '
    'across seo_articles rebuilds. Pregenerated for open-tier regs only by '
    'scripts/generate_sharh.py (slot sharh_generator); anon pages render the '
    'teaser only when a cached row exists (no LLM call in the anon path). '
    'First 2 lines free, rest gated (signup carrot).';

ALTER TABLE public.seo_sharh ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.seo_sharh FROM anon, authenticated;

COMMIT;
