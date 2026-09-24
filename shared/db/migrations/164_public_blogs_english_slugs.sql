-- 164_public_blogs_english_slugs.sql
--
-- English article slugs, a slug that can change at publish, and the redirect
-- layer that makes a change safe.
--
-- WHY. An Arabic slug percent-encodes to ~6x its length and, pasted into Arabic
-- prose, merges with the surrounding RTL text — on 2026-09-24 an X post went out
-- with the slug's tail pasted twice, Twitterbot got a 404, and the post had no
-- card. The marketing rewriter now proposes an ASCII kebab slug, and «نشر»
-- swaps it in. Three things stood in the way; this migration removes all three.
--
-- 1. SHAPE. 153's `public_blogs_slug_shape` forbade ASCII kebab outright — that
--    was how /blog/{ref} told an article from a subject. The guarantee is now
--    expressed as what it always meant: NO SLUG IS EVER HELD BY BOTH
--    VOCABULARIES. Two triggers enforce it in both directions (a blog slug may
--    not equal a subject slug, a subject slug may not equal any blog slug or
--    alias). The CHECK keeps the parts shape can still say: a pure-ASCII slug
--    must be kebab-case, a 32-hex slug is a legacy `blog_posts` token and is
--    refused, `subjects` is the literal index segment.
--
-- 2. PERMANENCE. A slug was permanent because a rename 404'd. The rename now
--    leaves the old slug in `public_blog_slug_aliases`, and every by-slug read
--    falls back to it — the frontend answers an alias with a 308 to the current
--    slug. A slug change is therefore a redirect, never a dead link.
--
-- 3. TIMING. The slug is minted from the Arabic headline at submit, before the
--    rewriter runs. `append_public_blog_version` gains `p_slug`: publish passes
--    the rewriter's English slug and the Arabic one becomes an alias. Omitting
--    `p_slug` (every existing caller) carries the slug exactly as before.
--
-- Idempotent. Additive for every existing caller: the 6-argument call shape
-- still resolves because `p_slug` defaults to NULL.

-- ---------------------------------------------------------------------------
-- 1. The alias table — the redirect layer.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.public_blog_slug_aliases (
    slug        text PRIMARY KEY,
    root_id     uuid NOT NULL REFERENCES public.public_blogs(blog_id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_public_blog_slug_aliases_root
    ON public.public_blog_slug_aliases(root_id);

COMMENT ON TABLE public.public_blog_slug_aliases IS
    'Slugs a public blog USED to have. /blog/{alias} 308s to the current slug. Written only by append_public_blog_version; service-role only (RLS on, no policies).';

ALTER TABLE public.public_blog_slug_aliases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.public_blog_slug_aliases FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. The shape CHECK — English allowed, token shape and `subjects` refused.
-- ---------------------------------------------------------------------------

ALTER TABLE public.public_blogs DROP CONSTRAINT IF EXISTS public_blogs_slug_shape;
ALTER TABLE public.public_blogs
    ADD CONSTRAINT public_blogs_slug_shape
    CHECK (
        char_length(slug) BETWEEN 1 AND 200
        AND slug <> 'subjects'
        AND slug !~ '^[0-9a-f]{32}$'
        -- Pure ASCII ⇒ kebab-case. A slug with any non-ASCII char is Arabic.
        AND (slug ~ '[^\x01-\x7f]' OR slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
    );

COMMENT ON COLUMN public.public_blogs.slug IS
    'URL slug — English kebab-case (from the rewriter at publish) or Arabic (minted at submit). May change via append_public_blog_version(p_slug); the old one is kept in public_blog_slug_aliases and redirects. Never equal to a blog_subjects.slug (trigger-enforced both ways).';

-- ---------------------------------------------------------------------------
-- 3. One URL space, two vocabularies — enforced both ways.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.public_blogs_slug_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF EXISTS (SELECT 1 FROM public.blog_subjects WHERE slug = NEW.slug) THEN
        RAISE EXCEPTION 'slug_taken: % is a blog subject slug', NEW.slug
            USING ERRCODE = 'unique_violation';
    END IF;
    -- Another blog's OLD address. Taking it would hijack that blog's redirect.
    IF EXISTS (
        SELECT 1 FROM public.public_blog_slug_aliases
        WHERE slug = NEW.slug
          AND root_id IS DISTINCT FROM COALESCE(NEW.root_id, NEW.blog_id)
    ) THEN
        RAISE EXCEPTION 'slug_taken: % is a former slug of another blog', NEW.slug
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_public_blogs_slug_guard ON public.public_blogs;
CREATE TRIGGER trg_public_blogs_slug_guard
    BEFORE INSERT OR UPDATE OF slug ON public.public_blogs
    FOR EACH ROW EXECUTE FUNCTION public.public_blogs_slug_guard();

CREATE OR REPLACE FUNCTION public.blog_subjects_slug_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF EXISTS (SELECT 1 FROM public.public_blogs WHERE slug = NEW.slug)
       OR EXISTS (SELECT 1 FROM public.public_blog_slug_aliases WHERE slug = NEW.slug)
    THEN
        RAISE EXCEPTION 'slug_taken: % is a blog article slug', NEW.slug
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_blog_subjects_slug_guard ON public.blog_subjects;
CREATE TRIGGER trg_blog_subjects_slug_guard
    BEFORE INSERT OR UPDATE OF slug ON public.blog_subjects
    FOR EACH ROW EXECUTE FUNCTION public.blog_subjects_slug_guard();

-- ---------------------------------------------------------------------------
-- 4. append_public_blog_version gains p_slug.
-- ---------------------------------------------------------------------------
-- DROP + CREATE rather than CREATE OR REPLACE: adding a parameter under OR
-- REPLACE would leave the 6-arg overload beside the 7-arg one, and PostgREST
-- refuses to choose between overloads that both match a named-argument call.

DROP FUNCTION IF EXISTS public.append_public_blog_version(uuid, text, text, text, text, text);

CREATE OR REPLACE FUNCTION public.append_public_blog_version(
    p_root_id       uuid,
    p_content_md    text,
    p_title         text    DEFAULT NULL,
    p_revision_note text    DEFAULT NULL,
    p_type          text    DEFAULT NULL,
    p_confidence    text    DEFAULT NULL,
    p_slug          text    DEFAULT NULL
)
RETURNS public.public_blogs
LANGUAGE plpgsql
AS $fn$
DECLARE
    cur    public.public_blogs;
    nxt    public.public_blogs;
    v_slug text;
BEGIN
    SELECT * INTO cur
    FROM public.public_blogs
    WHERE root_id = p_root_id AND is_current AND deleted_at IS NULL
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no current version for root_id %', p_root_id
            USING ERRCODE = 'no_data_found';
    END IF;

    v_slug := COALESCE(NULLIF(btrim(p_slug), ''), cur.slug);

    IF v_slug <> cur.slug THEN
        -- Taking back a slug this blog held before: it stops being an alias.
        DELETE FROM public.public_blog_slug_aliases
        WHERE slug = v_slug AND root_id = cur.root_id;
        -- The address being left keeps working as a redirect.
        INSERT INTO public.public_blog_slug_aliases (slug, root_id)
        VALUES (cur.slug, cur.root_id)
        ON CONFLICT (slug) DO NOTHING;
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
        v_slug,
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

REVOKE ALL ON FUNCTION public.append_public_blog_version(uuid,text,text,text,text,text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.append_public_blog_version(uuid,text,text,text,text,text,text) TO service_role;

NOTIFY pgrst, 'reload schema';
