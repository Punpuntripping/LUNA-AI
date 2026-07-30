-- 096_topics_taxonomy.sql
-- Date: 2026-07-22
-- Part of SEO Public Library Phase 1 — .claude/plans/seo_public_library.md
--   (§ "Locked decisions" → Taxonomy; § "Phase 1" → Topics).
--
-- Purpose:
--   The SHARED 2-level topic taxonomy used across every content type, powering
--   cross-type /topics/{slug} hubs (نظام + مواد + judgments + services +
--   calculators + blogs, all under one topic) — the internal-linking mesh no
--   Saudi competitor has.
--
--     1. public.topics — the taxonomy nodes. 2 levels via self-referencing
--        parent_id (NULL = top-level topic; non-NULL = sub-topic). slug is the
--        stable URL key (/topics/{slug}); name_ar is the Arabic display label.
--
--     2. public.topic_map — the many-to-many join from a topic to any piece of
--        content in any corpus. content_id is TEXT on purpose: the corpora use
--        mixed key shapes (uuid PKs for cases/services, ref strings like reg_ref
--        for regulations, derived article keys, blog slugs, calculator ids), so
--        a single uuid column could not hold them all. content_type disambiguates
--        which table content_id points into. PK (topic_id, content_type,
--        content_id) makes a mapping unique and re-tagging idempotent.
--
--   NO topic rows are seeded here. Seeding derives topics from the corpora's
--   existing sectors[] arrays (regulations_v2.sectors, services.sectors, …) via
--   a later Python script, not this migration.
--
-- Security / RLS:
--   * Both tables are NEW → RLS ENABLED (app rule). They hold no user data and
--     are read ONLY by the backend service role (public hubs fetch via anon
--     endpoints → service role). No policies (RLS-with-no-policies is
--     default-deny for anon/authenticated); REVOKE ALL added belt-and-suspenders
--     (same deny-all convention as 087_pii_mappings / 095).
--
-- Verified live-state (Supabase MCP, 2026-07-22):
--   * public.topics and public.topic_map do not exist.
--   * gen_random_uuid() available (pgcrypto, 001_extensions; also used by 092).
--
-- Dependencies:
--   - 001_extensions.sql (pgcrypto → gen_random_uuid).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; RLS enable + REVOKE re-runnable.

BEGIN;

------------------------------------------------------------------------
-- 1. Taxonomy nodes (2 levels via parent_id).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.topics (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text UNIQUE NOT NULL,
    name_ar      text NOT NULL,
    parent_id    uuid REFERENCES public.topics(id),
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.topics IS
    'Shared 2-level topic taxonomy for the SEO public library (096). parent_id '
    'NULL = top-level topic, non-NULL = sub-topic. slug = /topics/{slug} URL key. '
    'Rows are seeded from corpus sectors[] by a later Python script, not this '
    'migration.';
COMMENT ON COLUMN public.topics.slug IS
    'Stable URL key for /topics/{slug} (unique, never recomputed).';
COMMENT ON COLUMN public.topics.parent_id IS
    'Parent topic (self-reference). NULL = top-level; non-NULL = sub-topic (2 '
    'levels only by convention).';

------------------------------------------------------------------------
-- 2. Topic → content mapping (cross-corpus, many-to-many).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.topic_map (
    topic_id      uuid NOT NULL REFERENCES public.topics(id) ON DELETE CASCADE,
    content_type  text NOT NULL CHECK (content_type IN (
                      'regulation', 'article', 'judgment', 'circular',
                      'service', 'blog', 'calculator', 'form')),
    content_id    text NOT NULL,
    PRIMARY KEY (topic_id, content_type, content_id)
);

COMMENT ON TABLE public.topic_map IS
    'Cross-corpus topic membership (096). Joins a topic to any content item. '
    'content_id is TEXT because corpora use mixed key shapes (uuid, reg_ref, '
    'derived article keys, blog/calculator slugs); content_type says which table '
    'it points into.';
COMMENT ON COLUMN public.topic_map.content_id IS
    'Item key within the corpus named by content_type. TEXT to hold mixed '
    'uuid/ref key shapes across corpora.';

-- Reverse lookup: "which topics does this item belong to?" (content_type + id).
CREATE INDEX IF NOT EXISTS idx_topic_map_content
    ON public.topic_map (content_type, content_id);

------------------------------------------------------------------------
-- 3. RLS: deny-all, service-role only (no policies).
------------------------------------------------------------------------
ALTER TABLE public.topics    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.topic_map ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.topics    FROM anon, authenticated;
REVOKE ALL ON public.topic_map FROM anon, authenticated;

COMMIT;
