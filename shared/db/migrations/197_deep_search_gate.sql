-- ============================================================================
-- Migration 197 — deep-search plan gate: free = 1 per rolling 30 days + a
--                 2-point floor, basic = a 1-point floor (≤ 1 left = blocked).
--
-- Source: .claude/plans/deep_search_plan_gate.md (owner decisions locked
--         2026-10-30). Live facts below read from prod dwgghvxogtwyaxmbgjod on
--         2026-10-30, SELECT-only, immediately before this file was written.
--
-- WHY ────────────────────────────────────────────────────────────────────────
-- The points gate (get_user_quota_state, shared/quota.check) only refuses a send
-- whose window is already SPENT. A deep search is the heaviest thing a turn does
-- (cheapest real run 2.68 pts, median 5.14, p90 9.16), so a free user with 5
-- points could start one, two or three of them: 16 free users ran 2–3 each in
-- the last 30 days (avg 6.47 pts). Owner rules:
--     free  — 1 deep search per rolling 30 days, AND none with < 2 points left
--             (the floor is not redundant: it stops a user who spent > 3 of
--             their 5 points on chat before their first search);
--     basic — no deep search with ≤ 1 point left;
--     pro / max / dev / marketing_lawyer* — unchanged (NULL = no gate).
-- Neither threshold prevents an overrun; the gate only stops a NEW search.
--
-- WHAT THIS FILE DOES ────────────────────────────────────────────────────────
--   §1 plans.deep_search_min_points numeric NULL   — block when remaining < it
--      plans.deep_search_per_window integer NULL   — deep searches / 30 days
--      (+ CHECKs and comments). NULL = no gate on that rule.
--   §2 seeds: free = (2, 1), basic = (1.01, NULL). Every other plan stays NULL.
--      1.01 ENCODES "≤ 1 blocked" under the strict `remaining < min` rule:
--      1.00 left → blocked, 1.01 left → allowed.
--   §3 count_user_deep_searches(p_user_id, p_since) — SECURITY DEFINER,
--      service_role EXECUTE only.
--   §4 product_docs `usage_limits` + `pricing` (the router reads both): only the
--      sentences this gate makes false, each replaced iff still present verbatim.
--   §5 self-tests (RAISE EXCEPTION rolls the whole file back).
--
-- WHAT IT DELIBERATELY DOES NOT DO ───────────────────────────────────────────
--   * get_user_quota_state is NOT widened. Changing its RETURNS TABLE forces a
--     DROP + CREATE of user_subscriptions_live, which loses security_invoker
--     (129 / 129a). The backend reads the two new columns through the plans
--     catalog it already caches for max_parallel_runs (shared/quota, ~60 s TTL),
--     so a later UPDATE of either column is live within a minute, no deploy.
--   * No gate on the blog editorial path (headless, no plan) — backend-side.
--
-- THE COUNT — verified live 2026-10-30 (last 30 days, agent_family 'deep_search'):
--   * a run "reached retrieval" ⇔ it has a `deep_search.sector_picker` row or a
--     `deep_search.search_fee.*` row (reg_compliance / case). 277 such runs; 0
--     had one without the other; 0 rows with a NULL run_id. A run that crashed
--     before retrieval leaves only `deep_search.planner` rows (1 such run) and
--     is NOT counted (owner default, plan §7). A re-send of the same question is
--     a new run and IS counted.
--   * pause/resume keeps the run_id (15 retrieval runs span two messages) and
--     the simple_search → deep_search fallback rides simple_search's run_id —
--     count(DISTINCT run_id) counts each once.
--   * window floor = GREATEST(p_since, user_subscriptions.usage_reset_at),
--     resolved HERE exactly as get_user_usage_windows resolves the 30-day points
--     window; the backend passes p_since = now() − 30 days. Uses the existing
--     idx_llm_calls_user_created (user_id, created_at DESC).
--   * returns (runs, run_starts): run_starts = each counted run's first
--     retrieval row, oldest first — so the backend can say WHEN the count drops
--     back under the cap (the (runs − cap + 1)-th oldest + 30 days), which a
--     bare integer cannot.
--
-- RELEASE ORDER: apply 197 → deploy backend (the gate is live on deploy: this
-- file seeds the values) → web → mobile OTA. A backend deployed BEFORE 197 reads
-- the catalog with `select *`, finds no gate columns and runs ungated; nothing
-- 500s. Old clients drop the unknown `deep_search_gated` event and still show
-- the saved Arabic reply.
--
-- IDEMPOTENT: ADD COLUMN IF NOT EXISTS · DROP CONSTRAINT IF EXISTS + ADD ·
-- IS DISTINCT FROM-guarded seeds · CREATE OR REPLACE · REVOKE/GRANT ·
-- sentence-presence-guarded doc replaces. Safe to run twice.
--
-- POST-APPLY VERIFICATION
--   SELECT plan_id, deep_search_min_points, deep_search_per_window
--     FROM public.plans ORDER BY plan_id;
--   -- expect basic 1.01/NULL · free 2/1 · dev, marketing_lawyer,
--   --        marketing_lawyer_14, max, pro NULL/NULL
--   SELECT p.prosecdef, p.proconfig,
--          has_function_privilege('service_role',  p.oid, 'EXECUTE') AS svc,
--          has_function_privilege('anon',          p.oid, 'EXECUTE') AS anon,
--          has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authd
--     FROM pg_proc p WHERE p.oid =
--          'public.count_user_deep_searches(uuid, timestamptz)'::regprocedure;
--   -- expect t · {search_path=public} · t · f · f
--   SELECT * FROM public.count_user_deep_searches(
--            '00000000-0000-0000-0000-000000000000', now() - interval '30 days');
--   -- expect 0 · {}
--   SELECT doc_key,
--          content_md LIKE '%بحث معمّق واحد تقريباً%'                AS stale_usage,
--          content_md LIKE '%في الجلسة ومثله في الأسبوع%'            AS stale_pricing,
--          content_md LIKE '%بحث معمّق واحد كل 30 يوماً%'             AS new_free_line
--     FROM public.product_docs WHERE doc_key IN ('usage_limits', 'pricing');
--   -- expect f · f · t on both rows (router cache TTL 600 s)
--
-- ROLLBACK (if ever needed — the backend then runs ungated, nothing breaks):
--   UPDATE public.plans SET deep_search_min_points = NULL,
--                           deep_search_per_window = NULL;
--   -- the doc sentences are not restored automatically; the old text is quoted
--   -- verbatim in §4 below.
-- ============================================================================

BEGIN;

-- ── §1 plans columns ─────────────────────────────────────────────────────────
ALTER TABLE public.plans ADD COLUMN IF NOT EXISTS deep_search_min_points numeric NULL;
ALTER TABLE public.plans ADD COLUMN IF NOT EXISTS deep_search_per_window integer NULL;

ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_deep_search_min_points_check;
ALTER TABLE public.plans ADD CONSTRAINT plans_deep_search_min_points_check
    CHECK (deep_search_min_points IS NULL OR deep_search_min_points >= 0);

-- >= 1, not >= 0: "not included" is not a state this gate expresses (the copy
-- says "you used it"); a plan without deep search would need its own rule.
ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_deep_search_per_window_check;
ALTER TABLE public.plans ADD CONSTRAINT plans_deep_search_per_window_check
    CHECK (deep_search_per_window IS NULL OR deep_search_per_window >= 1);

COMMENT ON COLUMN public.plans.deep_search_min_points IS
  'Deep-search plan gate (197): a NEW deep search is refused when the points left '
  'in the tightest limited window are < this value. NULL = no floor. basic = 1.01 '
  'encodes "<= 1 left is blocked" under the strict < rule. Read by shared/quota '
  'via the cached plans catalog (~60 s), never by get_user_quota_state.';
COMMENT ON COLUMN public.plans.deep_search_per_window IS
  'Deep-search plan gate (197): deep searches allowed per rolling 30 days, counted '
  'by count_user_deep_searches (runs that reached retrieval). NULL = no cap.';

-- ── §2 seeds ────────────────────────────────────────────────────────────────
-- Only the two gated plans. pro / max / dev / marketing_lawyer /
-- marketing_lawyer_14 keep NULL (owner default for the promo plans, plan §7).
UPDATE public.plans AS p SET
    deep_search_min_points = v.min_points,
    deep_search_per_window = v.per_window,
    updated_at             = now()
FROM (VALUES
    ('free',  2.00::numeric, 1),
    ('basic', 1.01::numeric, NULL::integer)
) AS v(plan_id, min_points, per_window)
WHERE p.plan_id = v.plan_id
  AND (p.deep_search_min_points IS DISTINCT FROM v.min_points
    OR p.deep_search_per_window IS DISTINCT FROM v.per_window);

-- ── §3 the count ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.count_user_deep_searches(
    p_user_id uuid,
    p_since   timestamptz
)
RETURNS TABLE (runs integer, run_starts timestamptz[])
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    WITH floor_at AS (
        -- The 30-day points window's own lower bound (get_user_usage_windows):
        -- a purchase's usage_reset_at buys the allowance back. GREATEST ignores
        -- NULLs, so "never reset" / "no subscription row" leaves p_since.
        SELECT GREATEST(
                   p_since,
                   (SELECT s.usage_reset_at
                      FROM public.user_subscriptions s
                     WHERE s.user_id = p_user_id)
               ) AS at
    ),
    counted AS (
        SELECT c.run_id, min(c.created_at) AS started_at
          FROM public.llm_calls c, floor_at f
         WHERE c.user_id = p_user_id
           AND c.created_at >= f.at
           AND c.agent_family = 'deep_search'
           AND c.run_id IS NOT NULL
           -- "reached retrieval": the sector picker or a per-search fee row.
           -- starts_with, not LIKE — `_` is a LIKE wildcard.
           AND (c.agent = 'deep_search.sector_picker'
                OR starts_with(c.agent, 'deep_search.search_fee.'))
         GROUP BY c.run_id
    )
    SELECT count(*)::integer,
           COALESCE(array_agg(started_at ORDER BY started_at), '{}'::timestamptz[])
      FROM counted;
$$;

COMMENT ON FUNCTION public.count_user_deep_searches(uuid, timestamptz) IS
  'Deep-search plan gate (197): the user''s deep-search runs that reached retrieval '
  '(a deep_search.sector_picker or deep_search.search_fee.* llm_calls row) at or '
  'after GREATEST(p_since, usage_reset_at), one per run_id, with each run''s first '
  'retrieval instant (oldest first). service_role only.';

REVOKE ALL ON FUNCTION public.count_user_deep_searches(uuid, timestamptz)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.count_user_deep_searches(uuid, timestamptz)
    TO service_role;

-- ── §4 product_docs — only the sentences this gate makes false ───────────────
-- The router reads these rows (agents/tool_repository/rayhan_docs.py, 600 s TTL).
-- Same precedent as 187: replace the exact sentence, only while it is still
-- there verbatim; anything else is left alone with a NOTICE. Old sentences
-- (copied from the live rows 2026-10-30, each present exactly once):
--   usage_limits: «الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً
--                 — بحث معمّق واحد تقريباً، لتجربة ريحان قبل الاشتراك.»
--   pricing:      «**المجانية** — للتجربة، بحدّ يكفي لبحث معمّق واحد في الجلسة
--                 ومثله في الأسبوع.» — ALREADY wrong before this gate: free has
--                 had neither a session nor a weekly window since 129.
-- ⚠ PROPOSED copy — not owner-approved yet. No riyal amount (126), Latin digits.
-- The usage_limits text is the same as frontend/components/learn/UsageLimitsView.
DO $$
DECLARE
    v_usage_old   text := 'الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً — بحث معمّق واحد تقريباً، لتجربة ريحان قبل الاشتراك.';
    v_usage_new   text := 'الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً، وفيها بحث معمّق واحد كل 30 يوماً يبدأ متى بقيت في رصيدك نقطتان على الأقل — لتجربة ريحان قبل الاشتراك.'
                         || E'\n\n'
                         || 'وفي الباقة الأساسية لا يبدأ بحث معمّق جديد إذا بقيت لك نقطة واحدة أو أقل في الجلسة أو في الأسبوع.';
    v_pricing_old text := '**المجانية** — للتجربة، بحدّ يكفي لبحث معمّق واحد في الجلسة ومثله في الأسبوع.';
    v_pricing_new text := '**المجانية** — للتجربة: بحث معمّق واحد كل 30 يوماً، ضمن رصيد نقاط محدود للفترة نفسها.';
    v_n           integer;
BEGIN
    IF v_usage_new ~ '[٠-٩۰-۹]' OR v_pricing_new ~ '[٠-٩۰-۹]' THEN
        RAISE EXCEPTION '197: new doc text carries Arabic-Indic digits (Latin digits only)';
    END IF;
    IF v_pricing_new ~ '(ريال|ر\.س|SAR)' THEN
        RAISE EXCEPTION '197: new pricing text names an amount (126 forbids prices in product_docs)';
    END IF;

    UPDATE public.product_docs
       SET content_md = replace(content_md, v_usage_old, v_usage_new),
           updated_at = now()
     WHERE doc_key = 'usage_limits'
       AND position(v_usage_old IN content_md) > 0;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 1 THEN
        RAISE NOTICE '197: product_docs.usage_limits — free line replaced, basic line added.';
    ELSIF EXISTS (SELECT 1 FROM public.product_docs
                   WHERE doc_key = 'usage_limits'
                     AND position(v_usage_new IN content_md) > 0) THEN
        RAISE NOTICE '197: product_docs.usage_limits already carries the new text (re-run).';
    ELSE
        RAISE NOTICE '197: product_docs.usage_limits changed since 2026-10-30 — free line NOT replaced; edit by hand.';
    END IF;

    UPDATE public.product_docs
       SET content_md = replace(content_md, v_pricing_old, v_pricing_new),
           updated_at = now()
     WHERE doc_key = 'pricing'
       AND position(v_pricing_old IN content_md) > 0;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 1 THEN
        RAISE NOTICE '197: product_docs.pricing — free line replaced.';
    ELSIF EXISTS (SELECT 1 FROM public.product_docs
                   WHERE doc_key = 'pricing'
                     AND position(v_pricing_new IN content_md) > 0) THEN
        RAISE NOTICE '197: product_docs.pricing already carries the new text (re-run).';
    ELSE
        RAISE NOTICE '197: product_docs.pricing changed since 2026-10-30 — free line NOT replaced; edit by hand.';
    END IF;
END $$;

-- ── §5 self-tests ────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_fn        oid := 'public.count_user_deep_searches(uuid, timestamptz)'::regprocedure;
    v_type      text;
    v_bad       text;
    v_runs      integer;
    v_starts    timestamptz[];
    v_user      record;
    v_expect    integer;
    v_checked   integer := 0;
    v_sp        bigint;
    v_fee       bigint;
BEGIN
    -- columns + types
    SELECT data_type INTO v_type FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'plans'
       AND column_name = 'deep_search_min_points';
    IF v_type IS DISTINCT FROM 'numeric' THEN
        RAISE EXCEPTION '197: plans.deep_search_min_points is %, expected numeric', v_type;
    END IF;
    SELECT data_type INTO v_type FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'plans'
       AND column_name = 'deep_search_per_window';
    IF v_type IS DISTINCT FROM 'integer' THEN
        RAISE EXCEPTION '197: plans.deep_search_per_window is %, expected integer', v_type;
    END IF;

    -- seeds
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE plan_id = 'free'
                     AND deep_search_min_points = 2 AND deep_search_per_window = 1) THEN
        RAISE EXCEPTION '197: free must be (min 2, per_window 1)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE plan_id = 'basic'
                     AND deep_search_min_points = 1.01 AND deep_search_per_window IS NULL) THEN
        RAISE EXCEPTION '197: basic must be (min 1.01, per_window NULL)';
    END IF;
    -- Owner: pro / max unchanged — a gate value there is a bug in this file.
    SELECT string_agg(plan_id, ', ') INTO v_bad FROM public.plans
     WHERE plan_id IN ('pro', 'max')
       AND (deep_search_min_points IS NOT NULL OR deep_search_per_window IS NOT NULL);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '197: pro / max must stay ungated, found a value on: %', v_bad;
    END IF;
    SELECT string_agg(plan_id, ', ') INTO v_bad FROM public.plans
     WHERE plan_id NOT IN ('free', 'basic', 'pro', 'max')
       AND (deep_search_min_points IS NOT NULL OR deep_search_per_window IS NOT NULL);
    IF v_bad IS NOT NULL THEN
        RAISE NOTICE '197: other plans carrying a deep-search gate value (set by hand?): %', v_bad;
    END IF;

    -- The basic boundary under the strict rule `remaining < min`: 1.00 left is
    -- blocked, 1.01 left is allowed (plan §6).
    IF NOT ((SELECT 1.00::numeric < deep_search_min_points FROM public.plans WHERE plan_id = 'basic')
        AND NOT (SELECT 1.01::numeric < deep_search_min_points FROM public.plans WHERE plan_id = 'basic')) THEN
        RAISE EXCEPTION '197: basic boundary broken — 1.00 must block and 1.01 must pass';
    END IF;

    -- the function: definer, pinned search_path, shape, service_role only
    IF NOT (SELECT prosecdef FROM pg_proc WHERE oid = v_fn) THEN
        RAISE EXCEPTION '197: count_user_deep_searches must be SECURITY DEFINER';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE oid = v_fn
                     AND 'search_path=public' = ANY (proconfig)) THEN
        RAISE EXCEPTION '197: count_user_deep_searches must pin search_path=public';
    END IF;
    IF pg_get_function_result(v_fn) <> 'TABLE(runs integer, run_starts timestamp with time zone[])' THEN
        RAISE EXCEPTION '197: count_user_deep_searches returns %, expected TABLE(runs integer, run_starts timestamptz[])',
            pg_get_function_result(v_fn);
    END IF;
    IF NOT has_function_privilege('service_role', v_fn, 'EXECUTE') THEN
        RAISE EXCEPTION '197: service_role lost EXECUTE on count_user_deep_searches';
    END IF;
    IF has_function_privilege('anon', v_fn, 'EXECUTE')
       OR has_function_privilege('authenticated', v_fn, 'EXECUTE') THEN
        RAISE EXCEPTION '197: a client role can EXECUTE count_user_deep_searches';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a
                WHERE p.oid = v_fn AND a.grantee = 0 AND a.privilege_type = 'EXECUTE') THEN
        RAISE EXCEPTION '197: PUBLIC can EXECUTE count_user_deep_searches';
    END IF;

    -- functional: a user with no ledger rows counts zero
    SELECT runs, run_starts INTO v_runs, v_starts
      FROM public.count_user_deep_searches('00000000-0000-0000-0000-000000000000'::uuid,
                                           now() - interval '30 days');
    IF v_runs <> 0 OR cardinality(v_starts) <> 0 THEN
        RAISE EXCEPTION '197: an unknown user counts % run(s), expected 0', v_runs;
    END IF;

    -- functional: agrees with an independent count for real users (up to 20),
    -- and run_starts carries one instant per counted run
    FOR v_user IN
        SELECT DISTINCT c.user_id
          FROM public.llm_calls c
         WHERE c.created_at >= now() - interval '30 days'
           AND c.agent = 'deep_search.sector_picker'
         LIMIT 20
    LOOP
        SELECT count(DISTINCT c.run_id) INTO v_expect
          FROM public.llm_calls c
          LEFT JOIN public.user_subscriptions s ON s.user_id = c.user_id
         WHERE c.user_id = v_user.user_id
           AND c.created_at >= now() - interval '30 days'
           AND (s.usage_reset_at IS NULL OR c.created_at >= s.usage_reset_at)
           AND c.agent_family = 'deep_search'
           AND (c.agent = 'deep_search.sector_picker'
                OR c.agent LIKE 'deep\_search.search\_fee.%');
        SELECT runs, run_starts INTO v_runs, v_starts
          FROM public.count_user_deep_searches(v_user.user_id, now() - interval '30 days');
        IF v_runs <> v_expect OR cardinality(v_starts) <> v_runs THEN
            RAISE EXCEPTION '197: user % counts % run(s) / % start(s), independent count %',
                v_user.user_id, v_runs, cardinality(v_starts), v_expect;
        END IF;
        v_checked := v_checked + 1;
    END LOOP;

    -- The count depends on two ledger agent names (pinned in
    -- backend/tests/test_deep_search_gate.py). Silence here means a rename.
    SELECT count(*) FILTER (WHERE agent = 'deep_search.sector_picker'),
           count(*) FILTER (WHERE starts_with(agent, 'deep_search.search_fee.'))
      INTO v_sp, v_fee
      FROM public.llm_calls
     WHERE created_at >= now() - interval '30 days'
       AND agent_family = 'deep_search';
    IF v_sp = 0 OR v_fee = 0 THEN
        RAISE WARNING '197: no recent % rows — has a ledger agent been renamed? The free count would read 0.',
            CASE WHEN v_sp = 0 THEN 'deep_search.sector_picker' ELSE 'deep_search.search_fee.*' END;
    END IF;

    -- The view this file must NOT have touched (129/129a trap).
    IF NOT EXISTS (SELECT 1 FROM pg_class
                    WHERE relname = 'user_subscriptions_live'
                      AND 'security_invoker=true' = ANY (reloptions)) THEN
        RAISE EXCEPTION '197: user_subscriptions_live lost security_invoker';
    END IF;

    RAISE NOTICE '197 OK: plans gate columns seeded, count RPC service_role-only, % real user(s) cross-checked',
        v_checked;
END $$;

COMMIT;
