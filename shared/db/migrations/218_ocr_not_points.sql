-- ════════════════════════════════════════════════════════════════════════════
-- 218 — OCR costs PAGES only, never points
-- ════════════════════════════════════════════════════════════════════════════
--
-- Depends on: 137 (the current get_user_usage_windows body — reproduced
-- verbatim below from the LIVE definition, 2026-11-16, except the marked lines).
-- Idempotent: CREATE OR REPLACE with a byte-identical signature, so
-- get_user_quota_state / user_subscriptions_live are not rebuilt and grants stay.
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- The OCR runner writes the real Mistral price on its ledger row
-- (llm_calls.subtype = 'ocr_extraction', cost_usd = pages / 1000) — correct for
-- COGS. But this function summed EVERY cost_usd into the points windows, so a
-- file was billed twice: once in OCR pages (its own meter) and again in points.
-- /learn/usage-limits promises «استخراج نص من ملف — لا يُخصم من النقاط».
--
-- It stayed small while OCR read at most 30 pages a file (≤ 3 points). The page
-- budget (2026-11-16, d8072dc) lets one file read ⌊⅔ × plan pages⌋ — 100 pages
-- on Pro = 10 points of a 20-point session, ~333 on Max = 33 points. Live case
-- 2026-11-16: a Pro user's 132-page PDF took 100 pages AND 10 points; their
-- session hit 19.9 / 20 on a turn whose reply was a clarifying question.
--
-- OWNER DECISION 2026-11-16: OCR has nothing to do with points.
--
-- THE CHANGE ───────────────────────────────────────────────────────────────────
-- Rows with subtype 'ocr_extraction' are left out of every POINTS expression:
--   session_cost, weekly_cost, weekly_oldest, monthly_cost, monthly_oldest.
-- Untouched:
--   * cost_usd on the ledger rows — the real Mistral spend stays for COGS.
--   * ocr_pages / ocr_oldest — the OCR meter itself.
--   * the anchor CTEs (calls/flagged/burst_start/tiles/sess) and session_oldest
--     — an OCR call is still ACTIVITY that can start the 5h block; only its
--     cost stops counting. (137's split, unchanged: filtering the anchor moves
--     the clock.)
-- Retroactive by construction: usage is derived, so every window that held
-- OCR rows drops by their points the moment this is applied. No backfill.
--
-- `IS DISTINCT FROM`, not `<>`: subtype is NULL on most rows, and NULL <> 'x'
-- is NULL — it would silently drop every ordinary agent call from the sums.

BEGIN;

CREATE OR REPLACE FUNCTION public.get_user_usage_windows(p_user_id uuid)
 RETURNS TABLE(session_cost double precision, weekly_cost double precision, ocr_pages bigint, session_oldest timestamp with time zone, weekly_oldest timestamp with time zone, ocr_oldest timestamp with time zone, monthly_cost double precision, monthly_oldest timestamp with time zone)
 LANGUAGE sql
 STABLE
AS $function$
    WITH RECURSIVE
    -- The reset floor, resolved once. Coalesced to -infinity so that "never
    -- reset" and "no subscription row at all" both mean "count everything".
    usage_reset AS (
        SELECT COALESCE(
                   (SELECT s.usage_reset_at
                      FROM public.user_subscriptions s
                     WHERE s.user_id = p_user_id),
                   '-infinity'::timestamptz) AS reset_at
    ),
    -- Anchor CTEs (083). UNFILTERED by usage_reset — filtering them would move
    -- the 5h block's start and hand the user a brand-new session.
    -- 218: `subtype` is carried ONLY so the session SUM can drop OCR rows; the
    -- anchor still sees every call (OCR is activity, just not points).
    calls AS (
        SELECT created_at, cost_usd, subtype
        FROM public.llm_calls
        WHERE user_id = p_user_id
          AND created_at >= now() - interval '30 days'
    ),
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
    sess AS (
        SELECT CASE WHEN a IS NOT NULL AND now() < a + interval '5 hours'
                    THEN a END AS anchor
        FROM (SELECT max(anchor) AS a FROM tiles) m
    )
    SELECT
        -- Session points: lower bound moves to the reset, UPPER bound stays
        -- anchor + 5h. Same window, same expiry, zero spend inside it.
        -- 218: OCR rows excluded — pages are their only charge.
        COALESCE((SELECT SUM(c.cost_usd)
                    FROM calls c, sess s, usage_reset u
                   WHERE s.anchor IS NOT NULL
                     AND c.subtype IS DISTINCT FROM 'ocr_extraction'
                     AND c.created_at >= GREATEST(s.anchor, u.reset_at)
                     AND c.created_at <  s.anchor + interval '5 hours'), 0)::double precision,
        COALESCE(SUM(cost_usd) FILTER (
            WHERE subtype IS DISTINCT FROM 'ocr_extraction'                  -- 218
              AND created_at >= GREATEST(now() - interval '7 days', ur.reset_at)), 0)::double precision,
        -- OCR pages — FILTERED as of 137 (131 excluded these). A purchase buys
        -- back the page allowance; the 30-day roll-off stays for everyone who
        -- has not bought anything.
        COALESCE(SUM(pages_used) FILTER (
            WHERE created_at >= GREATEST(now() - interval '30 days', ur.reset_at)), 0)::bigint,
        -- The session ANCHOR, not an oldest call. Unfiltered on purpose.
        (SELECT anchor FROM sess),
        -- 218: matches the weekly SUM's filter (an OCR row frees no points
        -- when it rolls off, so it must not set the weekly countdown).
        MIN(created_at) FILTER (
            WHERE subtype IS DISTINCT FROM 'ocr_extraction'                  -- 218
              AND created_at >= GREATEST(now() - interval '7 days', ur.reset_at)),
        -- Matches the OCR sum's lower bound exactly (137): an unfiltered oldest
        -- beside a filtered sum is a lie with no visible symptom.
        MIN(created_at) FILTER (
            WHERE pages_used > 0
              AND created_at >= GREATEST(now() - interval '30 days', ur.reset_at)),
        COALESCE(SUM(cost_usd) FILTER (
            WHERE subtype IS DISTINCT FROM 'ocr_extraction'                  -- 218
              AND created_at >= ur.reset_at), 0)::double precision,
        MIN(created_at) FILTER (
            WHERE subtype IS DISTINCT FROM 'ocr_extraction'                  -- 218
              AND created_at >= ur.reset_at)
    FROM public.llm_calls
    CROSS JOIN usage_reset ur
    WHERE user_id = p_user_id
      AND created_at >= now() - interval '30 days';
$function$;

COMMENT ON FUNCTION public.get_user_usage_windows(uuid) IS
  'Rolling usage windows straight off the llm_calls ledger: 5h session (fixed '
  'anchor, 083), 7d, and 30d for both OCR pages and points (129). Every SUM and '
  'its matching oldest-call timestamp — points AND OCR pages (137) — starts at '
  'user_subscriptions.usage_reset_at when set. The session ANCHOR alone stays '
  'unfiltered, so a purchase zeroes the usage without moving any clock. '
  'OCR rows (subtype ocr_extraction) count in OCR pages ONLY, never in points '
  '(218) — their cost_usd is real Mistral spend kept for COGS.';

REVOKE EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) TO service_role;

-- ── self-test ────────────────────────────────────────────────────────────────
-- For every user with an OCR row in the last 7 days, the weekly points must
-- equal an independent sum of their NON-OCR cost over the same floor. Raises
-- (→ the whole migration rolls back) on any mismatch or if no user is checked
-- while OCR rows exist.
DO $$
DECLARE
    r          record;
    v_got      double precision;
    v_want     double precision;
    v_checked  int := 0;
BEGIN
    FOR r IN
        SELECT DISTINCT user_id
          FROM public.llm_calls
         WHERE subtype = 'ocr_extraction'
           AND cost_usd > 0
           AND created_at >= now() - interval '7 days'
           AND user_id IS NOT NULL
    LOOP
        SELECT w.weekly_cost INTO v_got
          FROM public.get_user_usage_windows(r.user_id) w;

        SELECT COALESCE(SUM(c.cost_usd), 0)::double precision INTO v_want
          FROM public.llm_calls c
         WHERE c.user_id = r.user_id
           AND c.subtype IS DISTINCT FROM 'ocr_extraction'
           AND c.created_at >= GREATEST(
                 now() - interval '7 days',
                 COALESCE((SELECT s.usage_reset_at FROM public.user_subscriptions s
                            WHERE s.user_id = r.user_id), '-infinity'::timestamptz));

        IF abs(v_got - v_want) > 1e-9 THEN
            RAISE EXCEPTION '218 self-test: user % weekly_cost % <> non-OCR sum %',
                r.user_id, v_got, v_want;
        END IF;
        v_checked := v_checked + 1;
    END LOOP;
    RAISE NOTICE '218 self-test OK: % user(s) with OCR rows this week, weekly points exclude OCR',
        v_checked;
END $$;

COMMIT;
