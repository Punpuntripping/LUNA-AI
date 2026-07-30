-- 099_anon_questions.sql
-- Date: 2026-07-23
-- Part of SEO Public Library Phase 4 — .claude/plans/seo_public_library.md
--   (§ "Phase 4" → اسأل ريحان popup; § "Backend surface" → POST /public/ask,
--    POST /ask/claim).
--
-- Purpose:
--   public.anon_questions — server-side storage for the anonymous «اسأل ريحان»
--   popup (Phase 4 conversion layer). An anon visitor asks 1 question/session
--   about the page they are on; the answer is generated (tier_2 flash, grounded
--   ONLY in the current page's chunks, capped tokens) and stored IN FULL here.
--
--   TRUNCATION / TRUST BOUNDARY (critical — mirrors the library gate philosophy):
--     * answer_md holds the COMPLETE generated answer server-side.
--     * the anon client only ever RECEIVES the first visible_prefix_chars of it
--       (default 220) — «سجّل مجاناً لعرض الإجابة كاملة». The remainder NEVER
--       reaches an anon client (server-side truncation, like the gated corpus —
--       plan § Gate mechanics).
--     * the full answer is revealed ONLY via the authed claim endpoint
--       (POST /ask/claim → post-login intent claim_anon_answer), which sets
--       claimed_by_user_id + claimed_at and returns answer_md in full (the
--       "continuity moment").
--
--   Abuse controls (session cap, IP rate limit, Turnstile on 2nd+ attempt,
--   ANON_ASK_ENABLED kill switch, ANON_ASK_DAILY_BUDGET) live in the backend, not
--   this schema. Anon spend is attributed via the llm_calls ledger, not here.
--
-- User reference — DELIBERATELY NO FK on claimed_by_user_id:
--   Convention in this codebase splits two ways —
--     * transactional/owned tables FK to public.users(user_id) (e.g. 004, 007,
--       070, 079, 087, 090);
--     * append-only / ledger-style tables use a bare `uuid` with NO FK
--       (058_llm_calls_ledger.user_id, 060_paused_runs.user_id).
--   anon_questions is append-only anon-attribution data whose user link is a
--   soft, post-hoc "claim". I chose the LEDGER style (bare uuid, no FK) to match
--   058/060: (a) rows are created with claimed_by_user_id NULL and only a minority
--   are ever claimed; (b) a hard FK would tempt an ON DELETE CASCADE that could
--   destroy this record on account deletion — deletion/retention is handled
--   explicitly by purge_user_data (090), not by cascade, so anon Q&A survives as
--   independent analytics/ledger data. claimed_by_user_id therefore = plain uuid.
--
-- Security / RLS:
--   NEW table → RLS ENABLED, no policies (default-deny for anon/authenticated).
--   Written + read ONLY by the backend service role (the /public/ask + /ask/claim
--   endpoints) — the service role is what enforces the visible-prefix truncation.
--   REVOKE ALL from anon, authenticated (same deny-all convention as 087 / 095 /
--   096 / 097 / 098). RLS ON here is explicitly required by plan § Phase 4.
--
-- Dependencies:
--   - 001_extensions.sql (pgcrypto → gen_random_uuid).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; RLS enable + REVOKE re-runnable.

BEGIN;

------------------------------------------------------------------------
-- 1. Anonymous «اسأل ريحان» question/answer storage.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.anon_questions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_key          text NOT NULL,
    page_type            text NOT NULL,
    page_id              text NOT NULL,
    question             text NOT NULL,
    answer_md            text,
    visible_prefix_chars int  NOT NULL DEFAULT 220,
    model                text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    claimed_by_user_id   uuid,
    claimed_at           timestamptz
);

COMMENT ON TABLE public.anon_questions IS
    'Server-side storage for the anonymous اسأل ريحان popup (099, Phase 4). '
    'answer_md is the FULL generated answer; the anon client receives only the '
    'first visible_prefix_chars — the rest is revealed only via the authed claim '
    'endpoint (POST /ask/claim). Deny-all RLS; service-role only.';
COMMENT ON COLUMN public.anon_questions.session_key IS
    'Anonymous session identifier (cookie/local) used for the 1-question/session '
    'cap and to list a visitor''s asks before signup.';
COMMENT ON COLUMN public.anon_questions.page_type IS
    'Context page kind the question was asked on: '
    'regulation|article|service|circular|judgment|blog|form|calculator.';
COMMENT ON COLUMN public.anon_questions.page_id IS
    'Context page key within page_type (slug/uuid/derived key) — the grounding '
    'source (answer is grounded ONLY in this page''s chunks).';
COMMENT ON COLUMN public.anon_questions.answer_md IS
    'FULL stored answer markdown. NEVER sent to an anon client beyond the visible '
    'prefix; revealed in full only on authed claim.';
COMMENT ON COLUMN public.anon_questions.visible_prefix_chars IS
    'Number of leading characters of answer_md the anon client may see (default '
    '220). Server truncates to this before responding to anon.';
COMMENT ON COLUMN public.anon_questions.model IS
    'Model slug that generated the answer (tier_2 flash) — for ledger/debug.';
COMMENT ON COLUMN public.anon_questions.claimed_by_user_id IS
    'Set on post-signup claim (claim_anon_answer intent). Bare uuid, NO FK — '
    'ledger-style like 058/060; retention handled by purge_user_data (090), not '
    'by cascade.';
COMMENT ON COLUMN public.anon_questions.claimed_at IS
    'Timestamp the answer was claimed by an authed user (NULL = unclaimed).';

------------------------------------------------------------------------
-- 2. Lookup indexes.
------------------------------------------------------------------------
-- A session's asks, newest first (cap enforcement + pre-signup listing).
CREATE INDEX IF NOT EXISTS idx_anon_questions_session
    ON public.anon_questions (session_key, created_at DESC);

-- Claimed answers by user (post-signup reveal / attribution). Partial: only the
-- minority of rows that were ever claimed.
CREATE INDEX IF NOT EXISTS idx_anon_questions_claimed_by
    ON public.anon_questions (claimed_by_user_id)
    WHERE claimed_by_user_id IS NOT NULL;

------------------------------------------------------------------------
-- 3. RLS: deny-all, service-role only (no policies).
------------------------------------------------------------------------
ALTER TABLE public.anon_questions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.anon_questions FROM anon, authenticated;

COMMIT;
