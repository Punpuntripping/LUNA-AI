-- ============================================================================
-- Migration 231 — attachment citations: what an uploaded file cites, and which
--                 official article each citation was tied to.
--
-- Source: .claude/plans/completion_search.md §5, owner decisions C14–C20, C22
--         (2026-11-21): a model-free scan lists every «المادة N» mention in an
--         attachment; each citation is resolved to an official article; the
--         article is then printed under the attachment SEGMENT that cites it.
--         C26 (2026-11-23): code decides what it can prove; what is left goes to
--         the Completion Search agent, which fetches the article (decided_by).
--         The owner chose a new table over a column on attachment_segments (C20).
--
-- WHAT IT STORES ─────────────────────────────────────────────────────────────
--   attachment_citation_scans   one row per scanned file — the versions the scan
--       ran with, the length of the text it read (a changed text redoes the scan)
--       and the counts.
--   attachment_citations        one row per citation: one article number at one
--       place in the file. POSITIONS, not text: char_start / char_end are offsets
--       into user_files.content_md, and that position is the link to the segment
--       (a citation belongs to the segment whose char_start <= position <
--       char_end). Nothing is written on attachment_segments, so a re-segmentation
--       — which deletes and re-inserts every segment row — never breaks the link.
--       law_as_written keeps the few words that name the law (at most 8).
--
--   Both hang on the FILE (user_files.file_id), the key attachment_segment_maps
--   and helper_runs moved to in 223. The column is named item_id like theirs.
--   Deleting the file removes its rows; removing a conversation's pointer to the
--   file does not.
--
--   regulation_id / article_id point at the library (regulations_v2 /
--   articles_v2). articles_v2 is a view, so they carry no foreign key: the reader
--   treats a missing article as "no longer pinned".
--
-- STATUS ──────────────────────────────────────────────────────────────────────
--   pinned          the official article is tied to this citation and delivered
--                   (decided_by says who tied it: 'code' or 'model')
--   medium          law and article found, the link to the law is a guess the
--                   agent did not settle — stored, never delivered
--   not_in_library  the law is not in our data (C17)
--   other_target    the article belongs to something that is not a law («من العقد»)
--   unresolved      everything else; `reason` says why
--
-- SECURITY / RLS ──────────────────────────────────────────────────────────────
--   RLS ENABLED on both (absolute rule 6). One owner-scoped SELECT policy each
--   (citations inherit ownership from their scan row). No client write policy,
--   REVOKE ALL from anon / authenticated (the 195 stance): the backend reads and
--   writes as service_role and the browser never touches these tables.
--
-- VERIFIED LIVE (prod dwgghvxogtwyaxmbgjod, 2026-11-22):
--   * user_files.file_id uuid PK; users.user_id uuid PK.
--   * attachment_segment_maps.item_id → user_files(file_id) ON DELETE CASCADE.
--   * regulations_v2.id, articles_v2.id are uuid.
--   * no relation named attachment_citation_scans / attachment_citations.
--
-- NUMBERING: master holds 227; the angle-map worktree holds 228; this plan uses
--   229 (enum value), 230 (the card), 231 (this file). Re-check on the day.
--
-- Dependencies: user_files (221), users.
-- IDEMPOTENT: CREATE TABLE / INDEX IF NOT EXISTS, DROP POLICY IF EXISTS +
--   CREATE POLICY; ENABLE RLS, REVOKE, GRANT and COMMENT are re-runnable.
-- ROLLBACK: DROP TABLE public.attachment_citations, public.attachment_citation_scans;
--           (no other object depends on them)
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── attachment_citation_scans ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_citation_scans (
    item_id                 uuid        PRIMARY KEY REFERENCES public.user_files(file_id) ON DELETE CASCADE,
    user_id                 uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    scan_rule_version       text        NOT NULL,
    paragraph_rule_version  text        NOT NULL,
    doc_type                text,                   -- the type the scan ran with (contracts are never scanned)
    content_chars           integer     NOT NULL,   -- len(content_md) the scan read
    mentions                integer     NOT NULL DEFAULT 0,   -- citing mentions (the file's own headings left out)
    headings                integer     NOT NULL DEFAULT 0,   -- the file's own clause headings, never resolved
    pinned                  integer     NOT NULL DEFAULT 0,
    medium                  integer     NOT NULL DEFAULT 0,
    not_in_library          integer     NOT NULL DEFAULT 0,
    sent_to_agent           integer     NOT NULL DEFAULT 0,   -- citations handed to the Completion Search agent
    pinned_by_agent         integer     NOT NULL DEFAULT 0,   -- of `pinned`, those the agent's fetch tied
    agent_failed            integer     NOT NULL DEFAULT 0,   -- citations whose agent request failed (left as code decided)
    model                   text,                   -- the agent's model slot; NULL when code decided every citation
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attachment_citation_scans_chars_chk CHECK (content_chars >= 0),
    CONSTRAINT attachment_citation_scans_counts_chk
        CHECK (mentions >= 0 AND headings >= 0 AND pinned >= 0 AND medium >= 0 AND not_in_library >= 0
               AND sent_to_agent >= 0 AND pinned_by_agent >= 0 AND agent_failed >= 0
               AND pinned_by_agent <= pinned)
);

-- ── attachment_citations ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_citations (
    item_id         uuid    NOT NULL REFERENCES public.attachment_citation_scans(item_id) ON DELETE CASCADE,
    seq             integer NOT NULL,              -- document order, 1-based
    char_start      integer NOT NULL,              -- the mention, offsets inside user_files.content_md
    char_end        integer NOT NULL,
    para_n          integer NOT NULL,              -- paragraph number under paragraph_rule_version (forensic)
    sentence_start  integer NOT NULL,              -- the sentence around it — positions only, never its text
    sentence_end    integer NOT NULL,
    article_number  text    NOT NULL,              -- Latin digits, as the library keys it
    law_as_written  text    NOT NULL DEFAULT '',   -- the words that name the law in the file
    basis           text    NOT NULL,              -- how the mention is tied to its law (the scan's fact)
    decided_by      text    NOT NULL DEFAULT 'code',
    status          text    NOT NULL,
    reason          text    NOT NULL DEFAULT '',
    regulation_id   uuid,                          -- NULL unless resolved
    article_id      uuid,
    CONSTRAINT attachment_citations_pkey PRIMARY KEY (item_id, seq),
    CONSTRAINT attachment_citations_seq_chk CHECK (seq >= 1),
    CONSTRAINT attachment_citations_chars_chk
        CHECK (char_start >= 0 AND char_end >= char_start
               AND sentence_start >= 0 AND sentence_end >= sentence_start),
    CONSTRAINT attachment_citations_status_chk
        CHECK (status IN ('pinned', 'medium', 'not_in_library', 'other_target', 'unresolved')),
    CONSTRAINT attachment_citations_decided_by_chk CHECK (decided_by IN ('code', 'model')),
    -- a pinned or medium citation always names its article
    CONSTRAINT attachment_citations_article_chk
        CHECK (status NOT IN ('pinned', 'medium') OR (regulation_id IS NOT NULL AND article_id IS NOT NULL))
);

-- ── indexes ─────────────────────────────────────────────────────────────────
-- Delivery reads "the pinned citations of this file" — a small part of the rows.
CREATE INDEX IF NOT EXISTS attachment_citations_pinned_idx
    ON public.attachment_citations (item_id) WHERE status = 'pinned';
-- The account-level cascade on a users delete must not scan.
CREATE INDEX IF NOT EXISTS attachment_citation_scans_user_id_idx
    ON public.attachment_citation_scans (user_id);

-- ── RLS + grants ────────────────────────────────────────────────────────────
ALTER TABLE public.attachment_citation_scans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_citations      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attachment_citation_scans_select_own ON public.attachment_citation_scans;
CREATE POLICY attachment_citation_scans_select_own ON public.attachment_citation_scans FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- Citations carry no user_id: ownership comes from their scan row.
DROP POLICY IF EXISTS attachment_citations_select_own ON public.attachment_citations;
CREATE POLICY attachment_citations_select_own ON public.attachment_citations FOR SELECT
    USING (EXISTS (
        SELECT 1
          FROM public.attachment_citation_scans s
         WHERE s.item_id = attachment_citations.item_id
           AND s.user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()))
    ));

REVOKE ALL ON TABLE public.attachment_citation_scans FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_citations      FROM anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_citation_scans TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_citations      TO service_role;

COMMENT ON TABLE public.attachment_citation_scans IS
    'One row per scanned file (231): the versions and text length the citation scan ran with, and its counts. item_id is user_files.file_id. Service-role only (RLS on, no client grants). Cascades with the file.';
COMMENT ON TABLE public.attachment_citations IS
    'One row per citation in a file (231): one article number at one character position, the law it was tied to and the official article (regulations_v2 / articles_v2 ids). The position links it to the attachment segment that cites it. Positions only — no copy of the file''s sentences. Service-role only.';
COMMENT ON COLUMN public.attachment_citation_scans.item_id IS 'user_files.file_id — the same key attachment_segment_maps uses since 223.';

-- self-check (before COMMIT, so a dry run proves it too)
DO $$
DECLARE v_n int;
BEGIN
    SELECT count(*) INTO v_n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname IN ('attachment_citation_scans', 'attachment_citations')
       AND c.relrowsecurity;
    IF v_n <> 2 THEN
        RAISE EXCEPTION '231: expected RLS on both citation tables, found % with it', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND confdeltype = 'c'
                    AND conrelid = 'public.attachment_citation_scans'::regclass
                    AND confrelid = 'public.user_files'::regclass) THEN
        RAISE EXCEPTION '231: attachment_citation_scans does not cascade from user_files';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND confdeltype = 'c'
                    AND conrelid = 'public.attachment_citations'::regclass
                    AND confrelid = 'public.attachment_citation_scans'::regclass) THEN
        RAISE EXCEPTION '231: attachment_citations does not cascade from its scan row';
    END IF;
    SELECT count(*) INTO v_n FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name IN ('attachment_citation_scans', 'attachment_citations')
       AND grantee IN ('anon', 'authenticated');
    IF v_n <> 0 THEN
        RAISE EXCEPTION '231: % client grant(s) left on the citation tables', v_n;
    END IF;
    RAISE NOTICE '231 OK: citation tables created, RLS on, cascading from user_files, no client grants';
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
