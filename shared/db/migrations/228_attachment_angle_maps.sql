-- ============================================================================
-- Migration 228 — attachment angle maps («خريطة الأوجه»), ONE PER MESSAGE GROUP:
-- the map, its member attachments, its angles.
--
-- Source: .claude/plans/relationship_map.md §4.6 (storage), D10–D17
--         (2026-11-21 / 22: one map per message — the files a user sends in one
--         message are mapped together, angles may connect their sections).
--         Code: agents/memory/angle_mapper (store.py writes and reads;
--         runner.py calls it; refs.py defines the section refs).
--
-- DEPLOY ORDER: apply to prod BEFORE deploying the code that writes these
-- tables — the angle mapper's runner calls write_angle_map(), so code first =
-- every map fails to store (write_angle_map returns None, the paid call is
-- wasted). Applying it early is harmless: nothing reads the tables until the
-- code is live. Redesigned 2026-11-22 before any apply (the per-attachment
-- version of 2026-11-21, then numbered 227, was never applied anywhere);
-- renumbered 228 the same day — master's 227 is 227_user_files_attach.sql.
--
-- WHAT IT STORES ──────────────────────────────────────────────────────────────
--   * attachment_angle_maps — one row per MESSAGE GROUP (map_id): the message
--     the files came with (message_id, UNIQUE; NULL for a file with no message
--     link — a group of one), its conversation and user, the prompt version and
--     model, the client's side {side, party, basis, confidence, by_document},
--     the context sections every search keeps in view, the gaps
--     [{kind, sections, issue, ocr_suspect}], the core sections in no angle
--     [{section, why}], the documents that belong to another matter
--     [{document, why}], the attachments of the group the mapper LEFT OUT after
--     reading their text (left_out: [{item_id, reason, content_chars,
--     segment_prompt_version, paragraph_rule_version, segmented_at}] — their
--     paragraphs no longer fit their sections, or no sections), tokens, cost and
--     duration.
--   * attachment_angle_map_items — the map's members, D1, D2… (doc_index, upload
--     order) → item_id (UNIQUE: an attachment is in at most one map), and what
--     the map was built from: content_chars = len(content_md), the segment
--     map's prompt / paragraph-rule versions and created_at (segmented_at). Any
--     of them differing from today's → the map is STALE (the reader ignores it,
--     the group is re-mapped); so is a different member set — an attachment of
--     the group that is neither a member nor in left_out at the same versions.
--     The freshness check reads only these versions and the segment-map HEADERS,
--     never the text.
--   * item_id is the CONVERSATION's item (workspace_items), not the file: segment
--     maps hang on user_files (223) and the mapper finds them through
--     workspace_items.file_id, but an angle map belongs to one conversation's use
--     of a file (a library file attached to two conversations — 227_user_files_
--     attach — is in two groups, with two maps).
--   * attachment_angles — one row per angle, most decisive first (seq, 1-based):
--     the side and party it serves, kind (strengthen | attack | answer), title,
--     argument, the sections it turns on, ONE legal question, strength
--     (strong | medium | weak — the map's first signal; the searches judge it),
--     what in the file cuts against it, and ocr_suspect (angles-v5: the angle
--     rests on a figure, date, number or name the OCR could have misread).
--   SECTION REFS: every section reference (angles.sections, context,
--   gaps[].sections, no_angle[].section) is a string "D<doc_index>.S<seq>" —
--   attachment_segments.seq of the member with that doc_index. Stored as jsonb
--   arrays of strings.
--   The الموكّل / الخصم / محايد labels are NOT stored (plan §4.4): code computes
--   them from the sections' owners when a view is built, because the planner
--   may correct the client's side at search time (D11).
--   All text is stored REAL (store-real invariant, وضع السرية): the mapper
--   decodes its model output before writing; consumers encode what they load.
--
-- LIFECYCLE ───────────────────────────────────────────────────────────────────
--   * A map is found from ANY member: attachment_angle_map_items.item_id
--     (UNIQUE) → map_id.
--   * Hard delete of a workspace_items row → its member row cascades (the map
--     then has a different member set: stale). Items and angles cascade from
--     their map. The map cascades from its conversation and from users(user_id)
--     (account purge / terminal auth-user delete); a hard-deleted message sets
--     message_id NULL (the map stays reachable from its members).
--   * Soft delete (workspace_items.deleted_at) leaves the rows; the reader's
--     freshness check drops a deleted member (stale).
--   * Replacing a map (store.write_angle_map): insert the new map (message_id
--     NULL) + its angles → delete the group's previous maps (the message's, and
--     any map holding a member) → set message_id → insert the members. Readers
--     reach a map only through its members, so it becomes visible complete. Two
--     writers for one group: the second hits UNIQUE(message_id) or
--     UNIQUE(item_id) and drops its own row. A writer killed mid-way leaves an
--     orphan map with no members — unreachable, removed with its conversation.
--   * No foreign key to attachment_segments: a re-segmented file makes the map
--     stale through the member row's versions, which the reader checks.
--
-- SECURITY / RLS ──────────────────────────────────────────────────────────────
--   RLS ENABLED on all three (absolute rule 6) with owner-scoped SELECT
--   policies in the repo's style, but NO grant to anon / authenticated
--   (migration 195 / 215 stance): the backend reads and writes them as
--   service_role (bypasses RLS) and the browser never touches them. The
--   policies are inert without a grant; they are there so a deliberate GRANT
--   later restores owner scoping, not an open table. No client INSERT / UPDATE
--   / DELETE policy on purpose.
--
-- NOT verified live yet (2026-11-22, written in the feat/angle-map worktree):
--   highest migration file on master is 227_user_files_attach.sql; no 228 in
--   any local checkout. Before applying, confirm against prod that no
--   public.attachment_angle% relation exists, that 215 (attachment_segment_maps)
--   is live, and that messages.message_id / conversations.conversation_id are
--   the uuid primary keys of 007 / 008 (migration drift: the folder is not the
--   truth).
--
-- Dependencies: workspace_items, users, conversations, messages, 215 (the
--   segment tables the map is built from — no FK to them), 223 (segment maps
--   keyed by file), 227 (library files attached as new items). gen_random_uuid()
--   is core since PostgreSQL 13.
-- IDEMPOTENT: CREATE TABLE / INDEX IF NOT EXISTS, DROP POLICY IF EXISTS +
--   CREATE POLICY; ENABLE RLS, REVOKE, GRANT and COMMENT are re-runnable.
-- ROLLBACK: DROP TABLE public.attachment_angles, public.attachment_angle_map_items,
--           public.attachment_angle_maps;  (no other object depends on them)
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── attachment_angle_maps ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_angle_maps (
    map_id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- the message the group's files came with; NULL = a file with no message link
    message_id              uuid        REFERENCES public.messages(message_id) ON DELETE SET NULL,
    conversation_id         uuid        REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,
    user_id                 uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    prompt_version          text        NOT NULL,                       -- 'angles-v5' …
    model                   text,
    client                  jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- {side, party, basis, confidence, by_document}
    context                 jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- ["D1.S3", …]
    gaps                    jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{kind, sections, issue, ocr_suspect}]
    no_angle                jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{section, why}]
    unrelated_documents     jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{document, why}]
    left_out                jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{item_id, reason, content_chars, segment_prompt_version, paragraph_rule_version, segmented_at}]
    input_tokens            integer     NOT NULL DEFAULT 0,
    output_tokens           integer     NOT NULL DEFAULT 0,
    reasoning_tokens        integer     NOT NULL DEFAULT 0,
    cost_usd                numeric     NOT NULL DEFAULT 0,
    duration_ms             integer     NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attachment_angle_maps_message_id_key UNIQUE (message_id),
    CONSTRAINT attachment_angle_maps_json_chk CHECK (
        jsonb_typeof(client) = 'object'
        AND jsonb_typeof(context) = 'array'
        AND jsonb_typeof(gaps) = 'array'
        AND jsonb_typeof(no_angle) = 'array'
        AND jsonb_typeof(unrelated_documents) = 'array'
        AND jsonb_typeof(left_out) = 'array'
    )
);

-- ── attachment_angle_map_items ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_angle_map_items (
    map_id                  uuid        NOT NULL REFERENCES public.attachment_angle_maps(map_id) ON DELETE CASCADE,
    item_id                 uuid        NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    doc_index               integer     NOT NULL,   -- D1 = 1, upload order inside the group
    content_chars           integer     NOT NULL,   -- len(content_md) the map was made from
    segment_prompt_version  text        NOT NULL,   -- the segment map it was built from
    paragraph_rule_version  text        NOT NULL,
    segmented_at            timestamptz,            -- that segment map's created_at
    CONSTRAINT attachment_angle_map_items_pkey PRIMARY KEY (map_id, doc_index),
    CONSTRAINT attachment_angle_map_items_item_id_key UNIQUE (item_id),
    CONSTRAINT attachment_angle_map_items_doc_index_chk CHECK (doc_index >= 1),
    CONSTRAINT attachment_angle_map_items_content_chars_chk CHECK (content_chars >= 0)
);

-- ── attachment_angles ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_angles (
    map_id          uuid      NOT NULL REFERENCES public.attachment_angle_maps(map_id) ON DELETE CASCADE,
    seq             integer   NOT NULL,                -- most decisive first, 1-based
    for_side        text      NOT NULL DEFAULT '',     -- owner key (claimant, respondent, …)
    for_party       text      NOT NULL DEFAULT '',     -- that party as the file names it
    kind            text      NOT NULL,
    title           text      NOT NULL DEFAULT '',
    argument        text      NOT NULL DEFAULT '',
    sections        jsonb     NOT NULL DEFAULT '[]'::jsonb,   -- ["D1.S7", "D2.S3"], the model's order
    legal_question  text      NOT NULL DEFAULT '',
    strength        text      NOT NULL,
    weakness        text      NOT NULL DEFAULT '',     -- '' when nothing cuts against it
    ocr_suspect     boolean   NOT NULL DEFAULT false,  -- rests on a reading the OCR could have got wrong (angles-v5)
    CONSTRAINT attachment_angles_pkey PRIMARY KEY (map_id, seq),
    CONSTRAINT attachment_angles_seq_chk CHECK (seq >= 1),
    CONSTRAINT attachment_angles_kind_chk CHECK (kind IN ('strengthen', 'attack', 'answer')),
    CONSTRAINT attachment_angles_strength_chk CHECK (strength IN ('strong', 'medium', 'weak')),
    CONSTRAINT attachment_angles_sections_chk CHECK (jsonb_typeof(sections) = 'array')
);

-- ── indexes ─────────────────────────────────────────────────────────────────
-- Reads: by item (UNIQUE item_id index), then by map_id (the primary keys);
-- by message (UNIQUE message_id index). user_id and conversation_id carry the
-- FK cascades and any per-user / per-conversation sweep: index them so a
-- delete upstream does not scan.
CREATE INDEX IF NOT EXISTS attachment_angle_maps_user_id_idx
    ON public.attachment_angle_maps (user_id);
CREATE INDEX IF NOT EXISTS attachment_angle_maps_conversation_id_idx
    ON public.attachment_angle_maps (conversation_id) WHERE conversation_id IS NOT NULL;

-- ── RLS + grants ────────────────────────────────────────────────────────────
ALTER TABLE public.attachment_angle_maps      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_angle_map_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_angles          ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attachment_angle_maps_select_own ON public.attachment_angle_maps;
CREATE POLICY attachment_angle_maps_select_own ON public.attachment_angle_maps FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- Members and angles carry no user_id: ownership comes from their map.
DROP POLICY IF EXISTS attachment_angle_map_items_select_own ON public.attachment_angle_map_items;
CREATE POLICY attachment_angle_map_items_select_own ON public.attachment_angle_map_items FOR SELECT
    USING (EXISTS (
        SELECT 1
          FROM public.attachment_angle_maps m
         WHERE m.map_id = attachment_angle_map_items.map_id
           AND m.user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()))
    ));

DROP POLICY IF EXISTS attachment_angles_select_own ON public.attachment_angles;
CREATE POLICY attachment_angles_select_own ON public.attachment_angles FOR SELECT
    USING (EXISTS (
        SELECT 1
          FROM public.attachment_angle_maps m
         WHERE m.map_id = attachment_angles.map_id
           AND m.user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()))
    ));

REVOKE ALL ON TABLE public.attachment_angle_maps      FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_angle_map_items FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_angles          FROM anon, authenticated;

-- service_role (the backend) must be able to read and write all three. The
-- live default privileges for postgres-owned tables already grant it; this
-- makes the file correct on its own.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_angle_maps      TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_angle_map_items TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_angles          TO service_role;

COMMENT ON TABLE public.attachment_angle_maps IS
    'One row per angle-mapped MESSAGE GROUP (228, relationship_map.md §4.6, D17): the attachments of one user message mapped together. message_id UNIQUE (NULL = a file with no message link). Prompt version, model, client side, context, gaps, core sections in no angle, documents of another matter, attachments left out after their text was read (left_out, with the versions they were judged on), tokens, cost, duration. Section refs are "D<doc_index>.S<seq>". Stored real. Service-role only (RLS on, no client grants).';
COMMENT ON COLUMN public.attachment_angle_maps.client IS
    '{side, party, basis, confidence, by_document} — side is an owner key (claimant | respondent | prosecution | victim | intervenor | contract_party | unknown), confidence clear | likely | unclear; by_document = [{document "D2", side, party}] where the client has another key / name in that document''s own proceeding.';
COMMENT ON COLUMN public.attachment_angle_maps.context IS
    'Section refs ("D1.S3") every search keeps in view (the parties, the requests, the operative ruling, the main terms).';
COMMENT ON TABLE public.attachment_angle_map_items IS
    'The members of an angle map (228): doc_index (D1 = 1, upload order) → item_id (UNIQUE — an attachment is in at most one map), with what the map was built from (content_chars, segment prompt / paragraph-rule versions, segmented_at). Any difference from today, or a different member set (left_out aside) → stale. Cascades from its map and its workspace item.';
COMMENT ON TABLE public.attachment_angles IS
    'One row per angle of an angle map, most decisive first (228): side + party served, kind (strengthen|attack|answer), title, argument, sections (refs "D1.S7", may span documents), one legal question, strength (strong|medium|weak — a first signal), weakness, ocr_suspect (rests on a reading the OCR could have got wrong). The الموكّل / الخصم / محايد labels are computed by code, never stored. Service-role only; cascades from its map.';

-- ── self-tests ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_rls     int;
    v_left    int;
    v_svc     int;
    v_casc    int;
    v_setnull int;
    v_chk     int;
    v_uniq    int;
BEGIN
    SELECT count(*) INTO v_rls
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname IN ('attachment_angle_maps', 'attachment_angle_map_items', 'attachment_angles')
       AND c.relrowsecurity;
    IF v_rls <> 3 THEN
        RAISE EXCEPTION '228: RLS must be enabled on all three tables (found % of 3)', v_rls;
    END IF;

    SELECT count(*) INTO v_left
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_angle_maps', 'attachment_angle_map_items', 'attachment_angles')
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_left <> 0 THEN
        RAISE EXCEPTION '228: % client-role privileges on the angle map tables', v_left;
    END IF;

    SELECT count(DISTINCT table_name) INTO v_svc
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_angle_maps', 'attachment_angle_map_items', 'attachment_angles')
       AND grantee = 'service_role'
       AND privilege_type = 'DELETE';
    IF v_svc <> 3 THEN
        RAISE EXCEPTION '228: service_role must hold DML on all three tables (found % of 3)', v_svc;
    END IF;

    -- The delete / purge story rests on these cascades: user → map,
    -- conversation → map, map → items, workspace item → items, map → angles.
    SELECT count(*) INTO v_casc
      FROM pg_constraint
     WHERE contype = 'f'
       AND confdeltype = 'c'
       AND ((conrelid = 'public.attachment_angle_maps'::regclass
             AND confrelid IN ('public.users'::regclass, 'public.conversations'::regclass))
         OR (conrelid = 'public.attachment_angle_map_items'::regclass
             AND confrelid IN ('public.attachment_angle_maps'::regclass, 'public.workspace_items'::regclass))
         OR (conrelid = 'public.attachment_angles'::regclass
             AND confrelid = 'public.attachment_angle_maps'::regclass));
    IF v_casc <> 5 THEN
        RAISE EXCEPTION '228: expected 5 ON DELETE CASCADE foreign keys (user → map, conversation → map, map → items, item → items, map → angles), found %', v_casc;
    END IF;

    -- A deleted message must not take the map with it (its members still find it).
    SELECT count(*) INTO v_setnull
      FROM pg_constraint
     WHERE contype = 'f'
       AND confdeltype = 'n'
       AND conrelid = 'public.attachment_angle_maps'::regclass
       AND confrelid = 'public.messages'::regclass;
    IF v_setnull <> 1 THEN
        RAISE EXCEPTION '228: attachment_angle_maps.message_id must be ON DELETE SET NULL (found % such keys)', v_setnull;
    END IF;

    -- One map per message; an attachment in at most one map.
    SELECT count(*) INTO v_uniq
      FROM pg_constraint
     WHERE contype = 'u'
       AND conname IN ('attachment_angle_maps_message_id_key', 'attachment_angle_map_items_item_id_key');
    IF v_uniq <> 2 THEN
        RAISE EXCEPTION '228: UNIQUE (message_id) / UNIQUE (item_id) missing (found % of 2)', v_uniq;
    END IF;

    SELECT count(*) INTO v_chk
      FROM pg_constraint
     WHERE contype = 'c'
       AND conrelid = 'public.attachment_angles'::regclass
       AND conname IN ('attachment_angles_kind_chk', 'attachment_angles_strength_chk');
    IF v_chk <> 2 THEN
        RAISE EXCEPTION '228: attachment_angles lacks its kind / strength CHECKs (found % of 2)', v_chk;
    END IF;

    IF (SELECT count(*) FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname IN ('attachment_angle_maps_user_id_idx', 'attachment_angle_maps_conversation_id_idx')) <> 2 THEN
        RAISE EXCEPTION '228: attachment_angle_maps user_id / conversation_id indexes missing';
    END IF;

    RAISE NOTICE '228 OK: 3 tables, RLS on, client roles revoked, service_role DML, 5 cascades + message SET NULL, UNIQUE message / item, kind/strength checks, indexes';
END $$;

COMMIT;
