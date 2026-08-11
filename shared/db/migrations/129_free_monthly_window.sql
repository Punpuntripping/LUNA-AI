-- 129: the free plan gets ONE 30-day window — no 5-hour, no weekly wall.
--
-- WHY
-- ---
-- A free user who hit the session cap was told «يُعاد الاحتساب خلال ٤ ساعات».
-- That sentence is an argument AGAINST subscribing: wait five hours and it is
-- free again. The whole point of a free tier's ceiling is to be the moment a
-- paid plan becomes the obvious next step, and a countdown measured in hours
-- destroys that. Pooled into a single rolling 30-day window the same block
-- reads «يُعاد الاحتساب خلال ٢٩ يوم» — a real decision point.
--
-- The allowance itself is UNCHANGED at `points_monthly = 5` (owner decision,
-- 2026-08-11). Note this is a real tightening in practice: `points_weekly = 5`
-- used to refill every 7 days, so the effective ceiling was ~21 points/month.
-- It is now 5. Roughly one to three real turns before the wall.
--
-- WHAT THIS TOUCHES BEYOND `free`
-- -------------------------------
-- Re-enabling the monthly ord window in `quota.check()` would otherwise start
-- enforcing `pro = 300` / `max = 1000` — limits that have sat in the table
-- unenforced since the monthly window was retired. Silently capping people who
-- have already paid is not part of this change, so both are set to NULL (the
-- gate skips a NULL limit entirely). Their session + weekly windows are what
-- governs them, exactly as before. Restoring a monthly cap for paid plans is a
-- deliberate, separately-priced decision — put the number back to turn it on.
--
-- SHAPE CHANGE
-- ------------
-- `get_user_usage_windows` never computed a monthly points figure — only OCR
-- pages used the 30-day span. Both RPCs gain `monthly_cost` / `monthly_oldest`,
-- APPENDED to the end of the return tables so every existing positional
-- consumer keeps its offsets. That is a return-type change, so the functions
-- must be dropped and recreated rather than CREATE OR REPLACE'd — and
-- `user_subscriptions_live` depends on `get_user_quota_state`, so it is dropped
-- first and rebuilt last.
--
-- ⚠ The view body below was read from `pg_get_viewdef` on 2026-08-11 and is
-- migration **120**'s shape (library meter + renewal_cancelled_at). Anything
-- that touches this view again must re-read the live definition first —
-- rebuilding it from an older migration silently drops operator columns.
--
-- The new monthly points figures are surfaced on the view too: an operator
-- looking at a blocked free user needs to see the window that blocked them.

BEGIN;

-- ── 1. Drop in dependency order ─────────────────────────────────────────────

DROP VIEW IF EXISTS public.user_subscriptions_live;
DROP FUNCTION IF EXISTS public.get_user_quota_state(uuid);
DROP FUNCTION IF EXISTS public.get_user_usage_windows(uuid);

-- ── 2. Usage windows + the monthly points span ──────────────────────────────

CREATE FUNCTION public.get_user_usage_windows(p_user_id uuid)
RETURNS TABLE(
    session_cost   double precision,
    weekly_cost    double precision,
    ocr_pages      bigint,
    session_oldest timestamptz,
    weekly_oldest  timestamptz,
    ocr_oldest     timestamptz,
    monthly_cost   double precision,
    monthly_oldest timestamptz
)
LANGUAGE sql
STABLE
AS $function$
    WITH RECURSIVE
    calls AS (
        SELECT created_at, cost_usd
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
        COALESCE((SELECT SUM(c.cost_usd) FROM calls c, sess s
                  WHERE s.anchor IS NOT NULL
                    AND c.created_at >= s.anchor
                    AND c.created_at < s.anchor + interval '5 hours'), 0)::double precision,
        COALESCE(SUM(cost_usd)  FILTER (WHERE created_at >= now() - interval '7 days'),  0)::double precision,
        COALESCE(SUM(pages_used) FILTER (WHERE created_at >= now() - interval '30 days'), 0)::bigint,
        (SELECT anchor FROM sess),
        MIN(created_at) FILTER (WHERE created_at >= now() - interval '7 days'),
        MIN(created_at) FILTER (WHERE pages_used > 0 AND created_at >= now() - interval '30 days'),
        -- The monthly window IS the outer WHERE clause, so these need no FILTER:
        -- the scan is already the rolling last 30 days. No extra pass over
        -- llm_calls is added by this migration.
        COALESCE(SUM(cost_usd), 0)::double precision,
        MIN(created_at)
    FROM public.llm_calls
    WHERE user_id = p_user_id
      AND created_at >= now() - interval '30 days';
$function$;

COMMENT ON FUNCTION public.get_user_usage_windows(uuid) IS
  'Rolling usage windows straight off the llm_calls ledger: 5h session (fixed '
  'anchor, 083), 7d, and 30d for both OCR pages and points (129).';

-- ── 3. Quota state — pass the monthly span through ──────────────────────────

CREATE FUNCTION public.get_user_quota_state(p_user_id uuid)
RETURNS TABLE(
    locked boolean, plan_id text, plan_name_ar text,
    expires_at timestamptz, is_expired boolean,
    effective_plan_id text, effective_name_ar text,
    points_session integer, points_weekly integer, points_monthly integer,
    ocr_pages_monthly integer, web_calls_monthly integer,
    session_cost double precision, weekly_cost double precision,
    ocr_pages bigint,
    session_oldest timestamptz, weekly_oldest timestamptz, ocr_oldest timestamptz,
    library_unlocks_limit integer, library_unlocks_used integer,
    library_period_key text, library_period_resets_at timestamptz,
    monthly_cost double precision, monthly_oldest timestamptz
)
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'public'
AS $function$
    SELECT
        (ep.plan_id IS NULL)                                          AS locked,
        s.plan_id,
        p.name_ar                                                     AS plan_name_ar,
        s.expires_at,
        (s.expires_at IS NOT NULL AND s.expires_at <= now())          AS is_expired,
        ep.plan_id                                                    AS effective_plan_id,
        ep.name_ar                                                    AS effective_name_ar,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.points_session_override,    ep.points_session)    END,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.points_weekly_override,     ep.points_weekly)     END,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.points_monthly_override,    ep.points_monthly)    END,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.ocr_pages_monthly_override, ep.ocr_pages_monthly) END,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.web_calls_monthly_override, ep.web_calls_monthly) END,
        w.session_cost,
        w.weekly_cost,
        w.ocr_pages,
        w.session_oldest,
        w.weekly_oldest,
        w.ocr_oldest,
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.library_unlocks_override, ep.library_unlocks_period) END,
        lu.used,
        pk.period_key,
        pk.resets_at,
        w.monthly_cost,
        w.monthly_oldest
    FROM public.user_subscriptions s
    LEFT JOIN public.plans p  ON p.plan_id = s.plan_id
    LEFT JOIN public.plans ep ON ep.plan_id = CASE
            WHEN s.plan_id IS NULL                                   THEN NULL
            WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'free'
            ELSE s.plan_id END
    CROSS JOIN LATERAL public.get_user_usage_windows(p_user_id) w
    CROSS JOIN LATERAL (
        SELECT CASE
            WHEN ep.duration_days IS NULL OR s.started_at IS NULL THEN NULL
            ELSE floor(
                     extract(epoch FROM (now() - s.started_at))
                     / (ep.duration_days * 86400)
                 )::bigint
        END AS idx
    ) pi
    CROSS JOIN LATERAL (
        SELECT
            CASE
                WHEN ep.plan_id IS NULL THEN NULL
                WHEN pi.idx IS NOT NULL THEN
                     ep.plan_id || ':'
                     || to_char(s.started_at AT TIME ZONE 'UTC', 'YYYYMMDD') || ':'
                     || pi.idx::text
                ELSE 'free:' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMM')
            END AS period_key,
            CASE
                WHEN ep.plan_id IS NULL THEN NULL
                WHEN pi.idx IS NOT NULL THEN
                     s.started_at
                     + make_interval(days => ((pi.idx + 1) * ep.duration_days)::int)
                ELSE (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month')
                     AT TIME ZONE 'UTC'
            END AS resets_at
    ) pk
    CROSS JOIN LATERAL (
        SELECT COALESCE(SUM(u.cost), 0)::integer AS used
        FROM public.library_unlocks u
        WHERE u.user_id = p_user_id
          AND pk.period_key IS NOT NULL
          AND u.period_key = pk.period_key
    ) lu
    WHERE s.user_id = p_user_id;
$function$;

COMMENT ON FUNCTION public.get_user_quota_state(uuid) IS
  'THE quota source: plan (with expiry→free fallback), every limit after '
  'per-user overrides, and current usage for each window. Read by both the gate '
  'and the usage dialog so what is shown is exactly what is enforced.';

-- ── 4. Operator view — rebuilt on the widened alias list ────────────────────

-- ⚠ security_invoker = true is NOT optional and NOT decoration. 120 created this
-- view with it; the first draft of 129 recreated the view WITHOUT it and that
-- silently flipped a users⋈subscriptions view to security-DEFINER in prod on
-- 2026-08-11. 132 §6 predicted exactly this. It was repaired by 129a; the option
-- is restored here so a fresh apply of 129 never reintroduces it.
CREATE VIEW public.user_subscriptions_live
WITH (security_invoker = true) AS
SELECT
    s.user_id,
    u.email,
    q.plan_id,
    q.plan_name_ar,
    CASE
        WHEN q.locked     THEN 'locked'::text
        WHEN q.is_expired THEN 'expired'::text
        ELSE 'active'::text
    END AS status,
    q.is_expired,
    q.effective_plan_id,
    q.effective_name_ar,
    round((q.session_cost * 100::double precision)::numeric, 2) AS points_session_used,
    q.points_session AS points_session_limit,
    round((q.weekly_cost * 100::double precision)::numeric, 2)  AS points_weekly_used,
    q.points_weekly  AS points_weekly_limit,
    round((q.monthly_cost * 100::double precision)::numeric, 2) AS points_monthly_used,
    q.points_monthly AS points_monthly_limit,
    q.ocr_pages AS ocr_pages_used,
    q.ocr_pages_monthly AS ocr_pages_limit,
    q.library_unlocks_used,
    q.library_unlocks_limit,
    q.library_period_key,
    q.library_period_resets_at,
    s.source,
    s.started_at,
    s.expires_at,
    s.renewal_cancelled_at,
    s.redeemed_code,
    s.points_session_override,
    s.points_weekly_override,
    s.points_monthly_override,
    s.ocr_pages_monthly_override,
    s.web_calls_monthly_override,
    s.library_unlocks_override,
    s.created_at,
    s.updated_at
FROM public.user_subscriptions s
JOIN public.users u ON u.user_id = s.user_id
CROSS JOIN LATERAL public.get_user_quota_state(s.user_id) q(
    locked, plan_id, plan_name_ar, expires_at, is_expired,
    effective_plan_id, effective_name_ar,
    points_session, points_weekly, points_monthly,
    ocr_pages_monthly, web_calls_monthly,
    session_cost, weekly_cost, ocr_pages,
    session_oldest, weekly_oldest, ocr_oldest,
    library_unlocks_limit, library_unlocks_used, library_period_key,
    library_period_resets_at,
    monthly_cost, monthly_oldest
);

COMMENT ON VIEW public.user_subscriptions_live IS
  'Operator-facing subscription state: derived status, every limit and its '
  'current usage (points incl. the 30-day window added in 129, OCR, library '
  'unlocks), and renewal_cancelled_at. service_role only.';

-- ── 5. Permissions — mirrors 093 / 105 / 118 ────────────────────────────────

REVOKE EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) TO service_role;

REVOKE EXECUTE ON FUNCTION public.get_user_quota_state(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_quota_state(uuid) TO service_role;

REVOKE SELECT ON public.user_subscriptions_live FROM anon, authenticated;
GRANT  SELECT ON public.user_subscriptions_live TO service_role;

-- ── 6. The plan limits themselves ───────────────────────────────────────────

-- free: one 30-day window, nothing else. points_monthly stays 5.
UPDATE public.plans
   SET points_session = NULL,
       points_weekly  = NULL
 WHERE plan_id = 'free';

-- pro / max: keep the monthly window OFF. These numbers (300 / 1000) predate
-- the window's retirement and have never been enforced; §"WHAT THIS TOUCHES"
-- above explains why turning them on is not part of this change.
UPDATE public.plans
   SET points_monthly = NULL
 WHERE plan_id IN ('pro', 'max');

COMMIT;
