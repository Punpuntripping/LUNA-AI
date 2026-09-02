-- 153_public_blogs.sql — the VERSIONED public blog wing.
--
-- Plan: .claude/plans/blog_subjects.md §2 (rev 2).
--
-- WHY A SECOND TABLE RATHER THAN COLUMNS ON blog_posts
-- ----------------------------------------------------
-- `blog_posts` (migration 070) is a FROZEN share-link snapshot: at publish it
-- freezes content_md + the resolved Reference[] so that editing or deleting the
-- source artifact can never change or break a link already sent to someone.
-- That immutability is the whole point of the design.
--
-- The public wing needs the opposite: an SEO agent rewrites a published article
-- (.claude/plans/marketing_agents.md §2.1). Putting a mutable article in an
-- immutable table would have quietly broken the guarantee for the 99 unlisted
-- share links that depend on it. So the public wing gets its own table, and
-- `blog_posts` keeps doing exactly what it does today, unchanged.
--
-- VERSIONING (plan D15)
-- --------------------
-- Every SEO rewrite APPENDS a version. `root_id` is the logical blog and is
-- propagated to every version — the same root-resolution shape
-- `blog_posts.source_post_id` already uses for copy chains (migration 088).
-- The slug addresses the CURRENT version only; versions share an address.
--
-- ⚠ THE APP MUST SUPPLY blog_id EXPLICITLY ON v1. `root_id` self-references
-- `blog_id`, so a v1 row sets both to the same value. The caller generates the
-- uuid (uuid4) and sends it, exactly as `deepsearch_api/generate.py` already
-- does for message ids. The FK is DEFERRABLE so the self-reference in a single
-- INSERT can never trip on statement ordering.
--
-- ⚠ INVERTED DEFAULT (plan D17). blog_posts.is_public defaults FALSE — those
-- pages are unlisted by nature. public_blogs.is_public defaults TRUE: a public
-- blog is open the moment it exists. Retraction is what flips it off.
--
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- 1. The table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.public_blogs (
    blog_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Versioning. root_id == blog_id on v1.
    root_id       uuid NOT NULL,
    version_no    integer NOT NULL DEFAULT 1,
    is_current    boolean NOT NULL DEFAULT true,
    revision_note text,

    -- Address + identity
    slug          text NOT NULL,
    title         text NOT NULL,
    type          text NOT NULL,

    -- Frozen content. references_json is copied VERBATIM into every version:
    -- the citation set of a published blog is closed (plan D18), which is what
    -- makes the SEO rewrite checkable rather than merely instructed.
    question_text   text NOT NULL,
    content_md      text NOT NULL,
    references_json jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Provenance
    subtype        text,
    source_item_id uuid,
    author_user_id uuid NOT NULL,
    confidence     text,

    -- Visibility
    is_public     boolean NOT NULL DEFAULT true,
    is_published  boolean NOT NULL DEFAULT true,

    view_count    integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

COMMENT ON TABLE public.public_blogs IS
    'The public blog wing (مدونة ريحان) — VERSIONED. One row per version; root_id is the logical blog; the slug serves is_current. Distinct from blog_posts, which stays an immutable share-link snapshot. Writes are service-role only.';

COMMENT ON COLUMN public.public_blogs.root_id IS
    'The logical blog. Equals blog_id on version 1 and is propagated to every later version.';
COMMENT ON COLUMN public.public_blogs.slug IS
    'Arabic URL slug. PERMANENT across versions — there is no redirect layer, so a rename 404s. A rewrite may change title; it must never change slug.';
COMMENT ON COLUMN public.public_blogs.references_json IS
    'Frozen Reference[] copied verbatim into every version. The citation set is CLOSED: an SEO rewrite may drop a citation but may never add or renumber one.';
COMMENT ON COLUMN public.public_blogs.is_public IS
    'Present in the public gallery + sitemap. DEFAULT TRUE — inverted from blog_posts, where unlisted is the default. Retraction sets it false; the page itself stays readable by direct link.';
COMMENT ON COLUMN public.public_blogs.question_text IS
    'The ANONYMIZED question. Ships in the public JSON even though the article template never displays it — never store a raw one.';

-- Self-reference, deferrable so a v1 INSERT setting root_id = blog_id is fine.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'public_blogs_root_fk'
    ) THEN
        ALTER TABLE public.public_blogs
            ADD CONSTRAINT public_blogs_root_fk
            FOREIGN KEY (root_id) REFERENCES public.public_blogs(blog_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

-- The three types (plan D3). Carried by the BLOG, not by the subject.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'public_blogs_type_check'
    ) THEN
        ALTER TABLE public.public_blogs
            ADD CONSTRAINT public_blogs_type_check
            CHECK (type IN ('laws_explanation', 'judicial_research', 'compliance'));
    END IF;
END $$;

-- ⚠ THE DISPATCHER'S GUARANTEE, EXPRESSED AS DATA (plan §3).
-- /blog/{ref} resolves subjects BEFORE blogs. blog_subjects.slug is constrained
-- to ASCII kebab-case (migration 154); this constraint forbids exactly that
-- shape here. A blog slug therefore can NEVER collide with a subject slug —
-- the resolution order is a guarantee, not a convention that a hand-minted slug
-- could later violate.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'public_blogs_slug_shape'
    ) THEN
        ALTER TABLE public.public_blogs
            ADD CONSTRAINT public_blogs_slug_shape
            CHECK (
                char_length(slug) BETWEEN 1 AND 200
                AND slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$'
            );
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------

-- Exactly one live version per logical blog. A botched version flip fails loudly
-- here instead of silently serving two current rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_current
    ON public.public_blogs(root_id)
    WHERE is_current AND deleted_at IS NULL;

-- The slug addresses the CURRENT version only.
CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_slug
    ON public.public_blogs(slug)
    WHERE is_current AND deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_version
    ON public.public_blogs(root_id, version_no);

-- The gallery / sitemap predicate.
CREATE INDEX IF NOT EXISTS idx_public_blogs_gallery
    ON public.public_blogs(created_at DESC)
    WHERE is_current AND is_public AND is_published AND deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 3. updated_at trigger
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_public_blogs_updated_at ON public.public_blogs;
CREATE TRIGGER trg_public_blogs_updated_at
    BEFORE UPDATE ON public.public_blogs
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ---------------------------------------------------------------------------
-- 4. RLS — read-only to the world, writes service-role only
-- ---------------------------------------------------------------------------
-- Mirrors blog_posts' posture (migration 070): anon + authenticated may SELECT
-- what is publicly visible; there is NO INSERT/UPDATE/DELETE policy at all, so
-- every write goes through the backend's service role. The editorial API is the
-- only writer.

ALTER TABLE public.public_blogs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public_blogs_select_visible" ON public.public_blogs;
CREATE POLICY "public_blogs_select_visible"
    ON public.public_blogs
    FOR SELECT
    TO anon, authenticated
    USING (
        is_current
        AND is_public
        AND is_published
        AND deleted_at IS NULL
    );
