-- 156_public_blogs_job_idempotency.sql — one editorial job publishes at most one blog.
--
-- Plan: .claude/plans/blog_subjects.md §5.
--
-- THE BUG THIS CLOSES (found by the first live end-to-end run, 2026-09-02)
-- ----------------------------------------------------------------------
-- One POST to /internal/blog-post-jobs produced **two** published blogs, 60
-- seconds apart, with different slugs:
--
--   34f7e7ef…  فسخ-عقد-الإيجار-التجاري-…
--   411be387…  فسخ-العقود-والشرط-الجزائي-…
--
-- `create_or_get_job` dedupes on `idempotency_key`, so only one JOB existed.
-- The publish step's only guard was `assert_slug_available` — and the slug is
-- derived from the aggregator's headline, which is **non-deterministic**. So a
-- re-drive (the catch-up sweep, or any retry after the row was written but
-- before the job was marked completed) generates a *different* headline, takes
-- a *different* slug, sails past the uniqueness check, and publishes a second
-- article from the same job.
--
-- Dedupe therefore cannot live on the slug. It has to live on the job, and it
-- has to be enforced by the database rather than by a read-then-write check in
-- the service — that check has a window exactly the width of one pipeline run,
-- which is precisely when a re-drive happens.
--
-- ⚠ VERSIONS DO NOT CARRY job_id. `append_public_blog_version` (migration 155)
-- does not copy this column, so every SEO rewrite inserts NULL here and the
-- partial index only ever constrains v1 rows. That is deliberate: the constraint
-- is "one job → one BLOG", not "one job → one version".
--
-- Idempotent: safe to re-run.

ALTER TABLE public.public_blogs
    ADD COLUMN IF NOT EXISTS job_id uuid;

COMMENT ON COLUMN public.public_blogs.job_id IS
    'The blog_post_jobs row that published this blog (v1 only — versions carry NULL). Uniquely indexed: a re-driven job cannot publish a second article, which the slug check could not prevent because the slug comes from a non-deterministic LLM headline.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_job
    ON public.public_blogs(job_id)
    WHERE job_id IS NOT NULL AND deleted_at IS NULL;
