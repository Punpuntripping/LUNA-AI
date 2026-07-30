-- ============================================================================
-- Migration 104 — the library unlock ledger
-- Plan: .claude/plans/access_tiers_gating.md  PART 3 "Migration 103" + §1.2 + §1.2.1
--
-- THE CONTRACT (read §1.2 of the plan before touching this table):
--   * Unlocks are PERMANENT. One row per user per item, FOREVER.
--     `ON CONFLICT DO NOTHING` makes re-opens free for all time.
--   * `period_key` records WHICH period paid for the unlock. It is what the
--     per-period allowance counts — it is NOT part of the uniqueness key.
--   * Access predicate: row exists AND (plan is paid OR period_key = current).
--     A lapsed user keeps the shelf frozen but intact; re-upgrading restores it.
--   * This table is MONEY. It is inserted once and never updated. Behavioural
--     counters (use_count, last_used_at) live on `library_items` (migration 106)
--     precisely so no page view ever writes to the cost ledger.
--
-- `cost` implements the weighted charge of §1.2.1 — one unlock must not mean
-- both "a paragraph" and "a 716-article statute". The quota therefore sums
-- `cost`, never `count(*)`.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.library_unlocks (
    unlock_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    content_type text NOT NULL,                    -- regulation|article|judgment|circular|form
    content_id   text NOT NULL,                    -- matches seo_item_meta.content_id
    period_key   text NOT NULL,                    -- the period CHARGED (see §3.1 / D8)
    cost         integer NOT NULL DEFAULT 1,       -- weighted charge, §1.2.1
    surface      text NOT NULL DEFAULT 'library',  -- 'library' | 'reference' (analytics only)
    unlocked_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT library_unlocks_cost_positive CHECK (cost >= 1),
    UNIQUE (user_id, content_type, content_id)
);

COMMENT ON TABLE public.library_unlocks IS
  'Permanent, idempotent, append-only ledger of library items a user has '
  'unlocked. One row per user per item forever; period_key records which '
  'period was charged. Never UPDATE this table — it is the billing record.';

COMMENT ON COLUMN public.library_unlocks.surface IS
  'Where the unlock happened: library page or chat reference panel. Analytics '
  'ONLY — it must never affect the charge, or the reference panel becomes a '
  'bypass again.';

COMMENT ON COLUMN public.library_unlocks.cost IS
  'Weighted charge (§1.2.1): article/judgment/circular/form = 1; '
  'regulation = clamp(ceil(n_articles/25), 1, 8).';

CREATE INDEX IF NOT EXISTS idx_library_unlocks_user_period
    ON public.library_unlocks (user_id, period_key);
CREATE INDEX IF NOT EXISTS idx_library_unlocks_item
    ON public.library_unlocks (content_type, content_id);

ALTER TABLE public.library_unlocks ENABLE ROW LEVEL SECURITY;
-- No policies: service-role only, matching the llm_calls ledger convention (058).
REVOKE ALL ON public.library_unlocks FROM anon, authenticated;
