-- ============================================================================
-- Migration 234 — attachment agent edits: what the USER asked to change in an
--                 uploaded file and its angle map («الخيوط»), stored beside the
--                 file, never by rewriting it.
--
-- Source: .claude/plans/attachment_agent.md §3 (storage), owner decisions
--         D7 (edit only what the user named: promote sections — promote only —,
--         change threads / gaps / the client line, fix small extraction mistakes),
--         D12 (text fixes are an OVERLAY; content_md is never rewritten; every
--         edit is recorded and survives a re-segment), D14 (a fix corrects every
--         occurrence), D15 (fixes and promotions follow the FILE into every
--         conversation it is attached to), D20 (free plan: fixes + promotions
--         yes, thread edits no — free users have no angle map), §7 option (a)
--         (a thread edit is made IN PLACE on the map: every conversation that
--         carries that map sees it).
--         Code: shared/attachments/text_corrections.py,
--               shared/attachments/segment_overrides.py (applied inside
--               agents/memory/attachment_segmenter/store.load_segment_maps),
--               agents/memory/angle_mapper/edits.py.
--
-- WHAT IT STORES ─────────────────────────────────────────────────────────────
--   attachment_text_corrections   one row per text fix of a FILE: every
--       wrong_text in user_files.content_md reads as right_text (occurrences =
--       how many there were when the fix was made). Readers apply the active
--       fixes (reverted_at NULL) in created_at order. content_md is NOT changed:
--       its length drives segment-map and angle-map freshness, so a rewrite would
--       re-segment the file, renumber its sections, break its threads and make
--       «بحث موسّع» refuse it. Columns are wrong_text / right_text (RIGHT is a
--       reserved word in PostgreSQL).
--   attachment_segment_overrides  one row per section PROMOTION: the character
--       range [char_start, char_end) of the file's text that loads as 'core'.
--       Keyed by POSITION on the file like the citation tables of 231 — nothing
--       is written on attachment_segments, whose rows a re-segment deletes and
--       re-inserts — so a promotion survives a re-segment. value is 'core' only:
--       a promotion never demotes.
--   attachment_angle_map_edits    the LOG of every edit made in place on an angle
--       map (228): kind, target ("A3", "gap 2", "client"…), before / after (jsonb),
--       the user's words that asked for it (evidence). map_id has NO foreign key
--       on purpose: a re-analysis replaces the map (new map_id) and the log must
--       survive it — the 'snapshot' row a re-analysis writes first holds the old
--       map whole, so it can be restored. undone_at marks an edit put back by an
--       'undo' row (whose target is the undone edit's id).
--
--   Text is stored REAL (store-real invariant, وضع السرية): the code decodes the
--   model's masked output and refuses a write that would leave a fake.
--
-- LIFECYCLE ───────────────────────────────────────────────────────────────────
--   * Fixes and promotions hang on the file: deleting the file (user_files) or
--     the account (users) removes them. Deleting the conversation the fix was
--     asked in does NOT (D15: the fix follows the file) — its conversation_id /
--     message_id become NULL.
--   * A same-owner composite key (file_id, user_id) → user_files (file_id,
--     user_id) (the 221 UNIQUE made for that) keeps a fix on its owner's file.
--   * The map-edit log cascades from the account and from the conversation the
--     edit was asked in (that conversation's words go with it); a hard-deleted
--     message sets message_id NULL.
--   * Revert / undo never delete: they set reverted_at / undone_at.
--
-- SECURITY / RLS ──────────────────────────────────────────────────────────────
--   RLS ENABLED on all three (absolute rule 6) with an owner-scoped SELECT policy
--   each, in the 228 / 231 style; REVOKE ALL from anon / authenticated (the 195
--   stance) and no client write policy: the backend reads and writes as
--   service_role and the browser never touches these tables. The policies are
--   inert without a grant; they are there so a deliberate GRANT later restores
--   owner scoping, not an open table.
--
-- DEPLOY ORDER: apply BEFORE deploying the code that READS these tables. The
--   readers never fail on a missing table (they log once a minute and show the
--   stored values), but every load_segment_maps call would pay a failing request
--   until then. Writes only come from flagged users (ATTACHMENT_AGENT_*).
--
-- NOT verified live (written 2026-11-27 in the feat/attachment-agent worktree,
--   never applied anywhere). Before applying, confirm against prod that no
--   public.attachment_text_corrections / attachment_segment_overrides /
--   attachment_angle_map_edits relation exists, that user_files(file_id) is the
--   uuid PK with UNIQUE (file_id, user_id) (221), and that
--   messages.message_id / conversations.conversation_id are the uuid PKs
--   (migration drift: the folder is not the truth).
--
-- NUMBERING: master holds up to 232b; 233 is taken in two other worktrees
--   (233_workspace_cap_45, 233_pro_t1_a_20_seats). Re-check on the day.
--
-- Dependencies: user_files (221), users, conversations, messages, 228 (the maps
--   the log is about — no FK to them).
-- IDEMPOTENT: CREATE TABLE / INDEX IF NOT EXISTS, DROP POLICY IF EXISTS +
--   CREATE POLICY; ENABLE RLS, REVOKE, GRANT and COMMENT are re-runnable.
-- ROLLBACK: DROP TABLE public.attachment_angle_map_edits,
--                      public.attachment_segment_overrides,
--                      public.attachment_text_corrections;
--           (no other object depends on them)
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── attachment_text_corrections ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_text_corrections (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id             uuid        NOT NULL REFERENCES public.user_files(file_id) ON DELETE CASCADE,
    user_id             uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    wrong_text          text        NOT NULL,   -- as it is in content_md (one line)
    right_text          text        NOT NULL,   -- what it should read
    occurrences         integer     NOT NULL DEFAULT 1,   -- how many when the fix was made
    evidence            text        NOT NULL DEFAULT '',  -- the user's words that asked for it
    conversation_id     uuid        REFERENCES public.conversations(conversation_id) ON DELETE SET NULL,
    message_id          uuid        REFERENCES public.messages(message_id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    reverted_at         timestamptz,
    derived             jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- derived wording the fix rewrote (undo)
    CONSTRAINT attachment_text_corrections_file_owner_fkey
        FOREIGN KEY (file_id, user_id) REFERENCES public.user_files (file_id, user_id) ON DELETE CASCADE,
    CONSTRAINT attachment_text_corrections_text_chk CHECK (
        wrong_text <> '' AND right_text <> '' AND wrong_text <> right_text
        AND strpos(wrong_text, E'\n') = 0 AND strpos(right_text, E'\n') = 0
        AND char_length(wrong_text) <= 500 AND char_length(right_text) <= 500
    ),
    CONSTRAINT attachment_text_corrections_occurrences_chk CHECK (occurrences >= 1)
);
-- A database that ran an earlier draft of 234 (before `derived`) gets the column.
ALTER TABLE public.attachment_text_corrections
    ADD COLUMN IF NOT EXISTS derived jsonb NOT NULL DEFAULT '[]'::jsonb;

-- ── attachment_segment_overrides ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_segment_overrides (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id             uuid        NOT NULL REFERENCES public.user_files(file_id) ON DELETE CASCADE,
    user_id             uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    char_start          integer     NOT NULL,   -- offsets inside user_files.content_md
    char_end            integer     NOT NULL,
    value               text        NOT NULL DEFAULT 'core',
    evidence            text        NOT NULL DEFAULT '',
    conversation_id     uuid        REFERENCES public.conversations(conversation_id) ON DELETE SET NULL,
    message_id          uuid        REFERENCES public.messages(message_id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    reverted_at         timestamptz,
    CONSTRAINT attachment_segment_overrides_file_owner_fkey
        FOREIGN KEY (file_id, user_id) REFERENCES public.user_files (file_id, user_id) ON DELETE CASCADE,
    CONSTRAINT attachment_segment_overrides_range_chk CHECK (char_start >= 0 AND char_end > char_start),
    CONSTRAINT attachment_segment_overrides_value_chk CHECK (value = 'core')
);

-- ── attachment_angle_map_edits ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.attachment_angle_map_edits (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id              uuid        NOT NULL,   -- attachment_angle_maps.map_id — NO FK: survives a replace
    conversation_id     uuid        REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,
    user_id             uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    item_ids            jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- the map's members then
    kind                text        NOT NULL,
    target              text        NOT NULL DEFAULT '',   -- "A3", "gap 2", "client", the undone edit's id…
    before              jsonb,
    after               jsonb,
    evidence            text        NOT NULL DEFAULT '',
    message_id          uuid        REFERENCES public.messages(message_id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    undone_at           timestamptz,
    CONSTRAINT attachment_angle_map_edits_kind_chk CHECK (kind IN (
        'edit_angle', 'add_angle', 'remove_angle',
        'add_gap', 'edit_gap', 'remove_gap',
        'edit_client', 'edit_context', 'edit_no_angle', 'edit_unrelated',
        'snapshot', 'undo'
    )),
    CONSTRAINT attachment_angle_map_edits_item_ids_chk CHECK (jsonb_typeof(item_ids) = 'array')
);

-- ── indexes ─────────────────────────────────────────────────────────────────
-- Readers: "the active fixes / promotions of these files" on every file read;
-- "the edits of this map" / "of this conversation". user_id carries the account
-- cascade (no scan on a users delete).
CREATE INDEX IF NOT EXISTS attachment_text_corrections_file_active_idx
    ON public.attachment_text_corrections (file_id) WHERE reverted_at IS NULL;
CREATE INDEX IF NOT EXISTS attachment_text_corrections_user_id_idx
    ON public.attachment_text_corrections (user_id);
CREATE INDEX IF NOT EXISTS attachment_segment_overrides_file_id_idx
    ON public.attachment_segment_overrides (file_id);
CREATE INDEX IF NOT EXISTS attachment_segment_overrides_user_id_idx
    ON public.attachment_segment_overrides (user_id);
CREATE INDEX IF NOT EXISTS attachment_angle_map_edits_map_id_idx
    ON public.attachment_angle_map_edits (map_id);
CREATE INDEX IF NOT EXISTS attachment_angle_map_edits_conversation_id_idx
    ON public.attachment_angle_map_edits (conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS attachment_angle_map_edits_user_id_idx
    ON public.attachment_angle_map_edits (user_id);

-- ── RLS + grants ────────────────────────────────────────────────────────────
ALTER TABLE public.attachment_text_corrections  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_segment_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attachment_angle_map_edits   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attachment_text_corrections_select_own ON public.attachment_text_corrections;
CREATE POLICY attachment_text_corrections_select_own ON public.attachment_text_corrections FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS attachment_segment_overrides_select_own ON public.attachment_segment_overrides;
CREATE POLICY attachment_segment_overrides_select_own ON public.attachment_segment_overrides FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS attachment_angle_map_edits_select_own ON public.attachment_angle_map_edits;
CREATE POLICY attachment_angle_map_edits_select_own ON public.attachment_angle_map_edits FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

REVOKE ALL ON TABLE public.attachment_text_corrections  FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_segment_overrides FROM anon, authenticated;
REVOKE ALL ON TABLE public.attachment_angle_map_edits   FROM anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_text_corrections  TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_segment_overrides TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.attachment_angle_map_edits   TO service_role;

COMMENT ON TABLE public.attachment_text_corrections IS
    'Text fixes of an uploaded file (234, attachment_agent.md §3.3, D12/D14/D15): every wrong_text in user_files.content_md reads as right_text, applied by the readers in created_at order while reverted_at IS NULL. content_md is never rewritten. Keyed by the file: follows it into every conversation. Stored real. Service-role only (RLS on, no client grants).';
COMMENT ON TABLE public.attachment_segment_overrides IS
    'Sections the user promoted to core (234, attachment_agent.md §3.1): a character range [char_start, char_end) of user_files.content_md. load_segment_maps loads every non-core section overlapping an active range as core (promote only). Keyed by position on the file, so it survives a re-segment. Service-role only.';
COMMENT ON TABLE public.attachment_angle_map_edits IS
    'Log of the user-asked edits made in place on an angle map (234, attachment_agent.md §3.2): kind, target, before / after, the user''s words (evidence). map_id has no FK so the log survives a re-analysis; a snapshot row holds a whole map before one. undone_at = put back by an undo row. Service-role only.';
COMMENT ON COLUMN public.attachment_angle_map_edits.target IS
    '"A<seq>" for an angle, "gap <n>" (1-based) for a gap, "client" / "context" / "no_angle" / "unrelated_documents" / "map" (snapshot); for an undo row, the id of the edit it undid.';
COMMENT ON COLUMN public.attachment_text_corrections.derived IS
    'The wording derived from the file that the fix rewrote (shared/attachments/derived.py): [{place, key, field, before, after}] (real text) and [{place: "angle_map", map_id, edit_id, target}]. Undoing the fix restores each field only while it still reads "after", and undoes each angle-map edit.';

-- ── self-tests (before COMMIT, so a dry run proves them too) ─────────────────
DO $$
DECLARE
    v_n int;
BEGIN
    SELECT count(*) INTO v_n
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname IN ('attachment_text_corrections', 'attachment_segment_overrides', 'attachment_angle_map_edits')
       AND c.relrowsecurity;
    IF v_n <> 3 THEN
        RAISE EXCEPTION '234: RLS must be enabled on all three tables (found % of 3)', v_n;
    END IF;

    SELECT count(*) INTO v_n
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_text_corrections', 'attachment_segment_overrides', 'attachment_angle_map_edits')
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_n <> 0 THEN
        RAISE EXCEPTION '234: % client-role privilege(s) left on the edit tables', v_n;
    END IF;

    SELECT count(DISTINCT table_name) INTO v_n
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('attachment_text_corrections', 'attachment_segment_overrides', 'attachment_angle_map_edits')
       AND grantee = 'service_role'
       AND privilege_type = 'DELETE';
    IF v_n <> 3 THEN
        RAISE EXCEPTION '234: service_role must hold DML on all three tables (found % of 3)', v_n;
    END IF;

    -- Fixes and promotions go with their file (two keys each: file, file + owner).
    SELECT count(*) INTO v_n
      FROM pg_constraint
     WHERE contype = 'f' AND confdeltype = 'c'
       AND conrelid IN ('public.attachment_text_corrections'::regclass, 'public.attachment_segment_overrides'::regclass)
       AND confrelid = 'public.user_files'::regclass;
    IF v_n <> 4 THEN
        RAISE EXCEPTION '234: expected 4 ON DELETE CASCADE keys to user_files (file + owner, two tables), found %', v_n;
    END IF;

    -- … and NOT with the conversation they were asked in (D15).
    SELECT count(*) INTO v_n
      FROM pg_constraint
     WHERE contype = 'f' AND confdeltype = 'n'
       AND conrelid IN ('public.attachment_text_corrections'::regclass, 'public.attachment_segment_overrides'::regclass)
       AND confrelid IN ('public.conversations'::regclass, 'public.messages'::regclass);
    IF v_n <> 4 THEN
        RAISE EXCEPTION '234: fixes / promotions must SET NULL on a conversation / message delete (found % of 4)', v_n;
    END IF;

    -- The log must survive a map replace: no key from it to the maps.
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE contype = 'f'
                  AND conrelid = 'public.attachment_angle_map_edits'::regclass
                  AND confrelid = 'public.attachment_angle_maps'::regclass) THEN
        RAISE EXCEPTION '234: attachment_angle_map_edits must not reference attachment_angle_maps';
    END IF;

    SELECT count(*) INTO v_n
      FROM pg_constraint
     WHERE contype = 'c'
       AND conname IN ('attachment_text_corrections_text_chk', 'attachment_segment_overrides_range_chk',
                       'attachment_segment_overrides_value_chk', 'attachment_angle_map_edits_kind_chk');
    IF v_n <> 4 THEN
        RAISE EXCEPTION '234: text / range / value / kind CHECKs missing (found % of 4)', v_n;
    END IF;

    IF (SELECT count(*) FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname IN ('attachment_text_corrections_file_active_idx', 'attachment_segment_overrides_file_id_idx',
                             'attachment_angle_map_edits_map_id_idx', 'attachment_angle_map_edits_conversation_id_idx')) <> 4 THEN
        RAISE EXCEPTION '234: reader indexes missing';
    END IF;

    -- The undo of a fix reads what it rewrote elsewhere.
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'attachment_text_corrections'
                      AND column_name = 'derived' AND data_type = 'jsonb' AND is_nullable = 'NO') THEN
        RAISE EXCEPTION '234: attachment_text_corrections.derived (jsonb NOT NULL) missing';
    END IF;

    RAISE NOTICE '234 OK: 3 tables, RLS on, client roles revoked, service_role DML, file cascades, conversation SET NULL, log without map FK, checks, indexes, derived column';
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
