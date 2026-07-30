-- ============================================================================
-- Migration 106 — «مكتبتي»: the user's library shelf
-- Plan: .claude/plans/access_tiers_gating.md  PART 5B (§5B.2, §5B.3)
--
-- Two tables, two jobs — do not merge them:
--   library_unlocks (104) is MONEY   — inserted once, never updated.
--   library_items   (this)  is BEHAVIOUR — upserted on every use.
-- If usage counters lived on the ledger, every quota count and cost audit would
-- silently be measuring something else.
--
-- Populated BOTH ways (§5B.2):
--   implicit — opening an item shelves it, gated or not. This is what makes the
--              الخدمات tab work: services are never gated and so never produce an
--              unlock row, but opening one is enough to shelve it.
--   explicit — a «حفظ» action pins an item the user has not opened.
-- Explicit saving is free at every tier: it stores a POINTER, never content, so
-- it grants no access and costs no unlock.
--
-- ⚠ ISR TRAP (§5B.3): the use_count upsert must NEVER run in a cached server
-- render. It rides the authed client call — the reveal request for gated items,
-- POST /library/mine/use for free ones. A server-side write would either poison
-- the shared cache or be skipped on every cache hit and undercount silently.
--
-- Reading history is sensitive — it records what a lawyer researched. RLS on,
-- service-role only, and covered by the account-deletion cascade.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.library_items (
    item_row_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    content_type    text NOT NULL,                    -- regulation|article|judgment|circular|form|service|calculator
    content_id      text NOT NULL,                    -- matches seo_item_meta.content_id
    source          text NOT NULL DEFAULT 'auto',     -- 'auto' (opened) | 'manual' (saved)
    use_count       integer NOT NULL DEFAULT 0,
    first_used_at   timestamptz,
    last_used_at    timestamptz,
    saved_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT library_items_source_valid CHECK (source IN ('auto', 'manual')),
    UNIQUE (user_id, content_type, content_id)
);

COMMENT ON TABLE public.library_items IS
  'The user''s مكتبتي shelf: every library item they opened (source=auto) or '
  'deliberately pinned (source=manual), with usage counters. Behaviour, not '
  'money — see library_unlocks for the billing ledger.';

COMMENT ON COLUMN public.library_items.use_count IS
  'Times the user opened this item. Incremented ONLY from an authed client '
  'call, never from a cached server render (ISR trap, plan §5B.3). Ranks the '
  '«الأكثر استخداماً» sort.';

COMMENT ON COLUMN public.library_items.source IS
  '''auto'' = shelved implicitly by opening it; ''manual'' = explicitly pinned '
  'via «حفظ». A manual pin is free at every tier and grants no access.';

-- Default sort is recency; secondary sort is «الأكثر استخداماً».
CREATE INDEX IF NOT EXISTS idx_library_items_user_recent
    ON public.library_items (user_id, last_used_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_library_items_user_used
    ON public.library_items (user_id, use_count DESC);
CREATE INDEX IF NOT EXISTS idx_library_items_user_type
    ON public.library_items (user_id, content_type);

ALTER TABLE public.library_items ENABLE ROW LEVEL SECURITY;
-- No policies: service-role only, same convention as library_unlocks.
REVOKE ALL ON public.library_items FROM anon, authenticated;
