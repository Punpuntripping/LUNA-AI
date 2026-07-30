-- ============================================================================
-- Migration 107 — atomic use_count increment for «مكتبتي»
-- Plan: .claude/plans/access_tiers_gating.md §5B.2 / §5B.3
--
-- §5B.2 specifies the shelf write as a single statement:
--
--   INSERT INTO library_items (...) VALUES (..., 1, now(), now())
--   ON CONFLICT (user_id, content_type, content_id) DO UPDATE
--   SET use_count = library_items.use_count + 1, last_used_at = now();
--
-- PostgREST cannot express `SET use_count = use_count + 1` — the column has to
-- be read, incremented in Python and written back, which loses an increment
-- whenever two uses land concurrently (a double-click, or two tabs). That is
-- only a ranking signal, never money (money is `library_unlocks`, insert-once),
-- but «الأكثر استخداماً» is a real product surface and a silently lossy counter
-- is the kind of thing nobody ever goes back and fixes.
--
-- This RPC lets the service send the plan's statement verbatim, atomically.
--
-- NOTE the deliberate asymmetries:
--   * `source` is NOT touched on conflict — an explicit 'manual' pin must never
--     be demoted back to 'auto' by a later read.
--   * `first_used_at` is set only on insert; it is a first-touch timestamp.
--   * SECURITY DEFINER + service_role only, matching the ledger convention.
--     `library_items` is RLS-on with no policies, so this is the only write path.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.record_library_item_use(
    p_user_id      uuid,
    p_content_type text,
    p_content_id   text
)
RETURNS integer
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    INSERT INTO public.library_items AS li
        (user_id, content_type, content_id, source,
         use_count, first_used_at, last_used_at, saved_at)
    VALUES
        (p_user_id, p_content_type, p_content_id, 'auto',
         1, now(), now(), now())
    ON CONFLICT (user_id, content_type, content_id) DO UPDATE
        SET use_count    = li.use_count + 1,
            last_used_at = now()
    RETURNING li.use_count;
$$;

COMMENT ON FUNCTION public.record_library_item_use(uuid, text, text) IS
  'Atomically shelve a library item and increment its use_count (plan §5B.2). '
  'Returns the new use_count. Never touches library_unlocks — that ledger is '
  'money and is insert-once. Must only ever be called from an authed, '
  'no-store request path: a call inside a cached ISR render would poison the '
  'shared cache or undercount silently (§5B.3 ISR trap).';

REVOKE EXECUTE ON FUNCTION public.record_library_item_use(uuid, text, text)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.record_library_item_use(uuid, text, text)
    TO service_role;
