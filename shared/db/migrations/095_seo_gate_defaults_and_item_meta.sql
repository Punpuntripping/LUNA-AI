-- 095_seo_gate_defaults_and_item_meta.sql
-- Date: 2026-07-22  (APPLIED to prod 2026-07-22 via Supabase MCP)
-- Part of SEO Public Library Phase 1 — .claude/plans/seo_public_library.md
--   (§ "Locked decisions" → Gating; § "Default gating policy"; § "Phase 1").
--
-- REVISED from the original draft after a live-schema discovery:
--   public.regulations_v2 / chunks_v2 / chunk_titles_v2 / articles_v2 /
--   cross_references_v2 are **VIEWS** over the pipeline-owned schema
--   `regulation_v2` (base tables regulation_v2.regulations, .chunks, ...).
--   Only cases / circulars / services are base tables in public.
--   ⇒ SEO columns must NOT live on corpus tables/views:
--     * ALTER on the views fails outright (42809), and
--     * columns on pipeline-owned base tables would be lost on re-ingest
--       (the judgments wing PLANS a full re-ingest).
--   ⇒ All per-item SEO state lives in ONE sidecar table `seo_item_meta`
--     keyed (content_type, content_id) that survives any corpus reload.
--   (This also supersedes the originally-drafted 097_seo_slugs_and_reg_tier.sql
--   — slug + seo_tier are sidecar columns now; 097 was never applied.)
--
-- Purpose:
--   1. public.seo_gate_defaults — section-level default gating policy (one row
--      per content_type). Fallback layer for library_service.resolve_gate().
--   2. public.seo_item_meta — unified sidecar: slug (stored URL key), seo_tier
--      (regulations popularity tier: 'open' top ~50–100 regs → article text
--      free), gate_override (per-item hard override via scripts/set_gate.py).
--
--   Resolution order in code (library_service.resolve_gate):
--     seo_item_meta.gate_override → (regulations) seo_item_meta.seo_tier →
--     seo_gate_defaults.default_gate.
--
-- Security / RLS: both tables NEW → RLS ENABLED, no policies (service-role
--   only), REVOKE ALL from anon/authenticated (deny-all convention, cf. 087).
--   Corpus tables' RLS state untouched.
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; seed via ON CONFLICT DO NOTHING
--   (re-runs never clobber an operator's live edits).

BEGIN;

------------------------------------------------------------------------
-- 1. Section-level default gating policy.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.seo_gate_defaults (
    content_type  text PRIMARY KEY,
    default_gate  text NOT NULL CHECK (default_gate IN ('open', 'gated')),
    notes         text
);

COMMENT ON TABLE public.seo_gate_defaults IS
    'Section-level default gating policy for the SEO public library (095). One '
    'row per content_type; library_service.resolve_gate() reads default_gate as '
    'the fallback when an item has no gate_override (and, for regulations, no '
    'seo_tier). notes documents the code-level truncation semantics.';

INSERT INTO public.seo_gate_defaults (content_type, default_gate, notes) VALUES
    ('regulation', 'gated', 'continuous full-doc reading gated; TOC/summary/metadata always free in code'),
    ('article',    'gated', 'effective default resolves via seo_item_meta.seo_tier of the parent regulation in code: open-tier regs render article text free'),
    ('judgment',   'gated', 'full text gated; principle+summary free in code'),
    ('circular',   'gated', 'code applies length threshold ~800 chars: short circulars render fully'),
    ('service',    'open',  'compliance pages never gated'),
    ('form',       'gated', 'template body+download gated; intro/when-to-use free in code')
ON CONFLICT (content_type) DO NOTHING;

ALTER TABLE public.seo_gate_defaults ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.seo_gate_defaults FROM anon, authenticated;

------------------------------------------------------------------------
-- 2. Unified SEO sidecar metadata (slug + tier + per-item gate override).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.seo_item_meta (
    content_type  text NOT NULL CHECK (content_type IN
                    ('regulation','article','judgment','circular','service','form')),
    content_id    text NOT NULL,
    slug          text,
    seo_tier      text CHECK (seo_tier IN ('open', 'gated')),
    gate_override text CHECK (gate_override IN ('open', 'gated')),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_type, content_id)
);

COMMENT ON TABLE public.seo_item_meta IS
    'Sidecar SEO metadata for the public library (095). One row per publishable '
    'item; keyed (content_type, content_id) because the corpus tables/views are '
    'pipeline-owned (regulation_v2 schema) and may be re-ingested — SEO state '
    'must survive reloads. slug = URL key (stored, never recomputed). seo_tier '
    '(regulations only): open = curated top reg, article text free. '
    'gate_override: per-item hard override set by scripts/set_gate.py. '
    'resolve_gate() order: gate_override -> seo_tier (regs) -> seo_gate_defaults.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_seo_item_meta_slug_unique
    ON public.seo_item_meta (content_type, slug)
    WHERE slug IS NOT NULL;

ALTER TABLE public.seo_item_meta ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.seo_item_meta FROM anon, authenticated;

COMMIT;
