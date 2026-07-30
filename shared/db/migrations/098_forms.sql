-- 098_forms.sql
-- Date: 2026-07-23
-- Part of SEO Public Library Phase 3 — .claude/plans/seo_public_library.md
--   (§ "Phase 3" → Forms; § "Template specs" → /forms/{slug};
--    § "Content sources" → Forms; § "Cross-cutting risks" → lawyer review).
--
-- Purpose:
--   public.forms — the نماذج (legal-form/template) wing content table backing
--   /forms/{slug} pages. Unlike the corpus wings (regulations/judgments/
--   circulars/compliance read pipeline-owned VIEWS), forms are ORIGINAL content
--   authored INTO this base table: scripts/draft_forms.py AI-drafts them, a human
--   reviews, and only then do they publish.
--
--   Layered gating (decided in code by library_service, per plan § Forms):
--     * use_case_md (متى تستخدمه) + intro_md (شرح) = FREE / SEO layer — the
--       ranking food.
--     * body_md (the template body) + docx_path (the download) = GATED — the
--       signup carrot.
--     * legal_basis = the الأساس النظامي links into المواد (mesh into seo_articles
--       / regulations).
--
--   LIABILITY HARD GATE (see plan § Cross-cutting risks — lawyer review is a hard
--   publish blocker):
--     * a form may EVER be served publicly ONLY when
--         review_status = 'approved' AND is_published = true.
--     * scripts/draft_forms.py writes rows as review_status='draft',
--       is_published=false (never publishes on its own).
--     * a human reviewer flips review_status -> 'approved' (and is_published ->
--       true) after legal review; the public endpoint (GET /public/library/
--       forms/{slug}) and the sitemap feed MUST filter on both flags.
--     * every rendered page still carries a disclaimer + «راجع مختصاً» (frontend).
--
-- Security / RLS:
--   NEW table → RLS ENABLED, no policies (default-deny for anon/authenticated).
--   Served ONLY via the backend service role (public endpoints), which is where
--   the approved+published filter is enforced. REVOKE ALL from anon, authenticated
--   (same deny-all convention as 087 / 095 / 096 / 097).
--
-- Dependencies:
--   - 001_extensions.sql (pgcrypto → gen_random_uuid).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; RLS enable + REVOKE re-runnable.

BEGIN;

------------------------------------------------------------------------
-- 1. Forms (نماذج) content table.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.forms (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug           text UNIQUE NOT NULL,
    title_ar       text NOT NULL,
    category       text,
    use_case_md    text,
    intro_md       text,
    body_md        text,
    legal_basis    jsonb,
    docx_path      text,
    review_status  text NOT NULL DEFAULT 'draft'
                     CHECK (review_status IN ('draft', 'approved')),
    is_published   boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.forms IS
    'نماذج wing content for /forms/{slug} (098). Original AI-drafted + human-'
    'reviewed templates (NOT corpus data). LIABILITY HARD GATE: only rows with '
    'review_status=''approved'' AND is_published=true may be served publicly; '
    'draft_forms.py writes ''draft''/false, a human reviewer approves. use_case_md '
    '+ intro_md are free (SEO layer); body_md + docx_path are gated.';
COMMENT ON COLUMN public.forms.slug IS
    'Stable URL key for /forms/{slug} (unique, never recomputed).';
COMMENT ON COLUMN public.forms.category IS
    'Optional grouping (الفئة) for hub filtering / breadcrumbs.';
COMMENT ON COLUMN public.forms.use_case_md IS
    'متى تستخدمه — free/SEO layer markdown (the ranking food).';
COMMENT ON COLUMN public.forms.intro_md IS
    'Intro / شرح — free markdown.';
COMMENT ON COLUMN public.forms.body_md IS
    'The template body markdown — GATED (signup carrot).';
COMMENT ON COLUMN public.forms.legal_basis IS
    'الأساس النظامي: jsonb array [{regulation_id, article_no, label}] linking into '
    'the المواد (mesh into seo_articles / regulations).';
COMMENT ON COLUMN public.forms.docx_path IS
    'Supabase Storage path of the GATED downloadable (docx/pdf), served via the '
    'PDF/download proxy.';
COMMENT ON COLUMN public.forms.review_status IS
    'draft = AI draft awaiting human legal review; approved = human-reviewed. '
    'Half of the publish gate (with is_published).';
COMMENT ON COLUMN public.forms.is_published IS
    'Publish switch. A form is public ONLY when review_status=''approved'' AND '
    'is_published=true.';

------------------------------------------------------------------------
-- 2. Publish-gate partial index (the only rows ever served publicly).
--    Predicate matches the hard gate exactly; ordered for hub listing.
------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_forms_published
    ON public.forms (created_at DESC)
    WHERE review_status = 'approved' AND is_published;

------------------------------------------------------------------------
-- 3. RLS: deny-all, service-role only (no policies).
------------------------------------------------------------------------
ALTER TABLE public.forms ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.forms FROM anon, authenticated;

COMMIT;
