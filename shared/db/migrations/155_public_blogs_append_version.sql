-- 155_public_blogs_append_version.sql — make the version flip actually atomic.
--
-- Plan: .claude/plans/blog_subjects.md §2 "Versioning mechanics" — "one
-- transaction: insert the new row with is_current=true, flip the old row to
-- is_current=false."
--
-- WHY THIS EXISTS
-- ---------------
-- The backend reaches Postgres through PostgREST, which cannot wrap several
-- statements in one transaction. The service layer's best available shape was
-- three round trips (insert demoted → demote old → promote new) with a
-- compensating re-flip on failure. That is SAFE — the partial unique index
-- `idx_public_blogs_current` makes two-current impossible and a lost race
-- surfaces as a 409 — but a crash between statements can strand an orphan
-- non-current row, and this is the write path the SEO agent takes on EVERY
-- rewrite (.claude/plans/marketing_agents.md §2.1).
--
-- One plpgsql function runs in a single implicit transaction, so the flip is
-- all-or-nothing with no compensation logic and no debris.
--
-- WHAT IT DOES NOT DO
-- -------------------
-- It does not touch `slug` (permanent across versions — there is no redirect
-- layer, so a rename 404s) and it does not touch `public_blog_subjects` (keyed
-- on root_id, i.e. on the LOGICAL blog, so an appended version inherits its
-- subjects without being re-filed).
--
-- `references_json` is carried over VERBATIM from the current version by
-- default: the citation set of a published blog is CLOSED (plan D18), which is
-- what lets the SEO rewrite be checked rather than merely instructed.
--
-- ⚠ `view_count` is CARRIED FORWARD, not reset. A reader is reading the blog,
-- not "version 3" of it — the count belongs to the logical article the way
-- `slug` does. Resetting per version would let an SEO rewrite silently discard
-- the readership it was written to grow, and nothing in this wing aggregates
-- views across a root to recover it.
--
-- Idempotent: safe to re-run.

CREATE OR REPLACE FUNCTION public.append_public_blog_version(
    p_root_id       uuid,
    p_content_md    text,
    p_title         text    DEFAULT NULL,   -- NULL = carry the current title
    p_revision_note text    DEFAULT NULL,
    p_type          text    DEFAULT NULL,   -- NULL = carry the current type
    p_confidence    text    DEFAULT NULL
)
RETURNS public.public_blogs
LANGUAGE plpgsql
AS $$
DECLARE
    cur public.public_blogs;
    nxt public.public_blogs;
BEGIN
    -- Lock the current version so two concurrent appends serialize here rather
    -- than racing to the unique index.
    SELECT * INTO cur
    FROM public.public_blogs
    WHERE root_id = p_root_id
      AND is_current
      AND deleted_at IS NULL
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no current version for root_id %', p_root_id
            USING ERRCODE = 'no_data_found';
    END IF;

    UPDATE public.public_blogs
       SET is_current = false
     WHERE blog_id = cur.blog_id;

    INSERT INTO public.public_blogs (
        root_id, version_no, is_current, revision_note,
        slug, title, type,
        question_text, content_md, references_json,
        subtype, source_item_id, author_user_id, confidence,
        is_public, is_published, view_count
    )
    VALUES (
        cur.root_id, cur.version_no + 1, true, p_revision_note,
        cur.slug,                                  -- PERMANENT
        COALESCE(p_title, cur.title),
        COALESCE(p_type, cur.type),
        cur.question_text,
        p_content_md,
        cur.references_json,                       -- VERBATIM — closed set
        cur.subtype, cur.source_item_id, cur.author_user_id,
        COALESCE(p_confidence, cur.confidence),
        cur.is_public, cur.is_published, cur.view_count
    )
    RETURNING * INTO nxt;

    RETURN nxt;
END;
$$;

COMMENT ON FUNCTION public.append_public_blog_version IS
    'Atomically append a new version of a public blog and demote the previous one. Carries slug (permanent), references_json (the closed citation set) and visibility forward; subjects ride root_id and are untouched. Service-role only.';

-- Writes on this table are service-role only (migration 153 grants no
-- INSERT/UPDATE policy at all). Functions are EXECUTE-able by PUBLIC by
-- default, so revoke that and hand it to the service role explicitly —
-- otherwise this would be a write primitive reachable with the anon key.
REVOKE ALL ON FUNCTION public.append_public_blog_version(
    uuid, text, text, text, text, text
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.append_public_blog_version(
    uuid, text, text, text, text, text
) TO service_role;
