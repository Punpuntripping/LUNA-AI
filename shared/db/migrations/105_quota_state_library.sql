-- ============================================================================
-- Migration 105 — extend get_user_quota_state with the library meter
-- Plan: .claude/plans/access_tiers_gating.md  PART 3 "Migration 104" + §3.1
--
-- Adds three columns to the quota-state RPC (which remains THE quota source of
-- truth, per migration 093):
--   library_unlocks_limit     — effective limit after expired→free fallback + override
--                               (NULL = unlimited, 0 = locked account)
--   library_unlocks_used      — SUM(cost) of unlocks charged to the CURRENT period
--   library_period_key        — the derived period key, so Python never re-derives it
--   library_period_resets_at  — when the current period rolls over (drives the
--                               Arabic «يتجدّد رصيدك …» copy). Derived here for the
--                               same reason as the key: one derivation, no drift.
--
-- period_key derivation (§3.1) uses the EFFECTIVE plan `ep`, so an expired
-- subscription automatically falls back to the free calendar month:
--   plan with duration_days : '{plan}:{started_at:YYYYMMDD}:{period_index}'
--                             period_index = floor((now - started_at) / duration)
--   free / no duration      : 'free:{YYYYMM}'   (UTC calendar month — user
--                                                decision 2026-07-27)
--
-- Storing the key on each ledger row means quota counting is a plain equality
-- filter: no window arithmetic at read time, and no drift between the check and
-- the count.
--
-- The RETURNS TABLE signature changes, so the function must be dropped and
-- recreated — and `user_subscriptions_live` pins the old column list in its
-- LATERAL alias, so it is dropped and recreated too.
-- ============================================================================

DROP VIEW IF EXISTS public.user_subscriptions_live;
DROP FUNCTION IF EXISTS public.get_user_quota_state(uuid);

CREATE FUNCTION public.get_user_quota_state(p_user_id uuid)
RETURNS TABLE(
    locked                boolean,
    plan_id               text,
    plan_name_ar          text,
    expires_at            timestamptz,
    is_expired            boolean,
    effective_plan_id     text,
    effective_name_ar     text,
    points_session        integer,
    points_weekly         integer,
    points_monthly        integer,
    ocr_pages_monthly     integer,
    web_calls_monthly     integer,
    session_cost          double precision,
    weekly_cost           double precision,
    ocr_pages             bigint,
    session_oldest        timestamptz,
    weekly_oldest         timestamptz,
    ocr_oldest            timestamptz,
    library_unlocks_limit     integer,
    library_unlocks_used      integer,
    library_period_key        text,
    library_period_resets_at  timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
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
        -- Library meter. NULL limit = unlimited (dev); 0 = locked account.
        CASE WHEN ep.plan_id IS NULL THEN 0
             ELSE COALESCE(s.library_unlocks_override, ep.library_unlocks_period) END,
        lu.used,
        pk.period_key,
        pk.resets_at
    FROM public.user_subscriptions s
    LEFT JOIN public.plans p  ON p.plan_id = s.plan_id
    LEFT JOIN public.plans ep ON ep.plan_id = CASE
            WHEN s.plan_id IS NULL                                   THEN NULL
            WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'free'
            ELSE s.plan_id END
    CROSS JOIN LATERAL public.get_user_usage_windows(p_user_id) w
    -- Period index for duration-based plans; NULL means "use the calendar month".
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
$$;

COMMENT ON FUNCTION public.get_user_quota_state(uuid) IS
  'THE quota source of truth: effective plan (expired→free fallback), every '
  'limit after per-user overrides, rolling points/OCR usage windows, and the '
  'library-unlock meter (limit, SUM(cost) used this period, derived period_key). '
  'Callers must NOT re-derive period_key — read library_period_key off this row.';

-- Operator view, recreated with the widened LATERAL alias list.
CREATE VIEW public.user_subscriptions_live
WITH (security_invoker = true)
AS
SELECT
    s.user_id,
    u.email,
    q.plan_id,
    q.plan_name_ar,
    CASE
        WHEN q.locked     THEN 'locked'
        WHEN q.is_expired THEN 'expired'
        ELSE 'active'
    END AS status,
    q.is_expired,
    q.effective_plan_id,
    q.effective_name_ar,
    round((q.session_cost * 100::double precision)::numeric, 2) AS points_session_used,
    q.points_session      AS points_session_limit,
    round((q.weekly_cost  * 100::double precision)::numeric, 2) AS points_weekly_used,
    q.points_weekly       AS points_weekly_limit,
    q.ocr_pages           AS ocr_pages_used,
    q.ocr_pages_monthly   AS ocr_pages_limit,
    q.library_unlocks_used  AS library_unlocks_used,
    q.library_unlocks_limit AS library_unlocks_limit,
    q.library_period_key,
    q.library_period_resets_at,
    s.source,
    s.started_at,
    s.expires_at,
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
    library_period_resets_at
);

COMMENT ON VIEW public.user_subscriptions_live IS
  'Operator-facing subscription state: derived status, every limit and its '
  'current usage (points, OCR, library unlocks). service_role only.';

-- Permissions — mirrors migration 093.
REVOKE EXECUTE ON FUNCTION public.get_user_quota_state(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_quota_state(uuid) TO service_role;
REVOKE SELECT ON public.user_subscriptions_live FROM anon, authenticated;
