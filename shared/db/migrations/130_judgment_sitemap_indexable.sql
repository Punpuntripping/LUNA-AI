-- ============================================================================
-- Migration 130 — `seo_item_meta.indexable`: publishing ≠ indexing
-- Plan: .claude/plans/library_court_sections_publish_ramp.md §2.3 (the courts
--       carve-out this generalises) · project_judgments_wing (the PDPL gate)
--
-- WHY A SECOND FLAG EXISTS AT ALL.
-- Every wing until now had exactly one state bit: `seo_item_meta.slug`. A slug
-- means the page is SERVABLE, and because servable and indexable were the same
-- thing everywhere, the sitemap feed could read the slug and stop. Judgments
-- break that identity. All 10,000 published rulings must stay servable — they
-- are what the 12 court sections paginate over, and un-publishing down to 3,000
-- would empty those sections — while only a curated subset may be handed to a
-- crawler. One column cannot hold two answers, so this adds the second.
--
--   slug IS NOT NULL  →  the wing can serve this page
--   indexable         →  a crawler may have it
--
-- The two are independent by design. `indexable` on an unslugged row is
-- meaningless but harmless: the sitemap feed joins on the slug first, so a row
-- with no slug can never reach a <loc> whatever this column says.
--
-- ⚠ FAIL-CLOSED SEEDING, AND WHY THE DEFAULT LIES.
-- The column DEFAULTs true so that regulations / articles / circulars / forms —
-- wings where published HAS always meant indexable — keep their exact present
-- behaviour with no backfill and no code change at their call sites. Judgments
-- are then explicitly flipped to false for every row, in this same migration,
-- BEFORE anything can read the column. That ordering is the point: between this
-- migration and the selector run there is a window in which the backend may
-- already be serving the judgments sitemap section, and in that window the
-- honest answer is "nothing is indexable yet", not "all 10,000 are". A default
-- of true with no explicit flip would publish the entire wing to Google on
-- deploy — the precise outcome the PDPL gate exists to prevent.
--
-- Re-running this migration is safe and does NOT re-close the gate: the seeding
-- UPDATE fires only in the branch that CREATES the column, so a replay against
-- a database where the selector has already marked 3,000 rulings indexable is a
-- no-op rather than a silent de-indexing of 3,000 live URLs.
--
-- ⚠ `public.cases` IS PIPELINE-OWNED — never ALTER it, never write to it. The
-- risk view below only READS it. Same rule as 123/124.
-- ============================================================================

-- ── 1. the flag ────────────────────────────────────────────────────────────
-- Add-and-seed in ONE branch, taken only when the column does not yet exist.
-- Splitting them (ADD COLUMN IF NOT EXISTS, then an unconditional UPDATE) would
-- make a replay wipe whatever the selector had marked; keeping them together
-- makes "close the judgment wing" a property of creating the column, which is
-- the only moment it is the right answer.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'seo_item_meta'
           AND column_name  = 'indexable'
    ) THEN
        ALTER TABLE public.seo_item_meta
            ADD COLUMN indexable boolean NOT NULL DEFAULT true;

        UPDATE public.seo_item_meta
           SET indexable = false
         WHERE content_type = 'judgment';
    END IF;
END $$;

COMMENT ON COLUMN public.seo_item_meta.indexable IS
    'May a crawler have this page. Independent of `slug` (= is it servable). '
    'True for every wing where published has always meant indexable; judgments '
    'are curated — see migration 130 and scripts/build_judgment_slugs.py --indexable.';

-- The sitemap feed's exact access path: filter `content_type = ? AND slug IS
-- NOT NULL AND indexable`, then `ORDER BY updated_at DESC, content_id` and
-- range-page it. The sort columns are IN the index in that order so a 3,000-row
-- section pages without a sort node. Partial on both predicates, so the index
-- holds only rows that can actually produce a <loc> — ~3,000 of the sidecar's
-- ~20,000 rows once the selector has run.
CREATE INDEX IF NOT EXISTS seo_item_meta_indexable_idx
    ON public.seo_item_meta (content_type, updated_at DESC, content_id)
 WHERE indexable AND slug IS NOT NULL;

-- ── 2. the PDPL risk view ──────────────────────────────────────────────────
-- The precondition on the indexable set: a ruling whose text still names a
-- person by an identifier does not get handed to a crawler, whatever the
-- diversity selector thinks of it.
--
-- ⚠ WHY THE TEN-DIGIT TEST IS NARROW. An earlier read of this corpus flagged
-- 3,987 of the 10,000 published rulings on a bare `[0-9]{10}`, which read as
-- "40% of the wing carries an ID". It does not. Saudi COMMERCIAL REGISTRATION
-- numbers are also ten digits and appear in nearly every commercial ruling,
-- and a company's CR number is not personal data. A Saudi national ID starts
-- with 1 (citizen) or 2 (resident), so anchoring on that first digit — with
-- explicit non-digit boundaries so a 12-digit invoice number cannot match a
-- 10-digit window inside itself — drops the flag to 74 rows. That is the real
-- number, and it is small enough to exclude outright rather than redact.
--
-- The phrase test is the broader net and carries most of the weight (1,596
-- rows): a ruling that says «رقم الهوية» is discussing an identity document
-- even when the digits were already stripped upstream.
--
-- This view is a SEQ SCAN over ~330 MB of judgment text. It is read ONCE per
-- selector run, by a service-role script, and must never be reachable from a
-- request path — hence the revokes below. Do not join it into a hub lister.
CREATE OR REPLACE VIEW public.library_judgment_pdpl_risk AS
SELECT
    c.id::text                                                  AS content_id,
    (c.content ~ '(^|[^0-9])[12][0-9]{9}([^0-9]|$)')            AS national_id_shaped,
    (c.content ~ 'رقم الهوية|الهوية الوطنية|السجل المدني|رقم الإقامة|جواز السفر')
                                                                AS identity_phrase,
    (
        c.content ~ '(^|[^0-9])[12][0-9]{9}([^0-9]|$)'
        OR c.content ~ 'رقم الهوية|الهوية الوطنية|السجل المدني|رقم الإقامة|جواز السفر'
    )                                                           AS at_risk
FROM public.cases c;

COMMENT ON VIEW public.library_judgment_pdpl_risk IS
    'Per-judgment PDPL risk markers, derived from cases.content at read time so '
    'a corpus re-ingest can never leave it stale. Service-role only — seq-scans '
    'the whole judgment corpus; never join into a request path.';

-- Service-role only. `anon` and `authenticated` must not be able to ask which
-- rulings mention an identity document — that question is itself a probe.
REVOKE ALL ON public.library_judgment_pdpl_risk FROM PUBLIC;
REVOKE ALL ON public.library_judgment_pdpl_risk FROM anon, authenticated;
GRANT SELECT ON public.library_judgment_pdpl_risk TO service_role;
