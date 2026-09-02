-- 154_blog_subjects.sql — the blog's browse axis (مواضيع).
--
-- Plan: .claude/plans/blog_subjects.md §2 (rev 2). Requires 153.
--
-- WHAT A SUBJECT IS
-- -----------------
-- A closed, curated vocabulary a reader can enter the blog on: /blog/work-law
-- lists every public blog carrying نظام العمل. Designed to reach ~100 rows.
--
-- ⚠ SUBJECTS CARRY NO `type` (plan D3). An earlier revision put the three
-- types (laws_explanation / judicial_research / compliance) on the subject and
-- had the blog inherit them. They now live on `public_blogs.type` instead —
-- one owner, so a blog and its subjects can never disagree about what the blog
-- is. Subjects are plain tags: slug + Arabic label.
--
-- ⚠ SLUGS ARE ASCII, AND THAT IS LOAD-BEARING. Migration 153 forbids this exact
-- shape on `public_blogs.slug`. Together the two constraints make the /blog/{ref}
-- dispatcher unambiguous by construction (plan §3): a subject slug and a blog
-- slug can never be the same string.
--
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- 1. The vocabulary
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.blog_subjects (
    subject_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug           text NOT NULL UNIQUE,
    label_ar       text NOT NULL,
    description_ar text,
    sort_rank      integer NOT NULL DEFAULT 0,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.blog_subjects IS
    'The blog browse vocabulary (مواضيع). Closed and curated — service-role writes only. Subjects are plain tags; the three types live on public_blogs.type, not here.';
COMMENT ON COLUMN public.blog_subjects.slug IS
    'ASCII kebab-case, permanent. Constrained to a shape that public_blogs.slug is forbidden from taking, which is what makes the /blog/{ref} dispatcher unambiguous.';
COMMENT ON COLUMN public.blog_subjects.is_active IS
    'Retiring a subject sets this false. Never delete a subject — the join FK is RESTRICT so a delete would have to unfile published blogs first.';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'blog_subjects_slug_ascii'
    ) THEN
        ALTER TABLE public.blog_subjects
            ADD CONSTRAINT blog_subjects_slug_ascii
            CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$');
    END IF;
END $$;

-- `subjects` is the literal segment of /blog/subjects (the full index page), so
-- no subject may claim it. The publish path refuses it too; this is the backstop.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'blog_subjects_slug_reserved'
    ) THEN
        ALTER TABLE public.blog_subjects
            ADD CONSTRAINT blog_subjects_slug_reserved
            CHECK (slug NOT IN ('subjects'));
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. The join — subjects belong to the LOGICAL blog, not to a version
-- ---------------------------------------------------------------------------
-- Keyed on root_id so an SEO rewrite (which appends a version) never has to
-- re-file the subjects it was already tagged with.

CREATE TABLE IF NOT EXISTS public.public_blog_subjects (
    root_id    uuid NOT NULL
               REFERENCES public.public_blogs(blog_id) ON DELETE CASCADE,
    subject_id uuid NOT NULL
               REFERENCES public.blog_subjects(subject_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (root_id, subject_id)
);

COMMENT ON TABLE public.public_blog_subjects IS
    'Many-to-many: one blog targets several subjects. Keyed on public_blogs.root_id (the LOGICAL blog) so appending an SEO version never re-files subjects.';

CREATE INDEX IF NOT EXISTS idx_public_blog_subjects_subject
    ON public.public_blog_subjects(subject_id);

-- ---------------------------------------------------------------------------
-- 3. updated_at trigger
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_blog_subjects_updated_at ON public.blog_subjects;
CREATE TRIGGER trg_blog_subjects_updated_at
    BEFORE UPDATE ON public.blog_subjects
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ---------------------------------------------------------------------------
-- 4. RLS
-- ---------------------------------------------------------------------------

ALTER TABLE public.blog_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.public_blog_subjects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "blog_subjects_select_active" ON public.blog_subjects;
CREATE POLICY "blog_subjects_select_active"
    ON public.blog_subjects
    FOR SELECT
    TO anon, authenticated
    USING (is_active);

-- The join is readable, but every path to it is already filtered by the blog's
-- own visibility policy from 153 — a row here reveals nothing on its own.
DROP POLICY IF EXISTS "public_blog_subjects_select" ON public.public_blog_subjects;
CREATE POLICY "public_blog_subjects_select"
    ON public.public_blog_subjects
    FOR SELECT
    TO anon, authenticated
    USING (true);

-- ---------------------------------------------------------------------------
-- 5. Seed — the three the operator named (plan §1)
-- ---------------------------------------------------------------------------
-- Arabic labels are copied verbatim from the operator's own message, never
-- retyped. `work-law` is the operator's slug — do not "correct" it to
-- `labor-law`. sort_rank is a manual ordering hint; ties break on blog count.

INSERT INTO public.blog_subjects (slug, label_ar, sort_rank)
VALUES
    ('work-law',        'نظام العمل', 10),
    ('promissory-note', 'سند الأمر',  20),
    ('saudization',     'السعودة',    30)
ON CONFLICT (slug) DO NOTHING;
