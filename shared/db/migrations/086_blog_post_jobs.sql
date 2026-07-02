-- 086_blog_post_jobs.sql
-- Feature: Blog-Post Generation API (internal, service-authed)
-- Plan: .claude/plans/blog_post_api.md  (§5 "Data model — migration 086")
--
-- Purpose:
--   Durable job table backing the async blog-post generation endpoint
--   (POST /internal/blog-post-jobs). A service caller submits a legal
--   `question`; a job row is inserted (status=queued) and drained 2-at-a-time
--   by an in-process asyncio worker that drives the SAME generation pipeline an
--   in-app message uses, then snapshots the answer into a private/unlisted
--   `blog_posts` row. This table is the submit → poll surface + the crash-safe
--   record of each job's request echo, provenance, and result/error.
--
-- Dependencies:
--   - 001_extensions.sql   (pgcrypto -> gen_random_uuid)
--   - 014_triggers.sql     (public.update_updated_at() trigger function)
--   - 070_blog_posts.sql   (blog_posts.post_id — provenance ref only, no FK)
--
-- Verified state facts (canonical migration files):
--   * pgcrypto IS installed (001) -> gen_random_uuid() available for the PK.
--   * The updated_at bump function is public.update_updated_at() (014_triggers
--     .sql:7); every table with updated_at reuses it — blog_posts (070),
--     system_templates (046), user_templates (055). We reuse it here too.
--   * No migration >085 exists; 086 is free.
--
-- Security (service-role-only surface):
--   * RLS is ENABLED with NO policies. RLS-on + zero-policies = DENY-ALL for
--     anon and authenticated (the JWT roles); the service-role client bypasses
--     RLS entirely. This table has no user-facing read/write path — the
--     endpoint is guarded by a shared service key (EDITORIAL_SERVICE_KEY) and
--     all reads/writes go through the backend service-role client. So there is
--     deliberately no SELECT/INSERT/UPDATE/DELETE policy.
--
-- NOTE: no hard FK on conversation_id / workspace_item_id / post_id — they are
-- provenance references only, mirroring blog_posts.source_item_id (070). The
-- job record must survive independent of the artifacts it produced.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- DROP TRIGGER IF EXISTS before CREATE TRIGGER.

BEGIN;

------------------------------------------------------------------------
-- 1. Table
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.blog_post_jobs (
    job_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Retries reuse the same key -> never duplicate a post. Unique index below.
    idempotency_key   TEXT UNIQUE NOT NULL,

    -- queued -> processing -> completed | failed.
    status            TEXT NOT NULL DEFAULT 'queued',

    ------------------------------------------------------------------
    -- request echo (the submitted body, frozen for provenance/replay)
    ------------------------------------------------------------------
    question          TEXT NOT NULL,
    title             TEXT,
    display_mode      TEXT NOT NULL DEFAULT 'question',
    subtype           TEXT NOT NULL DEFAULT 'marketing_telegram',
    language          TEXT NOT NULL DEFAULT 'ar',
    publish_policy    TEXT NOT NULL DEFAULT 'auto',   -- auto|always|never
    min_confidence    TEXT NOT NULL DEFAULT 'high',   -- high|medium|low
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    callback_url      TEXT,

    ------------------------------------------------------------------
    -- results / provenance (no hard FKs — provenance refs only)
    ------------------------------------------------------------------
    conversation_id   UUID,          -- the throwaway conversation used
    workspace_item_id UUID,          -- the generated workspace_items.item_id
    post_id           UUID,          -- the resulting blog_posts.post_id
    result            JSONB,         -- the full result payload we return
    error             JSONB,         -- {code, message, retryable}
    attempts          INT NOT NULL DEFAULT 0,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,

    CONSTRAINT blog_post_jobs_status_check
        CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    CONSTRAINT blog_post_jobs_publish_policy_check
        CHECK (publish_policy IN ('auto', 'always', 'never'))
);

------------------------------------------------------------------------
-- 2. Indexes
------------------------------------------------------------------------
-- Idempotency dedup lookup (also enforced by the UNIQUE constraint; the named
-- index documents the access path and makes the submit-time dedup race-safe).
CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_post_jobs_idem
    ON public.blog_post_jobs (idempotency_key);

-- Worker hot path: the startup catch-up sweep + queue drain scan only in-flight
-- jobs. Partial index keeps it tiny (completed/failed rows excluded).
CREATE INDEX IF NOT EXISTS idx_blog_post_jobs_status
    ON public.blog_post_jobs (status)
    WHERE status IN ('queued', 'processing');

------------------------------------------------------------------------
-- 3. updated_at trigger (reuse public.update_updated_at() from 014)
------------------------------------------------------------------------
DROP TRIGGER IF EXISTS update_blog_post_jobs_updated_at ON public.blog_post_jobs;
CREATE TRIGGER update_blog_post_jobs_updated_at
    BEFORE UPDATE ON public.blog_post_jobs
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

------------------------------------------------------------------------
-- 4. RLS — service-role only (RLS-on + NO policies => deny-all)
------------------------------------------------------------------------
-- Enabling RLS with zero policies denies ALL access to the anon and
-- authenticated roles. The service-role client (used by the backend job
-- worker + endpoints) bypasses RLS, and that is the ONLY intended access path.
-- This is a service-authed internal surface (EDITORIAL_SERVICE_KEY guards the
-- HTTP endpoint), so there is deliberately no user-facing policy of any kind.
ALTER TABLE public.blog_post_jobs ENABLE ROW LEVEL SECURITY;

------------------------------------------------------------------------
-- 5. Comments
------------------------------------------------------------------------
COMMENT ON TABLE public.blog_post_jobs IS
    'Durable async job records for the internal Blog-Post Generation API (POST /internal/blog-post-jobs). Each row = one submitted legal question driven headlessly through the generation pipeline by the editorial bot, snapshotted into a private/unlisted blog_posts row. RLS enabled with NO policies => service-role only (deny-all for anon/authenticated); the endpoint is guarded by EDITORIAL_SERVICE_KEY.';
COMMENT ON COLUMN public.blog_post_jobs.idempotency_key IS
    'Caller-supplied dedup key. UNIQUE — a retry with the same key returns the existing job (never a second post); lookup runs before the rate limiter so retries are free.';
COMMENT ON COLUMN public.blog_post_jobs.status IS
    'queued -> processing -> completed | failed. CHECK-constrained. Partial status index covers the in-flight (queued/processing) worker scan.';
COMMENT ON COLUMN public.blog_post_jobs.publish_policy IS
    'auto | always | never. auto => is_published = (confidence.label >= min_confidence); always/never force it. is_public stays false (private/unlisted).';
COMMENT ON COLUMN public.blog_post_jobs.min_confidence IS
    'Threshold (high|medium|low) the auto publish_policy compares the WI metadata.confidence label against.';
COMMENT ON COLUMN public.blog_post_jobs.metadata IS
    'Opaque caller passthrough (echoed on the job/result). Default empty object.';
COMMENT ON COLUMN public.blog_post_jobs.callback_url IS
    'Optional URL for a best-effort POST of the result when the job completes.';
COMMENT ON COLUMN public.blog_post_jobs.conversation_id IS
    'Provenance: the throwaway conversation created for this job (owned by the editorial bot). No FK — provenance ref only.';
COMMENT ON COLUMN public.blog_post_jobs.workspace_item_id IS
    'Provenance: the generated workspace_items.item_id captured from workspace_item_created. No FK — provenance ref only.';
COMMENT ON COLUMN public.blog_post_jobs.post_id IS
    'Provenance: the resulting blog_posts.post_id. No FK — the job record must survive independent of the post.';
COMMENT ON COLUMN public.blog_post_jobs.result IS
    'The full result payload returned to the caller (post_id, token, url, confidence, content_md, references, ...).';
COMMENT ON COLUMN public.blog_post_jobs.error IS
    'On failure: {code, message, retryable}. e.g. generation_failed / generation_timeout (retryable=true).';
COMMENT ON COLUMN public.blog_post_jobs.attempts IS
    'Number of processing attempts (incremented on each pickup); supports the startup catch-up sweep for stuck jobs.';

COMMIT;
