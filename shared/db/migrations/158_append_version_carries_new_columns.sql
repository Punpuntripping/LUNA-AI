-- 158_append_version_carries_new_columns.sql
--
-- Migration 155's append_public_blog_version enumerates its INSERT columns
-- explicitly, so the two columns added by 157 (`review_status`,
-- `generation_context`) would silently arrive at their defaults on every new
-- version: a rewrite of an approved article would come back 'pending', and the
-- generation record — the whole point of keeping the first draft — would be
-- NULL from v2 onward, exactly when an editor most needs it.
--
-- Both are carried forward from the current version, alongside slug,
-- references_json and view_count.
--
-- ⚠ OPEN QUESTION for whoever builds approval: should an SEO rewrite RESET
-- review_status to 'pending'? Carrying it forward is the neutral choice while
-- enforcement does not exist; re-review on every rewrite is defensible and is a
-- one-word change here.
--
-- Idempotent: CREATE OR REPLACE.

CREATE OR REPLACE FUNCTION public.append_public_blog_version(
    p_root_id       uuid,
    p_content_md    text,
    p_title         text    DEFAULT NULL,
    p_revision_note text    DEFAULT NULL,
    p_type          text    DEFAULT NULL,
    p_confidence    text    DEFAULT NULL
)
RETURNS public.public_blogs
LANGUAGE plpgsql
AS $fn$
DECLARE
    cur public.public_blogs;
    nxt public.public_blogs;
BEGIN
    SELECT * INTO cur
    FROM public.public_blogs
    WHERE root_id = p_root_id AND is_current AND deleted_at IS NULL
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no current version for root_id %', p_root_id
            USING ERRCODE = 'no_data_found';
    END IF;

    UPDATE public.public_blogs SET is_current = false WHERE blog_id = cur.blog_id;

    INSERT INTO public.public_blogs (
        root_id, version_no, is_current, revision_note,
        slug, title, type,
        question_text, content_md, references_json,
        subtype, source_item_id, author_user_id, confidence,
        is_public, is_published, view_count,
        review_status, generation_context
    )
    VALUES (
        cur.root_id, cur.version_no + 1, true, p_revision_note,
        cur.slug,
        COALESCE(p_title, cur.title),
        COALESCE(p_type, cur.type),
        cur.question_text,
        p_content_md,
        cur.references_json,
        cur.subtype, cur.source_item_id, cur.author_user_id,
        COALESCE(p_confidence, cur.confidence),
        cur.is_public, cur.is_published, cur.view_count,
        cur.review_status,
        cur.generation_context
    )
    RETURNING * INTO nxt;

    RETURN nxt;
END;
$fn$;

REVOKE ALL ON FUNCTION public.append_public_blog_version(uuid,text,text,text,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.append_public_blog_version(uuid,text,text,text,text,text) TO service_role;
