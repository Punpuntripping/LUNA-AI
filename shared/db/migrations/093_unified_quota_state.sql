-- ════════════════════════════════════════════════════════════════════════════
-- 093 — unified quota state: ONE read surface for identity + limits + usage
-- ════════════════════════════════════════════════════════════════════════════
--
-- Before this migration the quota picture was assembled from two reads in two
-- places: Python (shared/quota _user_limits) resolved plan → expiry fallback →
-- overrides from user_subscriptions+plans, then separately called the
-- get_user_usage_windows RPC for the rolling usage. The effective-limits logic
-- therefore lived in Python while the raw data lived in SQL — two definitions
-- to keep aligned, and no way to see limits+usage together from the DB.
--
-- Now: get_user_quota_state(user_id) is THE single source everything reads —
--   * the enforcement gate (shared/quota check)
--   * the حدود الاستخدام dialog (GET /api/v1/usage → current_usage_report)
--   * the operator glance (user_subscriptions_live, recreated below with
--     used-vs-limit columns)
-- One RPC call returns: plan identity, expiry state, EFFECTIVE limits (after
-- the expired→free fallback and per-user overrides), and live usage windows.
--
-- Usage stays DERIVED from the llm_calls ledger (via get_user_usage_windows,
-- reused internally) — deliberately NOT materialized into a table: rolling
-- windows decay continuously, so any stored counter is stale on write and
-- every spender becomes a drift risk (the pre-079 Redis-accumulator bug).
--
-- Effective-limit semantics (must match what shared/quota enforced before):
--   * no subscription row            → zero rows returned → caller treats as locked
--   * plan_id NULL / plan not in catalog → locked=true (limits forced to 0)
--   * expires_at passed              → limits come from the 'free' plan
--   * *_override non-NULL            → wins over the plan limit
--   * resulting limit NULL           → unlimited ; 0 → feature not included

-- ── 1. The single source ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.get_user_quota_state(p_user_id uuid)
RETURNS TABLE(
    locked            boolean,
    plan_id           text,
    plan_name_ar      text,
    expires_at        timestamptz,
    is_expired        boolean,
    effective_plan_id text,
    effective_name_ar text,
    points_session    integer,
    points_weekly     integer,
    points_monthly    integer,
    ocr_pages_monthly integer,
    web_calls_monthly integer,
    session_cost      double precision,
    weekly_cost       double precision,
    ocr_pages         bigint,
    session_oldest    timestamptz,
    weekly_oldest     timestamptz,
    ocr_oldest        timestamptz
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
        w.ocr_oldest
    FROM public.user_subscriptions s
    LEFT JOIN public.plans p  ON p.plan_id = s.plan_id
    -- effective plan: expired time-boxed subscription falls back to 'free'
    LEFT JOIN public.plans ep ON ep.plan_id = CASE
            WHEN s.plan_id IS NULL                                   THEN NULL
            WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()  THEN 'free'
            ELSE s.plan_id END
    CROSS JOIN LATERAL public.get_user_usage_windows(p_user_id) w
    WHERE s.user_id = p_user_id;
$$;

COMMENT ON FUNCTION public.get_user_quota_state(uuid) IS
    'THE quota source of truth (093): plan identity + effective limits (after '
    'expired→free fallback + overrides) + live rolling usage from the llm_calls '
    'ledger, in one row. The gate, the usage dialog, and the operator view all '
    'read this. Zero rows = no subscription row = locked. Service-role only.';

-- ── 2. Operator view — now shows used-vs-limit alongside identity ────────────

DROP VIEW IF EXISTS public.user_subscriptions_live;
CREATE VIEW public.user_subscriptions_live
WITH (security_invoker = true) AS
SELECT
    s.user_id,
    u.email,
    q.plan_id,
    q.plan_name_ar,
    CASE WHEN q.locked     THEN 'locked'
         WHEN q.is_expired THEN 'expired'
         ELSE 'active' END                        AS status,
    q.is_expired,
    q.effective_plan_id,
    q.effective_name_ar,
    round((q.session_cost * 100)::numeric, 2)     AS points_session_used,
    q.points_session                              AS points_session_limit,
    round((q.weekly_cost  * 100)::numeric, 2)     AS points_weekly_used,
    q.points_weekly                               AS points_weekly_limit,
    q.ocr_pages                                   AS ocr_pages_used,
    q.ocr_pages_monthly                           AS ocr_pages_limit,
    s.source,
    s.started_at,
    s.expires_at,
    s.redeemed_code,
    s.points_session_override,
    s.points_weekly_override,
    s.points_monthly_override,
    s.ocr_pages_monthly_override,
    s.web_calls_monthly_override,
    s.created_at,
    s.updated_at
FROM public.user_subscriptions s
JOIN public.users u ON u.user_id = s.user_id
CROSS JOIN LATERAL public.get_user_quota_state(s.user_id) q;

COMMENT ON VIEW public.user_subscriptions_live IS
    'Operator glance: subscription identity + derived status + used-vs-limit '
    '(points; 1$=100) — all computed at read time via get_user_quota_state, '
    'the same source the gate and the usage dialog consume (091/093). '
    'NULL limit = unlimited; 0 = feature not in plan.';

-- ── 3. Permission hygiene — quota RPCs are backend-only ─────────────────────
-- get_user_usage_windows was EXECUTE-able by any authenticated user with any
-- user_id (pre-existing IDOR: cross-user usage/cost read via PostgREST).
-- The backend talks to PostgREST as service_role (shared/db/client.py), which
-- keeps working via the explicit grants.

REVOKE EXECUTE ON FUNCTION public.get_user_quota_state(uuid)   FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_quota_state(uuid)   TO service_role;
REVOKE EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) TO service_role;

-- 092 revoked grant_plan from PUBLIC — restore the service-role path the
-- future payment webhook will use (postgres/SQL operators unaffected).
GRANT  EXECUTE ON FUNCTION public.grant_plan(uuid, text, text, uuid) TO service_role;

-- The view is operator-facing only (end users get their numbers via
-- GET /api/v1/usage); without EXECUTE on the RPC it would error for them anyway.
REVOKE SELECT ON public.user_subscriptions_live FROM anon, authenticated;
