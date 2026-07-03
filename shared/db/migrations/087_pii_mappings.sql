-- 087_pii_mappings.sql
-- Feature: تقنيع المعرّفات (identifier masking / وضع السرية) — per-user PII swap table.
--
-- Purpose:
--   Reversible masking of numeric identifiers + emails before user text reaches
--   external LLMs (.claude/plans/identifier_masking.md). Each user has a
--   persistent real ↔ fake mapping: same real value always swaps to the same
--   fake (prompt-cache-stable), and LLM output is decoded back by exact
--   fake-value match before anything is shown or stored.
--
-- Data model:
--   * kind: 'number' | 'email' (general digit-run rule vs the dedicated @ rule).
--   * real_value: normalized ASCII form (Arabic-Indic digits already folded).
--   * UNIQUE (user_id, real_value)  — never two fakes for one real (concurrent
--     turns race → loser re-selects the winner's row).
--   * UNIQUE (user_id, fake_value)  — decode is an exact-match lookup; a fake
--     collision would make decode ambiguous (loser regenerates).
--   * Rows are NEVER deleted on toggle-off (re-enable reuses the same fakes).
--
-- Security / RLS:
--   * DENY-ALL from clients: RLS enabled with NO policies + explicit REVOKE
--     from anon/authenticated. Only the backend (service role, bypasses RLS)
--     touches this table. real_value is plaintext PII — client visibility
--     would defeat the feature (Supabase at-rest encryption covers storage;
--     pgcrypto column encryption is optional later hardening).
--
-- Verified live-state (Supabase MCP, 2026-07-02):
--   * public.pii_mappings does not exist.
--   * public.users PK is user_id (NOT id — the plan sketch was wrong).
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS; RLS enable + REVOKE are
-- naturally re-runnable.

BEGIN;

CREATE TABLE IF NOT EXISTS public.pii_mappings (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'number',
    real_value  text NOT NULL,
    fake_value  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pii_mappings_kind_chk CHECK (kind IN ('number', 'email')),
    CONSTRAINT pii_mappings_user_real_uniq UNIQUE (user_id, real_value),
    CONSTRAINT pii_mappings_user_fake_uniq UNIQUE (user_id, fake_value)
);

-- Per-turn load is "SELECT * WHERE user_id = ?" — one index serves it; the two
-- UNIQUE constraints above already index the lookup/conflict paths.
CREATE INDEX IF NOT EXISTS idx_pii_mappings_user
    ON public.pii_mappings (user_id);

ALTER TABLE public.pii_mappings ENABLE ROW LEVEL SECURITY;

-- Deny-all: no policies are created on purpose. Belt-and-suspenders: strip the
-- default PostgREST grants so even a future accidental policy can't expose it.
REVOKE ALL ON public.pii_mappings FROM anon, authenticated;

COMMENT ON TABLE public.pii_mappings IS
    'Per-user PII swap table for وضع السرية (identifier masking). Service-role only — deny-all RLS, no client access. real_value is normalized ASCII; rows persist across toggle-off.';

COMMIT;
