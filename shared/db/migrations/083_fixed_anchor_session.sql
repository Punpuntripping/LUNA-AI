-- 083_fixed_anchor_session.sql
-- Make the points "session" window a FIXED 5-hour block anchored at the user's
-- first message, instead of a rolling "last 5 hours".
--
-- Why: the dialog (and product copy) tell users "the session starts when you
-- send your first message and lasts 5 hours". Since the 06-26 SSoT rework
-- (migration 079) the implementation was a rolling trailing-5h window, which
-- does NOT start at the first message — so the copy was inaccurate. This
-- restores the fixed-anchor semantics of the old Redis NX+TTL session, but
-- computes it entirely from the llm_calls ledger (the usage SSoT) so there is
-- no Redis state to drift or lose.
--
-- Model: sessions tile forward in fixed 5h blocks. The first block is anchored
-- at the first message after a >= 5h idle gap; while a block is open every call
-- counts toward it; the block resets exactly 5h after its anchor; the next
-- message at-or-after that opens a fresh block. The "current" session is the
-- last tile, and only counts if it is still open (now < anchor + 5h).
--
-- Only the SESSION changes. weekly (rolling 7d) and ocr (rolling 30d) are
-- unchanged. The function keeps the same name, signature and column order, so
-- the Python gate + report (shared/quota) need no change: session_oldest now
-- carries the active anchor (NULL when no session is open), and resets_at is
-- still computed as anchor + window in Python.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_user_usage_windows(p_user_id uuid)
RETURNS TABLE(
    session_cost   double precision,
    weekly_cost    double precision,
    ocr_pages      bigint,
    session_oldest timestamptz,   -- active fixed-anchor of the session (NULL if none open)
    weekly_oldest  timestamptz,
    ocr_oldest     timestamptz
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE
    calls AS (
        SELECT created_at, cost_usd
        FROM public.llm_calls
        WHERE user_id = p_user_id
          AND created_at >= now() - interval '30 days'
    ),
    -- Start of the most recent burst: the latest call whose previous call was
    -- >= 5h earlier (or which has no previous call within the 30-day scan).
    flagged AS (
        SELECT created_at,
               created_at - lag(created_at) OVER (ORDER BY created_at) AS gap
        FROM calls
    ),
    burst_start AS (
        SELECT max(created_at) AS f
        FROM flagged
        WHERE gap IS NULL OR gap >= interval '5 hours'
    ),
    -- Tile the burst forward into fixed 5h blocks, each anchored at the first
    -- call at-or-after the previous block's end (mirrors the old Redis NX+TTL
    -- session). The last anchor is the current session's anchor.
    tiles AS (
        SELECT (SELECT f FROM burst_start) AS anchor
        WHERE (SELECT f FROM burst_start) IS NOT NULL
        UNION ALL
        SELECT (SELECT min(c.created_at) FROM calls c
                WHERE c.created_at >= t.anchor + interval '5 hours')
        FROM tiles t
        WHERE EXISTS (SELECT 1 FROM calls c
                      WHERE c.created_at >= t.anchor + interval '5 hours')
    ),
    -- The active session anchor: the last tile, but only if it is still open
    -- (now < anchor + 5h). Otherwise the session has expired → no active window.
    sess AS (
        SELECT CASE WHEN a IS NOT NULL AND now() < a + interval '5 hours'
                    THEN a END AS anchor
        FROM (SELECT max(anchor) AS a FROM tiles) m
    )
    SELECT
        -- session: cost inside the active fixed-anchor 5h block (0 if expired)
        COALESCE((SELECT SUM(c.cost_usd) FROM calls c, sess s
                  WHERE s.anchor IS NOT NULL
                    AND c.created_at >= s.anchor
                    AND c.created_at < s.anchor + interval '5 hours'), 0)::double precision,
        -- weekly: rolling last 7 days (unchanged)
        COALESCE(SUM(cost_usd)  FILTER (WHERE created_at >= now() - interval '7 days'),  0)::double precision,
        -- ocr: rolling last 30 days, pages (unchanged)
        COALESCE(SUM(pages_used) FILTER (WHERE created_at >= now() - interval '30 days'), 0)::bigint,
        (SELECT anchor FROM sess),     -- session_oldest = active anchor (reset = anchor + 5h)
        MIN(created_at) FILTER (WHERE created_at >= now() - interval '7 days'),
        MIN(created_at) FILTER (WHERE pages_used > 0 AND created_at >= now() - interval '30 days')
    FROM public.llm_calls
    WHERE user_id = p_user_id
      AND created_at >= now() - interval '30 days';
$$;

COMMENT ON FUNCTION public.get_user_usage_windows(uuid) IS
    'Usage windows from the llm_calls ledger (the usage SSoT): session = FIXED '
    '5h block anchored at the first message (tiles forward in 5h steps; '
    'session_oldest = active anchor, NULL when no block is open), weekly = '
    'rolling 7d cost (USD), ocr = rolling 30d pages. Points derived in Python '
    '(1 USD = 100). resets_at = anchor/oldest + window.';
