-- ============================================================================
-- Migration 215 — attachment pages + segment maps + segments.
--
-- Source: .claude/plans/attachment_segmentation.md §4.5 (owner decision D10,
--         2026-11-11). Code: agents/memory/ocr_extractor (page rows),
--         agents/memory/attachment_segmenter (maps + segments).
--
-- WHAT IT STORES ──────────────────────────────────────────────────────────────
-- Beside workspace_items.content_md, which stays exactly as OCR builds it:
--   * attachment_pages — one row per OCR page: the page's markdown as Mistral
--     returned it and its char offset inside workspace_items.content_md (pages
--     are joined with a blank line, then the whole text is stripped). Written by
--     the OCR step while ATTACHMENT_SEGMENTER_ENABLED is on. Pre-launch uploads
--     have none (their page breaks are gone).
--   * attachment_segment_maps — one row per segmented attachment: what belongs
--     to the whole document (rule / prompt versions, model, split mode, the
--     documents inside the file, key paragraphs) and content_chars, the
--     len(content_md) the map was made from. A map whose content_chars differs
--     from today's len(content_md) is STALE: readers ignore it, the segmenter
--     re-runs. bundle_id / bundle_size (nullable): set when the file was
--     segmented in a BUNDLE — the small files (each <= 5,000 chars, 2+ of them,
--     together > 5,000) of one user message mapped in one run (owner,
--     2026-11-12). Every member still has its own map and segments, cut back to
--     its own text; bundle_id (uuid5 of the message id) is shared by the members.
--   * attachment_segments — one row per section, document order (seq): its
--     paragraph range, page range, char range inside content_md, label, value
--     (metadata / core / supporting / boilerplate / repeat), Arabic description
--     and its ORIGINAL text, sliced from content_md by code (never model text).
--   All text is stored REAL (store-real invariant, وضع السرية): the segmenter
--   decodes its model output before writing; consumers encode what they load.
--
-- LIFECYCLE ───────────────────────────────────────────────────────────────────
--   * Hard delete of the workspace_items row → pages and maps cascade; segments
--     cascade from their map. Verified live 2026-11-11: purge_user_data(uuid)
--     runs `DELETE FROM public.workspace_items WHERE user_id = p_user_id`, so an
--     account purge removes all three. user_id also cascades from users(user_id)
--     (belt and braces for the terminal auth-user delete).
--   * Soft delete (workspace_items.deleted_at) leaves the rows; readers filter
--     through the workspace_items join. The retention sweep purges the stored
--     file only and never touches these tables.
--   * Replacing a map = delete the map row (segments cascade) + insert.
--
-- SECURITY / RLS ──────────────────────────────────────────────────────────────
--   RLS ENABLED on all three (absolute rule 6) with owner-scoped SELECT policies
--   in the repo's style, but NO grant to anon / authenticated (migration 195
--   stance): the backend reads and writes them as service_role (bypasses RLS)
--   and the browser never touches them. The policies are inert without a grant;
--   they are there so a deliberate GRANT later restores owner scoping, not an
--   open table. No client INSERT / UPDATE / DELETE policy on purpose.
--
-- Verified live against prod dwgghvxogtwyaxmbgjod on 2026-11-11 (read-only):
--   * no relation named attachment_pages / attachment_segment_maps /
--     attachment_segments (nor any public.attachment% / %segment% relation);
--   * workspace_items.item_id is uuid, PRIMARY KEY (artifacts_pkey);
--     workspace_items.user_id uuid NOT NULL → users(user_id) ON DELETE CASCADE;
--   * workspace_items policies read
--     user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()));
--   * highest numbered migration recorded in schema_migrations: 210 (211/212
--     were applied by hand; 212's CHECK is live). No 215 anywhere.
--
-- Dependencies: workspace_items (artifacts), users.
-- IDEMPOTENT: CREATE TABLE / INDEX IF NOT EXISTS, DROP POLICY IF EXISTS +
-- CREATE POLICY; ENABLE RLS, REVOKE and COMMENT are naturally re-runnable.
-- ROLLBACK: DROP TABLE public.attachment_segments, public.attachment_segment_maps,
--           public.attachment_pages;  (no other object depends on them)
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── attachment_pages ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_pages (
    item_id     uuid        NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    page_no     integer     NOT NULL,
    content_md  text        NOT NULL DEFAULT '',   -- the page as OCR returned it
    char_start  integer     NOT NULL,              -- offset inside workspace_items.content_md
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attachment_pages_pkey PRIMARY KEY (item_id, page_no),
    CONSTRAINT attachment_pages_page_no_chk CHECK (page_no >= 1),
    CONSTRAINT attachment_pages_char_start_chk CHECK (char_start >= 0)
);

-- ── attachment_segment_maps ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_segment_maps (
    item_id                 uuid        PRIMARY KEY REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    user_id                 uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    paragraph_rule_version  text        NOT NULL,
    prompt_version          text        NOT NULL,
    model                   text,
    split                   boolean     NOT NULL DEFAULT false,
    documents_in_file       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    key_paragraphs          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    content_chars           integer     NOT NULL,   -- len(content_md) the map was made from
    -- Bundles (owner, 2026-11-12): the small files of one message segmented in ONE
    -- run. Every member keeps its own map; these two say which run made it.
    -- NULL = the file was segmented on its own.
    bundle_id               uuid,                   -- shared by the members (uuid5 of the message id)
    bundle_size             integer,                -- members in that run
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attachment_segment_maps_content_chars_chk CHECK (content_chars >= 0),
    CONSTRAINT attachment_segment_maps_bundle_chk
        CHECK ((bundle_id IS NULL) = (bundle_size IS NULL) AND (bundle_size IS NULL OR bundle_size >= 1))
);

-- ── attachment_segments ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_segments (
    item_id     uuid    NOT NULL REFERENCES public.attachment_segment_maps(item_id) ON DELETE CASCADE,
    seq         integer NOT NULL,                  -- document order, 1-based
    from_p      integer NOT NULL,                  -- paragraph numbers (P001 = 1), inclusive
    to_p        integer NOT NULL,
    page_from   integer,                           -- NULL for pre-launch uploads (no page rows)
    page_to     integer,
    char_start  integer NOT NULL,                  -- offsets inside workspace_items.content_md
    char_end    integer NOT NULL,
    label       text    NOT NULL,
    value       text    NOT NULL,
    what        text    NOT NULL DEFAULT '',       -- Arabic description, stored real
    content_md  text    NOT NULL DEFAULT '',       -- the section's original text
    CONSTRAINT attachment_segments_pkey PRIMARY KEY (item_id, seq),
    CONSTRAINT attachment_segments_value_chk
        CHECK (value IN ('metadata', 'core', 'supporting', 'boilerplate', 'repeat')),
    CONSTRAINT attachment_segments_range_chk CHECK (from_p >= 1 AND to_p >= from_p),
    CONSTRAINT attachment_segments_chars_chk CHECK (char_start >= 0 AND char_end >= char_start)
);

-- ── indexes ─────────────────────────────────────────────────────────────────
-- Reads are by item_id (served by the three primary keys). user_id carries the
-- account-level FK cascade and any per-user sweep; index it so the cascade on a
-- users delete does not scan.
CREATE INDEX IF NOT EXISTS attachment_pages_user_id_idx
    ON public.attachment_pages (user_id);
CREATE INDEX IF NOT EXISTS attachment_segment_maps_user_id_idx
    ON public.attachment_segment_maps (user_id);
-- "every map of this bundle" (re-runs, telemetry); most rows are solo (NULL).
CREATE INDEX IF NOT EXISTS attachment_segment_maps_bundle_id_idx
    ON public.attachment_segment_maps (bundle_id) WHERE bundle_id IS NOT NULL;

-- ── RLS + grants ────────────────────────────────────────────────────────────
ALTER TABLE public.attachment_pages        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_segment_maps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_segments     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attachment_pages_select_own ON public.attachment_pages;
CREATE POLICY attachment_pages_select_own ON public.attachment_pages FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS attachment_segment_maps_select_own ON public.attachment_segment_maps;
CREATE POLICY attachment_segment_maps_select_own ON public.attachment_segment_maps FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- Segments carry no user_id: ownership comes from their map.
DROP POLICY IF EXISTS attachment_segments_select_own ON public.attachment_segments;
CREATE POLICY attachment_segments_select_own ON public.attachment_segments FOR SELECT
    USING (EXISTS (
        SELECT 1
          FROM public.attachment_segment_maps m
         WHERE m.item_id = attachment_segments.item_id
           AND m.user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()))
    ));

REVOKE ALL ON TABLE public.attachment_pages        FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_segment_maps FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_segments     FROM anon, authenticated;

-- service_role (the backend) must be able to read and write all three. The live
-- default privileges for postgres-owned tables already grant it; this makes the
-- file correct on its own.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_pages        TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_segment_maps TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_segments     TO service_role;

COMMENT ON TABLE public.attachment_pages IS
    'One row per OCR page of an attachment (215): the page markdown as OCR returned it and its char offset inside workspace_items.content_md. Service-role only (RLS on, no client grants). Cascades with the workspace item.';
COMMENT ON TABLE public.attachment_segment_maps IS
    'One row per segmented attachment (215): versions, model, split mode, documents inside the file, key paragraphs, and content_chars = len(content_md) the map was made from (differs → stale, ignored). Service-role only.';
COMMENT ON COLUMN public.attachment_segment_maps.bundle_id IS
    'Set when the file was segmented in a bundle (the small files of one user message in one run, owner 2026-11-12): uuid5 of the message id, shared by the members. NULL = segmented on its own.';
COMMENT ON COLUMN public.attachment_segment_maps.bundle_size IS
    'Members in the bundle run that made this map (NULL with bundle_id).';
COMMENT ON TABLE public.attachment_segments IS
    'One row per section of a segmented attachment, document order (215): paragraph / page / char ranges, label, value (metadata|core|supporting|boilerplate|repeat), Arabic description and the original text sliced from content_md. Stored real. Service-role only; cascades from its map.';

-- ── self-tests ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_rls    int;
    v_left   int;
    v_svc    int;
    v_casc   int;
BEGIN
    SELECT count(*) INTO v_rls
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname IN ('attachment_pages', 'attachment_segment_maps', 'attachment_segments')
       AND c.relrowsecurity;
    IF v_rls <> 3 THEN
        RAISE EXCEPTION '215: RLS must be enabled on all three tables (found % of 3)', v_rls;
    END IF;

    SELECT count(*) INTO v_left
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_pages', 'attachment_segment_maps', 'attachment_segments')
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_left <> 0 THEN
        RAISE EXCEPTION '215: % client-role privileges on the attachment segment tables', v_left;
    END IF;

    SELECT count(DISTINCT table_name) INTO v_svc
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_pages', 'attachment_segment_maps', 'attachment_segments')
       AND grantee = 'service_role'
       AND privilege_type = 'DELETE';
    IF v_svc <> 3 THEN
        RAISE EXCEPTION '215: service_role must hold DML on all three tables (found % of 3)', v_svc;
    END IF;

    -- The purge / delete story rests on these three cascades.
    SELECT count(*) INTO v_casc
      FROM pg_constraint
     WHERE contype = 'f'
       AND confdeltype = 'c'
       AND ((conrelid = 'public.attachment_pages'::regclass
             AND confrelid = 'public.workspace_items'::regclass)
         OR (conrelid = 'public.attachment_segment_maps'::regclass
             AND confrelid = 'public.workspace_items'::regclass)
         OR (conrelid = 'public.attachment_segments'::regclass
             AND confrelid = 'public.attachment_segment_maps'::regclass));
    IF v_casc <> 3 THEN
        RAISE EXCEPTION '215: expected 3 ON DELETE CASCADE foreign keys to the item / map (found %)', v_casc;
    END IF;

    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'attachment_segment_maps'
           AND column_name IN ('bundle_id', 'bundle_size')) <> 2 THEN
        RAISE EXCEPTION '215: attachment_segment_maps lacks bundle_id / bundle_size';
    END IF;

    RAISE NOTICE '215 OK: 3 tables, RLS on, client roles revoked, service_role DML, cascades in place, bundle columns';
END $$;

COMMIT;
