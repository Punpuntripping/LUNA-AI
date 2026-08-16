-- ════════════════════════════════════════════════════════════════════════════
-- 137 — every paid purchase zeroes ALL THREE meters (points · OCR · library)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Supersedes the two narrowing guards 131 shipped with. Depends on:
--   079 (user_subscriptions), 083 (session anchor), 092 (plans.price_sar),
--   104/105 (library_unlocks + library period in get_user_quota_state),
--   113 (stamp_payment_prior_snapshot), 129 (monthly window),
--   131 (usage_reset_at + stamp_usage_reset + the current window bodies).
-- Idempotent: CREATE OR REPLACE only, plus one value-stable COMMENT.
--
-- ⚠ MUST be applied AFTER 131. §2 and §3 are CREATE OR REPLACE over 131's/105's
--   bodies with byte-identical signatures, so no dependency is dropped and
--   user_subscriptions_live is never rebuilt (see 131 §3 for why that matters).
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- Live incident, 2026-08-16. One user, eight minutes, two payments, still blocked:
--
--     09:50  free (a LAPSED `max` row)     blocked  ord/monthly  120.9 / 5 pts
--     09:51  buys `basic` — 49.90 SAR
--     09:55  basic                         blocked  ord/weekly    50.9 / 50 pts  ×4
--     09:57  buys `pro`   — 40.03 SAR (prorated)
--     09:58  pro                           blocked  ocr/monthly   48 / 40 pages
--
-- Three defects stacked, each individually defensible, jointly a customer who
-- paid twice and got nothing:
--
--   (a) 113's snapshot records `user_subscriptions.plan_id` RAW, with no expiry
--       check, so her lapsed `max` became the "prior plan" of a purchase made
--       while she was effectively on `free`. Every subsequent purchase then reads
--       as a downgrade. (§1 removes the price comparison entirely, which is what
--       makes this defect harmless rather than papering over it — the snapshot
--       keeps its own job, restoring the prior plan on an upgrade refund, where
--       raw-vs-effective is the correct reading.)
--   (b) 131's rank gate. Same-plan restack → 113 writes prior_plan_id NULL →
--       NULL prior price → `not_an_upgrade`. So a plain RENEWAL never reset
--       either — the single most common paid event in the product.
--   (c) 131's decision 3: OCR was excluded from the reset floor on the theory
--       that "a higher plan raises the page ceiling enough to unblock on its
--       own". It does not. Pages consumed under the OLD cycle keep counting for
--       30 days, so pro's 40-page cap was already 47/40 the moment she bought it.
--
-- THE NEW RULE — OWNER DECISION 2026-08-16 ────────────────────────────────────
--
--     ANY PAID PURCHASE ZEROES POINTS, OCR PAGES, AND LIBRARY UNLOCKS.
--     THE CLOCKS ARE STILL NOT TOUCHED.
--
-- 131's core distinction survives intact — an upgrade buys back your ALLOWANCE,
-- not a fresh calendar — it is only the set of meters and the set of qualifying
-- payments that widen. The session anchor stays unfiltered for exactly the
-- reason 131 spelled out; that trap is unchanged and still live.
--
-- ON THE ARBITRAGE 131 GUARDED AGAINST ────────────────────────────────────────
-- 131 refused same-plan resets so that "basic (50 pts / 49.90) does not become
-- cheaper per point than upgrading". Re-checked against the catalog, the premise
-- does not hold: basic is 1.00 SAR/weekly-point, max is 0.76 — max is cheaper per
-- point on every window, so repeat-basic is not an arbitrage, it is a worse deal
-- the customer pays real money for. And a point costs us $0.01 ≈ 0.04 SAR, so
-- every one of those purchases is ~25× margin. The guard was protecting revenue
-- from itself.
--
-- ACCEPTED, NOT OVERLOOKED — unchanged in kind from 131, wider in frequency:
-- purchase → reset → burn the fresh window → refund inside 24h. The money goes
-- back, the spend does not, and revoke_plan_grant does not rewind usage_reset_at.
-- Exposure is still bounded by the plan cap (max = 250 pts ≈ $2.50) plus, now,
-- the OCR cap (max = 200 pages ≈ $0.20 at Mistral list). It is now reachable once
-- per purchase rather than once per upgrade. If it is ever abused, the fix is to
-- defer the stamp until the refund window closes — not to reinstate a gate that
-- blocks paying customers to stop a loss two orders of magnitude smaller.

BEGIN;

-- ── 1. stamp_usage_reset — every paid purchase, not only a rank increase ─────
--
-- What is REMOVED: the three-way price comparison (new_price / prior_price /
-- strict `>`). What is KEPT, and each still load-bearing:
--
--   * `paid_at`, NEVER `now()`. The paid path runs twice by design (Moyasar
--     webhook + the client's /verify) and this RPC has no early-return anchor,
--     so it must be idempotent BY VALUE. now() would write a later stamp on the
--     replay and silently erase points the user legitimately spent in between.
--     This is 131's sharpest trap and widening the gate does nothing to it.
--   * GREATEST(...) — the floor moves forward only, so an out-of-order replay of
--     an OLDER payment cannot rewind a newer reset.
--   * The `action` column names the branch that ran, so an operator can read
--     back why a reset did or did not happen. `not_an_upgrade` is retired; a
--     paid row now always reaches `reset`.
--   * updated_at set by hand — trg_user_subscriptions_assignment is BEFORE
--     UPDATE **OF plan_id**, so this statement does not fire it and therefore
--     cannot disturb the expiry clock. Which is the point.
--
-- The `plans` joins go too: with no price test there is nothing to read from
-- them, and leaving them in would invite a future reader to reintroduce the
-- comparison. status is still deliberately unchecked — 119 lets money land on an
-- `expired` quote and be fulfilled normally, so paid_at is the honest signal.

CREATE OR REPLACE FUNCTION public.stamp_usage_reset(p_payment_id uuid)
RETURNS TABLE(target_user_id uuid, reset_at timestamp with time zone, action text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_pay RECORD;
    v_at  TIMESTAMPTZ;
BEGIN
    SELECT t.user_id, t.plan_id, t.paid_at
      INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id;

    IF NOT FOUND THEN
        -- Never raise: this runs on the webhook path, which has a finite retry
        -- budget and must not spend it on a 500 (same posture as 113).
        RAISE NOTICE 'stamp_usage_reset: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, 'payment_not_found'::text;
        RETURN;
    END IF;

    -- No deterministic timestamp to stamp. Falling back to now() here is exactly
    -- the bug this function is shaped to avoid, so this is a no-op instead. In
    -- the intended call order (mark paid → snapshot → grant → this) paid_at is
    -- always set; reaching this branch means the caller ran out of order.
    IF v_pay.paid_at IS NULL THEN
        RETURN QUERY SELECT v_pay.user_id, NULL::timestamptz, 'not_paid'::text;
        RETURN;
    END IF;

    UPDATE public.user_subscriptions s
       SET usage_reset_at = GREATEST(
               COALESCE(s.usage_reset_at, '-infinity'::timestamptz),
               v_pay.paid_at),
           updated_at     = now()
     WHERE s.user_id = v_pay.user_id
    RETURNING s.usage_reset_at INTO v_at;

    IF NOT FOUND THEN
        -- Paid, with no subscription row to reset. grant_plan runs before this
        -- and creates one, so this is an anomaly worth naming, not swallowing.
        RETURN QUERY SELECT v_pay.user_id, NULL::timestamptz, 'no_subscription'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_pay.user_id, v_at, 'reset'::text;
END;
$$;

COMMENT ON FUNCTION public.stamp_usage_reset(uuid) IS
    'Zeroes a user''s points, OCR pages and library unlocks on ANY paid purchase '
    '— upgrade, renewal, or re-purchase after a lapse (137, widening 131''s '
    'rank-increase gate) — by stamping user_subscriptions.usage_reset_at from '
    'the payment''s paid_at. Stamps paid_at and never now(), and moves the floor '
    'forward only, so the webhook + /verify double-run is idempotent by value. '
    'Touches no clock: expiry, the 5h session anchor and the plan''s own period '
    'length are all unchanged. Called AFTER grant_plan; failures are non-fatal. '
    'Service-role only.';

REVOKE EXECUTE ON FUNCTION public.stamp_usage_reset(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.stamp_usage_reset(uuid) TO service_role;

-- ── 2. get_user_usage_windows — OCR now reads the reset floor ────────────────
--
-- The ONLY change from 131's body is the two OCR expressions. Everything else is
-- reproduced verbatim, including the comments that name the traps, because the
-- next reader of this function needs them more than they need a short diff.
--
-- THE SPLIT, restated — the one place this can silently do the wrong thing:
--   UNFILTERED  calls, flagged, burst_start, tiles, sess — the anchor CTEs.
--               Filtering them moves the 5h block's start and hands out a free
--               session. Nothing in the type system or a diff review catches it;
--               the behaviour would just be more generous than agreed.
--   UNFILTERED  session_oldest — the ANCHOR, not an oldest call. The gate derives
--               resets_at from it; filtering it moves the countdown.
--   FILTERED    session_cost, weekly_cost, monthly_cost — the usage itself.
--   FILTERED    weekly_oldest, monthly_oldest — to MATCH their sums.
--   FILTERED    ocr_pages, ocr_oldest — NEW in 137 (was decision 3 in 131). The
--               pair moves together for the same reason the weekly pair does: an
--               unfiltered oldest beside a filtered sum is a lie with no visible
--               symptom. Post-reset the meter reads 0 and, with no rows left in
--               the window, ocr_oldest is NULL — which shared/quota renders as
--               "fully available, no countdown", exactly right.
--
-- Note the asymmetry with §3: OCR is a ROLLING 30-day window whose floor simply
-- rises, whereas the library allowance is a FIXED period that must start a NEW
-- one. Same stamp, two correct-but-different readings of it.

CREATE OR REPLACE FUNCTION public.get_user_usage_windows(p_user_id uuid)
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
    -- The reset floor, resolved once. Coalesced to -infinity so that "never
    -- reset" and "no subscription row at all" both mean "count everything" and
    -- no expression below needs a NULL branch.
    usage_reset AS (
        SELECT COALESCE(
                   (SELECT s.usage_reset_at
                      FROM public.user_subscriptions s
                     WHERE s.user_id = p_user_id),
                   '-infinity'::timestamptz) AS reset_at
    ),
    -- ─── Anchor CTEs (083). UNFILTERED by usage_reset — see the split above.
    --     Touching any of the next five is how "keep the time" turns into a free
    --     5-hour block.
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
        -- Session points. The lower bound moves to the reset, the UPPER bound
        -- stays anchor + 5h. That asymmetry IS the decision: same window, same
        -- expiry, zero spend inside it.
        --
        -- This sublink joins usage_reset again rather than correlating to the
        -- outer `ur`, and must: the outer query aggregates with no GROUP BY, so
        -- an outer reference to ur.reset_at from the target list is an ungrouped
        -- column and PostgreSQL rejects it. Do not "simplify" this to ur.reset_at
        -- — and above all do not add a GROUP BY to make it compile. It is one row
        -- from a primary-key lookup either way.
        COALESCE((SELECT SUM(c.cost_usd)
                    FROM calls c, sess s, usage_reset u
                   WHERE s.anchor IS NOT NULL
                     AND c.created_at >= GREATEST(s.anchor, u.reset_at)
                     AND c.created_at <  s.anchor + interval '5 hours'), 0)::double precision,
        COALESCE(SUM(cost_usd) FILTER (
            WHERE created_at >= GREATEST(now() - interval '7 days', ur.reset_at)), 0)::double precision,
        -- OCR pages — FILTERED as of 137. A purchase buys back the page
        -- allowance, so pages scanned before the payment must stop counting; the
        -- 30-day roll-off stays for everyone who has not bought anything.
        COALESCE(SUM(pages_used) FILTER (
            WHERE created_at >= GREATEST(now() - interval '30 days', ur.reset_at)), 0)::bigint,
        -- The session ANCHOR, not an oldest call. Unfiltered on purpose.
        (SELECT anchor FROM sess),
        -- Matches the weekly sum's lower bound exactly; the two must never drift
        -- apart or the countdown stops describing the usage it accompanies.
        MIN(created_at) FILTER (
            WHERE created_at >= GREATEST(now() - interval '7 days', ur.reset_at)),
        -- Same rule for OCR now that its sum is filtered (137).
        MIN(created_at) FILTER (
            WHERE pages_used > 0
              AND created_at >= GREATEST(now() - interval '30 days', ur.reset_at)),
        -- The 30-day bound is already the outer WHERE clause (129), so the
        -- monthly pair needs only the reset floor added — no GREATEST, and still
        -- no extra pass over llm_calls.
        COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= ur.reset_at), 0)::double precision,
        MIN(created_at) FILTER (WHERE created_at >= ur.reset_at)
    FROM public.llm_calls
    -- Exactly one row, so this changes no cardinality; it exists only to make
    -- reset_at a plain column reference usable inside the FILTER clauses above.
    CROSS JOIN usage_reset ur
    WHERE user_id = p_user_id
      AND created_at >= now() - interval '30 days';
$function$;

COMMENT ON FUNCTION public.get_user_usage_windows(uuid) IS
  'Rolling usage windows straight off the llm_calls ledger: 5h session (fixed '
  'anchor, 083), 7d, and 30d for both OCR pages and points (129). Every SUM and '
  'its matching oldest-call timestamp — points AND OCR pages (137) — starts at '
  'user_subscriptions.usage_reset_at when set. The session ANCHOR alone stays '
  'unfiltered, so a purchase zeroes the usage without moving any clock.';

REVOKE EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) TO service_role;

-- ── 3. get_user_quota_state — the library period restarts at the purchase ────
--
-- The library meter is NOT a rolling window, so it cannot read usage_reset_at as
-- a floor the way §2 does. It is a per-period allowance counted as
-- SUM(library_unlocks.cost) WHERE period_key = <the current key>, and the ONLY
-- way to zero it is to make the current key a DIFFERENT string — old rows keep
-- their old key and stop matching. Nothing is deleted or rewritten.
--
-- Today the key is anchored on `started_at`, and grant_plan deliberately PRESERVES
-- started_at when it extends an active same-plan subscription (that is what makes
-- renewal stack the remaining days rather than truncate them). So a renewal left
-- the key — and therefore the spent unlocks — exactly where they were. The plan
-- change in the 2026-08-16 incident moved started_at and hid this; a straight
-- renewal would not have.
--
-- The fix is one anchor expression, GREATEST(started_at, usage_reset_at), threaded
-- through all three places that must agree — idx, the YYYYMMDD in the key, and
-- resets_at. They are computed from `a` in a single LATERAL precisely so they
-- cannot be updated one at a time; a key that disagrees with its own resets_at
-- would show a user a countdown to a renewal that already happened.
--
-- GREATEST ignores NULL operands in PostgreSQL, so all four combinations behave:
--   both set        → the later one wins (a renewal restarts the period)
--   reset only      → the reset anchors it (a subscription row with no started_at)
--   started only    → today's behaviour, unchanged for everyone who never paid
--   neither         → idx NULL → the free-plan calendar-month branch, unchanged.
--
-- Everything else in this function is reproduced verbatim from 105/129. The
-- RETURNS TABLE is byte-identical, so user_subscriptions_live keeps its OID
-- dependency and is NOT rebuilt — see 131 §3 for why rebuilding it from an old
-- migration silently drops operator columns.

CREATE OR REPLACE FUNCTION public.get_user_quota_state(p_user_id uuid)
RETURNS TABLE(
    locked boolean, plan_id text, plan_name_ar text,
    expires_at timestamp with time zone, is_expired boolean,
    effective_plan_id text, effective_name_ar text,
    points_session integer, points_weekly integer, points_monthly integer,
    ocr_pages_monthly integer, web_calls_monthly integer,
    session_cost double precision, weekly_cost double precision, ocr_pages bigint,
    session_oldest timestamp with time zone, weekly_oldest timestamp with time zone,
    ocr_oldest timestamp with time zone,
    library_unlocks_limit integer, library_unlocks_used integer,
    library_period_key text, library_period_resets_at timestamp with time zone,
    monthly_cost double precision, monthly_oldest timestamp with time zone
)
LANGUAGE sql
STABLE
SECURITY DEFINER
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
    -- The period anchor (137). ONE expression, consumed by all three outputs
    -- below so they cannot drift apart. GREATEST ignores NULLs, so a row with no
    -- usage_reset_at yields plain started_at — i.e. pre-137 behaviour — and a
    -- purchase moves the anchor forward, minting a new period_key and a
    -- resets_at measured from the payment.
    CROSS JOIN LATERAL (
        SELECT GREATEST(s.started_at, s.usage_reset_at) AS at
    ) pa
    CROSS JOIN LATERAL (
        SELECT CASE
            WHEN ep.duration_days IS NULL OR pa.at IS NULL THEN NULL
            ELSE floor(
                     extract(epoch FROM (now() - pa.at))
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
    'counting without any row being deleted.';

REVOKE EXECUTE ON FUNCTION public.get_user_quota_state(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_quota_state(uuid) TO service_role;

-- ── 4. the column comment — 131's is now wrong in two places ────────────────
--
-- It claimed the stamp happens "only on a rank increase" and "deliberately does
-- NOT gate OCR pages". Both were true of 131 and are false of 137. A stale
-- COMMENT on the one column three functions read is worse than no comment: it is
-- what the next person checks before touching this.

COMMENT ON COLUMN public.user_subscriptions.usage_reset_at IS
    'Floor for EVERY usage meter (137): llm_calls older than this count for '
    'neither points nor OCR pages in get_user_usage_windows, and '
    'get_user_quota_state anchors the library period at '
    'GREATEST(started_at, usage_reset_at) so unlocks reset too. NULL = never '
    'purchased/reset and behaves as -infinity, i.e. count everything. Stamped '
    'ONLY by stamp_usage_reset, on ANY paid purchase (131 restricted this to '
    'rank increases; 137 widened it after a renewal left a paying customer '
    'blocked), with the payment''s paid_at — never now(). Moves forward only. '
    'Moves no clock: not expiry, not the 5h session anchor.';

COMMIT;
