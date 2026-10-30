-- ════════════════════════════════════════════════════════════════════════════
-- 196 — renewal grace: a declined renewal keeps Pro / Max for 24h while retried
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/renewal_retry_grace.md §3 (this file), §0 (owner
--       decisions 2026-10-30), §4 (the backend that writes the column).
-- Depends on: 092 (grant_plan), 079/091 (user_subscriptions +
--             trg_user_subscriptions_assignment), 120 (renewal_cancelled_at +
--             user_subscriptions_live — a security_invoker view, NEVER rebuilt
--             here: 129/129a), 132 (renewal_failed_count / renewal_attempt_at),
--             137 (usage_reset_at + the library period anchor). ⚠ §0 refuses
--             to run if get_user_quota_state is not the body this file was
--             written against, or if the trigger facts B2 relies on changed.
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE on the unchanged
--             signature + RETURNS, re-asserted grants. Re-runnable (§0 accepts
--             this file's own body).
-- Live state: read on prod dwgghvxogtwyaxmbgjod 2026-10-30 immediately before
--             writing — pg_get_functiondef of get_user_quota_state, grant_plan
--             and handle_subscription_assignment, pg_get_viewdef +
--             reloptions of user_subscriptions_live (33 columns,
--             {security_invoker=true}). Latest applied: 195. The live quota
--             body's comments already differ from 137's file, so §2 is the LIVE
--             body plus the changes marked «196:», not a copy of 137.
--
-- ── WHY ─────────────────────────────────────────────────────────────────────
-- The renewal job now charges AT expiry (renewal_retry_grace.md §0.1). A soft
-- decline (51 insufficient funds, …) used to lapse the plan the moment the term
-- ran out, mid-ladder: abdallhalsrraj was declined at 03:30 and on the free plan
-- by 21:33 the same day. The owner's rule: keep Pro / Max for 24h while the card
-- is retried at +12h and +24h, then lapse.
--
-- expires_at cannot carry that (§6): it is the renewal period key
-- (payment_transactions.period_start), and moving it would let a retry miss
-- attempt 0's row and the per-period unique index stop preventing a second
-- charge. So access gets its own column, written by the renewal job alone:
--
--   user_subscriptions.renewal_grace_until — "access continues until here while
--   a renewal is being retried". NULL = no grace.
--
-- and get_user_quota_state — THE quota source (gate, usage dialog, the view) —
-- reads the term as live until the later of the two.
--
-- ── WHAT CHANGES IN get_user_quota_state (each marked «196:» in §2) ─────────
-- (1) ACCESS END = renewal_grace_until when the row is source = 'payment' AND
--     the grace ends LATER than expires_at; otherwise expires_at. is_expired,
--     the expired→free fallback and the `expires_at` OUTPUT column all derive
--     from it — the output now means "when this plan stops", so the usage
--     dialog counts down to the grace's end instead of reading «خلال لحظات»
--     for a day. user_subscriptions_live keeps showing the raw column
--     (s.expires_at) and derives status from is_expired: 'active' in grace.
--     Equivalent to the plan's "expires_at > now() OR renewal_grace_until >
--     now()", plus two walls:
--       * source = 'payment' — the only rows the renewal job serves. A grace
--         left on a row that a code / store grant re-sourced mid-ladder is
--         inert (see 194 below).
--       * LATER than expires_at — the job ends a ladder with grace = now()
--         ("lapse now"). If that happens on the last tick BEFORE expiry, the
--         paid term still runs to its own end: a lapse never cuts paid time.
-- (2) THE LIBRARY PERIOD IS PINNED IN THE GRACE. The key is anchored at
--     GREATEST(started_at, usage_reset_at) and rolls every duration_days, which
--     for a term bought at its anchor is EXACTLY expires_at — verified on the
--     live body. Without a pin the grace would open a brand-new library
--     allowance (key …:1) a day before anyone paid for it. In the grace the
--     period clock stops one second before expires_at (the term's last
--     period); a successful renewal stamps usage_reset_at and mints the new key
--     as before, and an ended grace falls to free:YYYYMM. resets_at reads the
--     grace's end there — the latest this allowance can last.
-- RETURNS TABLE is byte-identical (§0 and §4 assert it), so the view keeps its
-- OID dependency, its 33 columns and security_invoker. Nothing is rebuilt.
--
-- ── B1 vs B2 (§3): grant_plan is NOT edited — B2 ─────────────────────────────
-- grant_plan stacks a same-plan purchase onto the old expiry only while
-- expires_at > now(); a renewal that succeeds INSIDE the grace would open a
-- fresh window from now() and slip the billing date. B1 (widen grant_plan's
-- predicate) would edit the live money path for a side concern — 113's rule —
-- and 194 leans on grant_plan staying put. B2 instead: the backend
-- (renewal_service._keep_the_billing_date) writes expires_at = period_start +
-- duration_days (and the continuous started_at) back in a SEPARATE statement
-- that never names plan_id. Safe because of two facts, read live and asserted
-- in §0 at apply time:
--   * trg_user_subscriptions_assignment is BEFORE UPDATE OF plan_id — a
--     statement without plan_id does not fire it;
--   * handle_subscription_assignment re-derives expires_at only when
--     NEW.plan_id IS DISTINCT FROM OLD.plan_id — grant_plan's same-plan upsert
--     (which DOES name plan_id) is never re-stamped.
-- §5 [G] proves both behaviourally on a fake account.
--
-- ── 194 (store purchases — written, NOT applied) — CORRECT IN EITHER ORDER ──
-- 194 and 196 touch disjoint objects; neither checks anything the other adds.
--   * 196 replaces get_user_quota_state; 194 never names it. 196 §0's body
--     fingerprint is the same before and after 194.
--   * 194 asserts grant_plan has ONE overload and that grant_store_plan's
--     RETURNS equals grant_plan's. 196 never touches grant_plan (B2): both
--     hold whichever runs first.
--   * Both ADD COLUMN on user_subscriptions (different names, IF NOT EXISTS);
--     neither rebuilds user_subscriptions_live.
--   * 194's grant_store_plan clears renewal_cancelled_at / renewal_failed_count
--     but cannot know renewal_grace_until. It need not: it sets source to the
--     store, and (1)'s source = 'payment' wall makes a grace left behind inert.
--   * Both self-tests build a fake auth.users account and roll it back.
--
-- ── SHIPS INERT ─────────────────────────────────────────────────────────────
-- The column is NULL on every row and only the new backend writes it. With
-- renewal_grace_until NULL, access end = expires_at and the period clock =
-- now(): get_user_quota_state answers every row exactly as before. The OLD
-- backend runs unchanged against this file (it never names the column).
-- Applying it therefore changes behaviour for ZERO users.
--
-- ── DEPLOY ORDER ────────────────────────────────────────────────────────────
-- 196 → backend (renewal_service selects / writes renewal_grace_until and
-- GET /payments/subscription selects it: ahead of 196 the sweep logs a 42703
-- every tick and charges nobody, and the settings dialog 500s) → web.
-- (renewal_retry_grace.md §8.)
--
-- ── LOCKS ───────────────────────────────────────────────────────────────────
-- ADD COLUMN (NULL, no default) is metadata-only but takes ACCESS EXCLUSIVE on
-- user_subscriptions until COMMIT — every quota read waits, self-tests
-- included, well under a second. lock_timeout fails fast instead of queueing
-- every message send behind this transaction. The behavioural self-test inserts
-- a fake auth.users row: run as postgres (SQL editor / the pooler URL), like 192.
--
-- ── CLIENT WRITES ───────────────────────────────────────────────────────────
-- The column GRANTS ACCESS, so a client must never write it. anon /
-- authenticated hold table-level privileges on user_subscriptions (the
-- inherited Supabase default), but RLS is on and the only policy is
-- user_subscriptions_select_self (SELECT, authenticated): no INSERT / UPDATE /
-- DELETE reaches the table through PostgREST. §4 asserts exactly that. Reading
-- one's own grace is harmless.
--
-- ── VERIFY (after apply) ────────────────────────────────────────────────────
-- -- 1. the view is intact (EXPECT {security_invoker=true} | 33)
-- SELECT c.reloptions,
--        (SELECT count(*) FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'user_subscriptions_live')
--   FROM pg_class c WHERE c.oid = 'public.user_subscriptions_live'::regclass;
-- -- 2. the column (EXPECT timestamp with time zone | YES)
-- SELECT data_type, is_nullable FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'user_subscriptions'
--    AND column_name = 'renewal_grace_until';
-- -- 3. the RPC is 196's, same RETURNS, still service_role only
-- --    (EXPECT true | 1 | false | false | true)
-- SELECT position('196:' in p.prosrc) > 0,
--        (SELECT count(*) FROM pg_proc q WHERE q.proname = 'get_user_quota_state'
--           AND q.pronamespace = 'public'::regnamespace),
--        has_function_privilege('anon', p.oid, 'EXECUTE'),
--        has_function_privilege('authenticated', p.oid, 'EXECUTE'),
--        has_function_privilege('service_role', p.oid, 'EXECUTE')
--   FROM pg_proc p WHERE p.oid = 'public.get_user_quota_state(uuid)'::regprocedure;
-- -- 4. inert (EXPECT 0 until the new backend declines a card)
-- SELECT count(*) FROM public.user_subscriptions WHERE renewal_grace_until IS NOT NULL;
-- -- 5. later: who is in a grace right now, and until when
-- SELECT user_id, plan_id, expires_at, renewal_grace_until, renewal_failed_count,
--        renewal_attempt_at
--   FROM public.user_subscriptions
--  WHERE source = 'payment' AND renewal_grace_until > now()
--  ORDER BY renewal_grace_until;
--
-- ── ROLLBACK ────────────────────────────────────────────────────────────────
-- Backend first (it names the column). Then CREATE OR REPLACE
-- get_user_quota_state with the pre-196 body — §2 with the «196:» changes
-- reverted (access end → s.expires_at, period clock → now(), resets_at's grace
-- branch removed) — and only then
--   ALTER TABLE public.user_subscriptions DROP COLUMN renewal_grace_until;
-- (a grace in flight is lost: those users drop to free at once).

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 0. the facts this file leans on — refuse on drift, change nothing ───────
DO $$
DECLARE
    v_col  TEXT;
    v_n    INTEGER;
    v_src  TEXT;
    v_md5  TEXT;
BEGIN
    FOREACH v_col IN ARRAY ARRAY['plan_id', 'source', 'started_at', 'expires_at',
                                 'usage_reset_at', 'renewal_cancelled_at',
                                 'renewal_attempt_at', 'renewal_failed_count'] LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns c
                        WHERE c.table_schema = 'public'
                          AND c.table_name = 'user_subscriptions'
                          AND c.column_name = v_col) THEN
            RAISE EXCEPTION '196: user_subscriptions.% is missing (120 / 132 / 137 not applied?) — nothing changed', v_col;
        END IF;
    END LOOP;

    -- CREATE OR REPLACE on a signature that does not exist CREATES a second
    -- overload; a different RETURNS would refuse — or, "fixed" with a DROP,
    -- take user_subscriptions_live with it (129/129a).
    SELECT count(*) INTO v_n
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'get_user_quota_state';
    IF v_n <> 1 OR to_regprocedure('public.get_user_quota_state(uuid)') IS NULL THEN
        RAISE EXCEPTION '196: expected exactly one public.get_user_quota_state(uuid), found % overload(s) — nothing changed', v_n;
    END IF;
    IF pg_get_function_result('public.get_user_quota_state(uuid)'::regprocedure)
       IS DISTINCT FROM 'TABLE(locked boolean, plan_id text, plan_name_ar text, expires_at timestamp with time zone, is_expired boolean, effective_plan_id text, effective_name_ar text, points_session integer, points_weekly integer, points_monthly integer, ocr_pages_monthly integer, web_calls_monthly integer, session_cost double precision, weekly_cost double precision, ocr_pages bigint, session_oldest timestamp with time zone, weekly_oldest timestamp with time zone, ocr_oldest timestamp with time zone, library_unlocks_limit integer, library_unlocks_used integer, library_period_key text, library_period_resets_at timestamp with time zone, monthly_cost double precision, monthly_oldest timestamp with time zone)' THEN
        RAISE EXCEPTION '196: get_user_quota_state RETURNS changed since 2026-10-30 (%) — read it live and reconcile. Nothing changed.',
            pg_get_function_result('public.get_user_quota_state(uuid)'::regprocedure);
    END IF;

    -- The live body is the one §2 was built from (whitespace-normalised md5,
    -- read 2026-10-30), or this file's own on a re-run.
    SELECT p.prosrc INTO v_src
      FROM pg_proc p WHERE p.oid = 'public.get_user_quota_state(uuid)'::regprocedure;
    v_md5 := md5(btrim(regexp_replace(v_src, '\s+', ' ', 'g')));
    IF v_md5 <> 'ff22c2f2df084c19936f173f4bfaf358' AND position('196:' in v_src) = 0 THEN
        RAISE EXCEPTION '196: get_user_quota_state is not the body this file was written against (md5 %) — '
                        'read it live (pg_get_functiondef) and fold the 196 changes into it. Nothing changed.', v_md5;
    END IF;

    -- B2's two facts (header). If either changed, the backend's separate
    -- expires_at write is no longer known to be safe.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
                    WHERE t.tgrelid = 'public.user_subscriptions'::regclass
                      AND t.tgname = 'trg_user_subscriptions_assignment'
                      AND NOT t.tgisinternal
                      AND t.tgenabled <> 'D'
                      AND pg_get_triggerdef(t.oid) LIKE '%BEFORE UPDATE OF plan_id ON public.user_subscriptions FOR EACH ROW%') THEN
        RAISE EXCEPTION '196: trg_user_subscriptions_assignment is not BEFORE UPDATE OF plan_id — B2 (renewal_service._keep_the_billing_date) relies on it. Nothing changed.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid = to_regprocedure('public.handle_subscription_assignment()')
                      AND p.prosrc LIKE '%NEW.plan_id IS DISTINCT FROM OLD.plan_id%') THEN
        RAISE EXCEPTION '196: handle_subscription_assignment no longer re-stamps ONLY on a plan change — B2 relies on it. Nothing changed.';
    END IF;
    -- …and nothing else on the table rewrites the term behind it.
    SELECT count(*) INTO v_n
      FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
     WHERE t.tgrelid = 'public.user_subscriptions'::regclass
       AND NOT t.tgisinternal
       AND t.tgname <> 'trg_user_subscriptions_assignment'
       AND p.prosrc ILIKE '%expires_at%';
    IF v_n <> 0 THEN
        RAISE EXCEPTION '196: % other trigger(s) on user_subscriptions touch expires_at — check them against B2 first. Nothing changed.', v_n;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_class c
                    WHERE c.oid = to_regclass('public.user_subscriptions_live')
                      AND c.reloptions @> ARRAY['security_invoker=true']) THEN
        RAISE EXCEPTION '196: user_subscriptions_live is missing or lost security_invoker (129/129a) — fix that on its own first. Nothing changed.';
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. user_subscriptions.renewal_grace_until
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.user_subscriptions
    ADD COLUMN IF NOT EXISTS renewal_grace_until timestamptz NULL;

COMMENT ON COLUMN public.user_subscriptions.renewal_grace_until IS
    'Renewal grace (196, renewal_retry_grace.md): access continues until here while '
    'a declined renewal is retried. NULL = no grace. Written ONLY by the renewal job '
    '(first soft decline: GREATEST(expires_at, now) + 24h30m; ladder over: now()), '
    'and cleared by any paid grant (subscription_service.reset_renewal_ladder) or a '
    'cancel during the grace (lapse now). get_user_quota_state reads the plan as live '
    'until this when source = ''payment'' and it is LATER than expires_at. Never moves '
    'expires_at, which stays the renewal period key.';


-- ════════════════════════════════════════════════════════════════════════════
-- 2. get_user_quota_state — the LIVE body (2026-10-30) + the «196:» changes
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.get_user_quota_state(p_user_id uuid)
 RETURNS TABLE(locked boolean, plan_id text, plan_name_ar text, expires_at timestamp with time zone, is_expired boolean, effective_plan_id text, effective_name_ar text, points_session integer, points_weekly integer, points_monthly integer, ocr_pages_monthly integer, web_calls_monthly integer, session_cost double precision, weekly_cost double precision, ocr_pages bigint, session_oldest timestamp with time zone, weekly_oldest timestamp with time zone, ocr_oldest timestamp with time zone, library_unlocks_limit integer, library_unlocks_used integer, library_period_key text, library_period_resets_at timestamp with time zone, monthly_cost double precision, monthly_oldest timestamp with time zone)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
    SELECT
        (ep.plan_id IS NULL)                                          AS locked,
        s.plan_id,
        p.name_ar                                                     AS plan_name_ar,
        ae.at                                                         AS expires_at,
        (ae.at IS NOT NULL AND ae.at <= now())                        AS is_expired,
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
    -- 196: when ACCESS ends. The renewal grace (renewal_grace_until) extends a
    -- payment-sourced term while a declined renewal is retried — only when it
    -- ends LATER than the term, so a ladder ended with grace = now() before
    -- expiry never cuts paid time, and only for source = 'payment', so a grace
    -- left on a row a code / store grant took over is inert. Everything that
    -- says "expired" reads this, never s.expires_at, which stays the renewal
    -- period key.
    CROSS JOIN LATERAL (
        SELECT CASE
            WHEN s.source = 'payment' AND s.renewal_grace_until > s.expires_at
                THEN s.renewal_grace_until
            ELSE s.expires_at
        END AS at
    ) ae
    LEFT JOIN public.plans p  ON p.plan_id = s.plan_id
    LEFT JOIN public.plans ep ON ep.plan_id = CASE
            WHEN s.plan_id IS NULL                   THEN NULL
            WHEN ae.at IS NOT NULL AND ae.at <= now() THEN 'free'
            ELSE s.plan_id END
    CROSS JOIN LATERAL public.get_user_usage_windows(p_user_id) w
    -- The period anchor (137). ONE expression, consumed by all three outputs
    -- below so they cannot drift apart. The library meter is a per-period
    -- allowance, not a rolling window, so the only way to zero it is to mint a
    -- DIFFERENT period_key — old library_unlocks rows keep theirs and stop
    -- matching. grant_plan deliberately PRESERVES started_at on a same-plan
    -- renewal (that is what stacks the remaining days), so started_at alone
    -- never moved on the most common paid event. GREATEST ignores NULLs, so a
    -- row with no usage_reset_at yields plain started_at = pre-137 behaviour.
    CROSS JOIN LATERAL (
        SELECT GREATEST(s.started_at, s.usage_reset_at) AS at
    ) pa
    -- 196: the period CLOCK. now(), except inside the renewal grace: the term
    -- has ended but access has not, and for a term bought at its anchor the key
    -- rolls at exactly expires_at — the grace would open a fresh allowance
    -- nobody has paid for. There the clock stops one second before the term
    -- ended (its LAST period). A successful renewal stamps usage_reset_at and
    -- mints the next key as before; an ended grace falls to free:YYYYMM.
    CROSS JOIN LATERAL (
        SELECT
            (s.expires_at <= now() AND ae.at > now())                AS in_grace,
            CASE WHEN s.expires_at <= now() AND ae.at > now()
                 THEN GREATEST(pa.at, s.expires_at - interval '1 second')
                 ELSE now()
            END                                                      AS at
    ) pc
    CROSS JOIN LATERAL (
        SELECT CASE
            WHEN ep.duration_days IS NULL OR pa.at IS NULL THEN NULL
            ELSE floor(
                     extract(epoch FROM (pc.at - pa.at))
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
                     || to_char(pa.at AT TIME ZONE 'UTC', 'YYYYMMDD') || ':'
                     || pi.idx::text
                ELSE 'free:' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMM')
            END AS period_key,
            CASE
                WHEN ep.plan_id IS NULL THEN NULL
                -- 196: a pinned period lasts, at the latest, until the grace
                -- ends (renewal → new key sooner; lapse → free:YYYYMM then).
                WHEN pi.idx IS NOT NULL AND pc.in_grace THEN ae.at
                WHEN pi.idx IS NOT NULL THEN
                     pa.at
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
    'THE quota source of truth (093/105): plan identity, EFFECTIVE limits '
    '(expired→free fallback + per-user overrides already applied) and every '
    'usage window, in one row — shared by the gate, the usage dialog and '
    'user_subscriptions_live so what is shown is exactly what is enforced. The '
    'library period is anchored at GREATEST(started_at, usage_reset_at) (137), '
    'so a paid purchase mints a new period_key and the spent unlocks stop '
    'counting without any row being deleted. 196: expiry is ACCESS end — '
    'renewal_grace_until when a payment-sourced row''s grace ends later than '
    'expires_at (a declined renewal being retried), else expires_at; the '
    'expires_at OUTPUT is that instant, and inside the grace the library period '
    'stays on the term''s last period.';

REVOKE EXECUTE ON FUNCTION public.get_user_quota_state(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_quota_state(uuid) TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- 3. (no view change) — user_subscriptions_live reads q.is_expired and
--    s.expires_at; CREATE OR REPLACE kept its dependency. §4 proves it.
-- ════════════════════════════════════════════════════════════════════════════


-- ── 4. structure ─────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_n    INTEGER;
    v_def  BOOLEAN;
    v_vol  "char";
    v_cfg  TEXT[];
    v_g    BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = 'public' AND c.table_name = 'user_subscriptions'
                      AND c.column_name = 'renewal_grace_until'
                      AND c.data_type = 'timestamp with time zone'
                      AND c.is_nullable = 'YES'
                      AND c.column_default IS NULL) THEN
        RAISE EXCEPTION '196: user_subscriptions.renewal_grace_until is missing or not a nullable timestamptz without default';
    END IF;

    SELECT count(*) INTO v_n
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'get_user_quota_state';
    IF v_n <> 1 THEN
        RAISE EXCEPTION '196: get_user_quota_state has % overload(s) after the replace, expected 1', v_n;
    END IF;
    IF pg_get_function_result('public.get_user_quota_state(uuid)'::regprocedure)
       IS DISTINCT FROM 'TABLE(locked boolean, plan_id text, plan_name_ar text, expires_at timestamp with time zone, is_expired boolean, effective_plan_id text, effective_name_ar text, points_session integer, points_weekly integer, points_monthly integer, ocr_pages_monthly integer, web_calls_monthly integer, session_cost double precision, weekly_cost double precision, ocr_pages bigint, session_oldest timestamp with time zone, weekly_oldest timestamp with time zone, ocr_oldest timestamp with time zone, library_unlocks_limit integer, library_unlocks_used integer, library_period_key text, library_period_resets_at timestamp with time zone, monthly_cost double precision, monthly_oldest timestamp with time zone)' THEN
        RAISE EXCEPTION '196: get_user_quota_state RETURNS changed by the replace';
    END IF;
    SELECT p.prosecdef, p.provolatile, p.proconfig INTO v_def, v_vol, v_cfg
      FROM pg_proc p WHERE p.oid = 'public.get_user_quota_state(uuid)'::regprocedure;
    IF NOT v_def OR v_vol <> 's'
       OR NOT EXISTS (SELECT 1 FROM unnest(COALESCE(v_cfg, ARRAY[]::text[])) c(setting)
                       WHERE c.setting = 'search_path=public') THEN
        RAISE EXCEPTION '196: get_user_quota_state must stay STABLE SECURITY DEFINER with search_path=public (definer %, volatility %, config %)',
            v_def, v_vol, v_cfg;
    END IF;
    IF has_function_privilege('anon', 'public.get_user_quota_state(uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.get_user_quota_state(uuid)', 'EXECUTE')
       OR NOT has_function_privilege('service_role', 'public.get_user_quota_state(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '196: get_user_quota_state EXECUTE must be service_role only';
    END IF;

    -- the view: never rebuilt, so it must look exactly as it did
    IF NOT EXISTS (SELECT 1 FROM pg_class c
                    WHERE c.oid = 'public.user_subscriptions_live'::regclass
                      AND c.reloptions @> ARRAY['security_invoker=true']) THEN
        RAISE EXCEPTION '196: user_subscriptions_live lost security_invoker (129/129a trap)';
    END IF;
    SELECT count(*) INTO v_n FROM information_schema.columns c
     WHERE c.table_schema = 'public' AND c.table_name = 'user_subscriptions_live';
    IF v_n <> 33 THEN
        RAISE EXCEPTION '196: user_subscriptions_live has % columns, expected 33', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid
                    WHERE d.classid = 'pg_rewrite'::regclass
                      AND r.ev_class = 'public.user_subscriptions_live'::regclass
                      AND d.refclassid = 'pg_proc'::regclass
                      AND d.refobjid = 'public.get_user_quota_state(uuid)'::regprocedure) THEN
        RAISE EXCEPTION '196: user_subscriptions_live no longer depends on get_user_quota_state';
    END IF;

    -- the column grants access: no client may write it (header, CLIENT WRITES)
    IF NOT (SELECT c.relrowsecurity FROM pg_class c
             WHERE c.oid = 'public.user_subscriptions'::regclass) THEN
        RAISE EXCEPTION '196: RLS is off on user_subscriptions — a client could write renewal_grace_until';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policy p
                WHERE p.polrelid = 'public.user_subscriptions'::regclass
                  AND p.polcmd <> 'r') THEN
        RAISE EXCEPTION '196: a non-SELECT policy exists on user_subscriptions — a client could grant itself a grace. Review it.';
    END IF;

    SELECT count(*) INTO v_g FROM public.user_subscriptions s WHERE s.renewal_grace_until IS NOT NULL;
    IF v_g = 0 THEN
        RAISE NOTICE '196: structure OK — no row has a grace, so every quota answer is unchanged';
    ELSE
        RAISE NOTICE '196: structure OK — % row(s) carry renewal_grace_until (a re-run after the backend shipped)', v_g;
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 5. Behavioural self-tests — one fake account, then ALL ROLLED BACK.
-- ════════════════════════════════════════════════════════════════════════════
-- 192's pattern: everything runs inside an inner BEGIN … EXCEPTION block that
-- ends by raising a private SQLSTATE (LU196) and catching it, so PostgreSQL
-- rolls the whole subtransaction back — the fake auth.users / users /
-- user_subscriptions rows and every write to them. A real assertion failure
-- raises P0001, is NOT caught, and aborts the migration. The fake account goes
-- through auth.users so on_auth_user_created builds its rows exactly as a
-- signup does; no real row is ever touched, even inside the rolled-back block.
--
--   [A] live term, no grace                → max, as before
--   [B] term ended, grace running          → max, not expired, view 'active',
--                                            expires_at = the grace's end,
--                                            library key PINNED (…:0, not …:1)
--   [C] grace ended                        → free
--   [D] term ended, no grace               → free (today's fallback)
--   [E] grace on a non-payment source      → free (the 194 wall)
--   [F] grace EARLIER than the term        → max to the term's own end
--   [G] B2: grant_plan same-plan on a live term stacks (the trigger does not
--       re-stamp); on a term in the grace it opens a fresh window, and a
--       separate expires_at write — no plan_id — sticks
DO $selftest$
DECLARE
    v_users0 BIGINT;
    v_run    TEXT := replace(gen_random_uuid()::text, '-', '');
    v_auth   UUID := gen_random_uuid();
    v_uid    UUID;
    v_dur    INTEGER;
    v_start  TIMESTAMPTZ;
    v_old    TIMESTAMPTZ;
    v_grace  TIMESTAMPTZ;
    v_q      RECORD;
    v_s      RECORD;
    v_status TEXT;
    v_key    TEXT;
BEGIN
    SELECT count(*) INTO v_users0 FROM public.users;

    IF NOT has_table_privilege('auth.users', 'INSERT') THEN
        RAISE EXCEPTION
            '196 self-test: this role cannot INSERT into auth.users (fake account '
            'for the behavioural tests). Run the migration as postgres (SQL editor).';
    END IF;

    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = 'max';
    IF v_dur IS NULL OR v_dur <= 1 THEN
        RAISE EXCEPTION '196 self-test: plans.max.duration_days is % — expected a recurring term', v_dur;
    END IF;

    BEGIN
        INSERT INTO auth.users (id, instance_id, aud, role, email,
                                raw_app_meta_data, raw_user_meta_data,
                                created_at, updated_at)
        VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated',
                'authenticated', format('selftest-196-%s@selftest.invalid', v_run),
                '{}'::jsonb, '{}'::jsonb, now(), now());

        SELECT u.user_id INTO v_uid FROM public.users u WHERE u.auth_id = v_auth;
        IF v_uid IS NULL THEN
            RAISE EXCEPTION '196 self-test: on_auth_user_created built no public.users row';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.user_subscriptions s WHERE s.user_id = v_uid) THEN
            RAISE EXCEPTION '196 self-test: the fake account has no user_subscriptions row (handle_new_user seed)';
        END IF;

        -- the assignment trigger stamps expires_at = now() + duration
        UPDATE public.user_subscriptions s SET plan_id = 'max' WHERE s.user_id = v_uid;
        UPDATE public.user_subscriptions s
           SET source = 'payment', usage_reset_at = NULL, renewal_grace_until = NULL
         WHERE s.user_id = v_uid;

        -- ── [A] live term, no grace ──
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        SELECT s.expires_at INTO v_old FROM public.user_subscriptions s WHERE s.user_id = v_uid;
        IF v_q.effective_plan_id IS DISTINCT FROM 'max' OR v_q.is_expired
           OR v_q.expires_at IS DISTINCT FROM v_old THEN
            RAISE EXCEPTION '196 self-test [A live]: effective %, expired %, expires % (expected max, false, %)',
                v_q.effective_plan_id, v_q.is_expired, v_q.expires_at, v_old;
        END IF;

        -- ── [B] the term ended an hour ago, the grace runs 23h more ──
        -- started_at = one duration before the term ended, so the UNPINNED key
        -- would be …:1 (a fresh allowance) right now.
        v_start := now() - make_interval(days => v_dur) - interval '1 hour';
        v_grace := now() + interval '23 hours';
        UPDATE public.user_subscriptions s
           SET started_at = v_start,
               expires_at = now() - interval '1 hour',
               renewal_grace_until = v_grace
         WHERE s.user_id = v_uid;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        v_key := 'max:' || to_char(v_start AT TIME ZONE 'UTC', 'YYYYMMDD') || ':0';
        IF v_q.effective_plan_id IS DISTINCT FROM 'max' OR v_q.is_expired
           OR v_q.expires_at IS DISTINCT FROM v_grace THEN
            RAISE EXCEPTION '196 self-test [B grace]: effective %, expired %, expires % (expected max, false, %)',
                v_q.effective_plan_id, v_q.is_expired, v_q.expires_at, v_grace;
        END IF;
        IF v_q.library_period_key IS DISTINCT FROM v_key
           OR v_q.library_period_resets_at IS DISTINCT FROM v_grace THEN
            RAISE EXCEPTION '196 self-test [B grace]: library key % resets % (expected the PINNED % resetting %)',
                v_q.library_period_key, v_q.library_period_resets_at, v_key, v_grace;
        END IF;
        SELECT l.status, l.effective_plan_id INTO v_s
          FROM public.user_subscriptions_live l WHERE l.user_id = v_uid;
        IF v_s.status IS DISTINCT FROM 'active' OR v_s.effective_plan_id IS DISTINCT FROM 'max' THEN
            RAISE EXCEPTION '196 self-test [B grace]: the view reads % / % (expected active / max)',
                v_s.status, v_s.effective_plan_id;
        END IF;

        -- ── [C] the grace ended a minute ago ──
        UPDATE public.user_subscriptions s
           SET renewal_grace_until = now() - interval '1 minute'
         WHERE s.user_id = v_uid;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        SELECT l.status INTO v_status FROM public.user_subscriptions_live l WHERE l.user_id = v_uid;
        IF v_q.effective_plan_id IS DISTINCT FROM 'free' OR NOT v_q.is_expired
           OR v_q.library_period_key NOT LIKE 'free:%' OR v_status IS DISTINCT FROM 'expired' THEN
            RAISE EXCEPTION '196 self-test [C grace over]: effective %, expired %, key %, view % (expected free, true, free:…, expired)',
                v_q.effective_plan_id, v_q.is_expired, v_q.library_period_key, v_status;
        END IF;

        -- ── [D] ended, no grace — today's fallback, unchanged ──
        UPDATE public.user_subscriptions s SET renewal_grace_until = NULL WHERE s.user_id = v_uid;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        IF v_q.effective_plan_id IS DISTINCT FROM 'free' OR NOT v_q.is_expired THEN
            RAISE EXCEPTION '196 self-test [D no grace]: effective %, expired % (expected free, true)',
                v_q.effective_plan_id, v_q.is_expired;
        END IF;

        -- ── [E] a grace on a row another source took over is inert ──
        UPDATE public.user_subscriptions s
           SET source = 'code', renewal_grace_until = now() + interval '23 hours'
         WHERE s.user_id = v_uid;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        IF v_q.effective_plan_id IS DISTINCT FROM 'free' OR NOT v_q.is_expired THEN
            RAISE EXCEPTION '196 self-test [E non-payment]: effective %, expired % (expected free, true)',
                v_q.effective_plan_id, v_q.is_expired;
        END IF;

        -- ── [F] "lapse now" on the last tick BEFORE expiry: paid time stands ──
        UPDATE public.user_subscriptions s
           SET source = 'payment',
               expires_at = now() + interval '10 minutes',
               renewal_grace_until = now() - interval '1 minute'
         WHERE s.user_id = v_uid;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        IF v_q.effective_plan_id IS DISTINCT FROM 'max' OR v_q.is_expired
           OR v_q.expires_at IS DISTINCT FROM now() + interval '10 minutes' THEN
            RAISE EXCEPTION '196 self-test [F early lapse]: effective %, expired %, expires % (expected max, false, the term''s own end)',
                v_q.effective_plan_id, v_q.is_expired, v_q.expires_at;
        END IF;

        -- ── [G1] grant_plan on a LIVE same-plan term stacks; the trigger fires
        --         (the upsert names plan_id) and does NOT re-stamp ──
        v_old := now() + interval '5 days';
        UPDATE public.user_subscriptions s
           SET expires_at = v_old, started_at = v_start, renewal_grace_until = NULL
         WHERE s.user_id = v_uid;
        PERFORM * FROM public.grant_plan(v_uid, 'max', 'payment', NULL);
        SELECT s.expires_at, s.started_at INTO v_s FROM public.user_subscriptions s WHERE s.user_id = v_uid;
        IF v_s.expires_at IS DISTINCT FROM v_old + make_interval(days => v_dur)
           OR v_s.started_at IS DISTINCT FROM v_start THEN
            RAISE EXCEPTION '196 self-test [G1 stack]: expires %, started % (expected %, %) — the trigger re-stamped a same-plan grant',
                v_s.expires_at, v_s.started_at, v_old + make_interval(days => v_dur), v_start;
        END IF;

        -- ── [G2] inside the grace grant_plan opens a fresh window; B2's
        --         separate write (no plan_id) puts the term back and sticks ──
        v_old := now() - interval '12 hours';
        UPDATE public.user_subscriptions s
           SET expires_at = v_old, started_at = v_start,
               renewal_grace_until = v_old + interval '24 hours 30 minutes'
         WHERE s.user_id = v_uid;
        PERFORM * FROM public.grant_plan(v_uid, 'max', 'payment', NULL);
        SELECT s.expires_at INTO v_s FROM public.user_subscriptions s WHERE s.user_id = v_uid;
        IF v_s.expires_at IS DISTINCT FROM now() + make_interval(days => v_dur) THEN
            RAISE EXCEPTION '196 self-test [G2 fresh]: grant_plan inside the grace wrote % (expected now() + % days) — B2''s premise changed',
                v_s.expires_at, v_dur;
        END IF;
        UPDATE public.user_subscriptions s
           SET expires_at = v_old + make_interval(days => v_dur),
               started_at = v_start,
               renewal_grace_until = NULL
         WHERE s.user_id = v_uid AND s.plan_id = 'max';
        SELECT s.expires_at, s.started_at INTO v_s FROM public.user_subscriptions s WHERE s.user_id = v_uid;
        IF v_s.expires_at IS DISTINCT FROM v_old + make_interval(days => v_dur)
           OR v_s.started_at IS DISTINCT FROM v_start THEN
            RAISE EXCEPTION '196 self-test [G2 restack]: expires %, started % after the B2 write (expected %, %)',
                v_s.expires_at, v_s.started_at, v_old + make_interval(days => v_dur), v_start;
        END IF;
        SELECT q.* INTO v_q FROM public.get_user_quota_state(v_uid) q;
        IF v_q.effective_plan_id IS DISTINCT FROM 'max' OR v_q.is_expired THEN
            RAISE EXCEPTION '196 self-test [G2 restack]: effective %, expired % (expected max, false)',
                v_q.effective_plan_id, v_q.is_expired;
        END IF;

        RAISE EXCEPTION USING ERRCODE = 'LU196', MESSAGE = '196 self-test sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU196' THEN
        RAISE NOTICE '196: behavioural self-tests A–G passed; every test write rolled back';
    END;

    -- nothing left behind
    IF (SELECT count(*) FROM public.users) <> v_users0 THEN
        RAISE EXCEPTION '196 self-test left public.users rows behind';
    END IF;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-196-%') THEN
        RAISE EXCEPTION '196 self-test left fake auth.users rows behind';
    END IF;
END $selftest$;

COMMIT;
