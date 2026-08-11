-- ════════════════════════════════════════════════════════════════════════════
-- 131 — usage reset on upgrade: a bigger plan clears the meter immediately
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/quota_upgrade_ladder.md — Part A (A1–A5), Part E, Traps.
-- Depends on: 079 (user_subscriptions + handle_subscription_assignment),
--             083 (fixed session anchor), 092 (payment_transactions, plans.price_sar),
--             113 (prior_plan_id snapshot, stamp_payment_prior_snapshot precedent),
--             129 (monthly points window; get_user_usage_windows' current shape).
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE / value-stable UPDATEs.
--
-- ⚠ MUST be applied AFTER 129. The function replaced in §3 below is 129's version,
--   including its `monthly_cost` / `monthly_oldest` columns. Applying this file to
--   a pre-129 database would ADD those columns without the rest of 129's changes
--   and leave `user_subscriptions_live` mismatched against the widened alias list.
--
-- NUMBERING: this file was drafted as 130 and renumbered to 131 — a
--   `130_judgment_sitemap_indexable.sql` already existed. The two are independent
--   (different objects, no shared dependencies) and may run in either order.
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- Quota usage is a rolling SUM over the `llm_calls` ledger, not a balance that
-- can be topped up. So a paying user who burns their weekly points and then buys
-- a bigger plan is still blocked: the same spend still sits in the same window,
-- now merely measured against a larger cap that it may still exceed. The product
-- takes the customer's money and hands them the same wall.
--
-- Fixing this at the point of sale is also the strongest honest thing the upsell
-- can say — «الترقية تصفّر استهلاكك الحالي وتعيدك للعمل فوراً» — which is the
-- whole reason the ladder in Parts B–D is worth building at all.
--
-- The owner decision (2026-08-11) is precise, and the precision matters:
--
--     ZERO THE USAGE, KEEP THE CLOCKS.
--
-- "If the user has 4 hours left and consumed 60 points, those points become 0" —
-- the 4 hours stay 4 hours. An upgrade buys back your allowance, not a fresh
-- calendar. §3 is where that distinction is either honoured or silently lost.
--
-- THREE GUARDS, EACH LOAD-BEARING ──────────────────────────────────────────────
--   * RANK INCREASE ONLY. Re-buying the SAME plan must not reset, or basic
--     (50 pts / 49.90) becomes cheaper per point than upgrading and the ladder is
--     quietly arbitraged from below.
--   * POINTS ONLY. OCR pages are untouched (owner): a higher plan raises the page
--     ceiling enough to unblock on its own, and OCR spend is a real cost already
--     incurred, not a metered allowance.
--   * `paid_at`, NEVER `now()`. See §2 — this is the one that bites silently.
--
-- ACCEPTED, NOT OVERLOOKED ─────────────────────────────────────────────────────
-- upgrade → reset → spend the fresh window → refund inside the 24-hour window.
-- The money goes back; the LLM spend does not. 119's supersede logic protects
-- CREDIT, not consumed usage. Exposure is bounded by the plan cap (max = 250
-- points ≈ $2.50/cycle) and the owner accepted it explicitly rather than defer
-- the reset until the refund window closes — deferral would cost exactly the
-- instant gratification the whole upsell rests on. If it is ever abused, that
-- deferral is the fix.

BEGIN;

-- ── 1. `usage_reset_at` — the floor every points window reads from ───────────
--
-- One nullable timestamp per subscription. NULL is the normal state and means
-- "never reset", which is why no DEFAULT is given: every existing row must stay
-- NULL so that applying this file forgives nobody's accrued usage retroactively.
-- The windows read GREATEST(window_start, COALESCE(usage_reset_at, '-infinity')),
-- so a NULL is exactly a no-op and needs no special case anywhere downstream.

ALTER TABLE public.user_subscriptions
    ADD COLUMN IF NOT EXISTS usage_reset_at timestamptz;

COMMENT ON COLUMN public.user_subscriptions.usage_reset_at IS
    'Floor for the POINTS windows in get_user_usage_windows (131): llm_calls '
    'older than this are not counted. NULL = never reset (the state of every row '
    'before 131 and of every user who has never upgraded) and behaves as '
    '-infinity, i.e. count everything. Stamped ONLY by stamp_usage_reset, and '
    'only on a rank increase, with the payment''s paid_at — never now(). Moves '
    'forward only (GREATEST). Deliberately does NOT gate OCR pages: an upgrade '
    'buys back the points allowance, not the pages already scanned.';

-- ── 2. stamp_usage_reset — the only writer of that column ────────────────────
--
-- A SEPARATE RPC rather than a change to grant_plan, following the precedent 113
-- set with stamp_payment_prior_snapshot and the reasoning it recorded: grant_plan
-- is the live money path, migration files are not the prod schema, and a blind
-- CREATE OR REPLACE over the one function that moves money can silently revert
-- prod drift. Additive beats destructive. The backend calls this AFTER grant_plan
-- (see quota_upgrade_ladder.md §A4) so a failed reset can never block a grant the
-- customer has already paid for.
--
-- RANK IS `price_sar`, NOT A NEW COLUMN ────────────────────────────────────────
-- The ladder already exists twice — PLAN_RANK in payment_service.py and the
-- implicit price order in `plans`. A `plans.rank` column would make it three
-- copies to drift apart. price_sar is already the authoritative amount checkout
-- charges, and the invariant it rests on is:
--
--     PRICE ORDER == CAPABILITY ORDER  (49.90 < 89.90 < 189.90)
--
-- True today. Rank-less plans (free, marketing_*, dev) have price_sar IS NULL and
-- therefore never trigger a reset, which matches the existing "rank-less earns no
-- credit" rule in create_checkout. IF A PLAN IS EVER PRICED OUT OF CAPABILITY
-- ORDER, add an explicit rank column and switch BOTH this RPC and PLAN_RANK to it.
--
-- WHY `paid_at` AND NOT `now()` ────────────────────────────────────────────────
-- The paid path runs twice by design — the Moyasar webhook and the client's
-- /verify confirmation both drive it, and grant_plan is idempotent by early
-- return rather than by locking them out. This RPC has no such early-return
-- anchor, so it must be idempotent BY VALUE: a second run has to write the
-- identical timestamp. now() would write a LATER one and silently erase every
-- point spent between the two runs — usage the user legitimately consumed on the
-- plan they just bought. paid_at is fixed at the moment the money landed, so a
-- replay is a no-op write. The GREATEST additionally stops an out-of-order replay
-- of an OLDER payment from rewinding a newer reset.
--
-- The `action` column names the branch that ran, mirroring revoke_plan_grant
-- (113); the backend logs it, and an operator can read back why a reset did or
-- did not happen without re-deriving prices by hand.
--
-- OUT-parameter names are deliberately NOT `user_id` / `usage_reset_at`: inside a
-- plpgsql body those would shadow the identically-named columns of the very
-- tables this function reads, and the resulting ambiguity is a runtime error, not
-- a compile-time one.

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
    -- One read for the payment AND both prices. LEFT JOIN on both sides so an
    -- unpriced or missing plan yields a NULL price rather than dropping the row —
    -- an inner join would make "free → basic" indistinguishable from "payment not
    -- found", and those two want different actions in the log.
    SELECT t.user_id, t.plan_id, t.prior_plan_id, t.paid_at,
           np.price_sar AS new_price,
           pp.price_sar AS prior_price
      INTO v_pay
      FROM public.payment_transactions t
      LEFT JOIN public.plans np ON np.plan_id = t.plan_id
      LEFT JOIN public.plans pp ON pp.plan_id = t.prior_plan_id
     WHERE t.payment_id = p_payment_id;

    IF NOT FOUND THEN
        -- Never raise: this runs on the webhook path, which has a finite retry
        -- budget and must not be spent on a 500 (same posture as 113).
        RAISE NOTICE 'stamp_usage_reset: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, 'payment_not_found'::text;
        RETURN;
    END IF;

    -- No deterministic timestamp to stamp. Falling back to now() here is exactly
    -- the bug the whole function is shaped to avoid, so this is a no-op instead.
    -- In the intended call order (mark paid → snapshot → grant → this) paid_at is
    -- always set; reaching this branch means the caller ran out of order.
    -- status is deliberately NOT checked: 119 allows money to land on an
    -- `expired` quote and be fulfilled normally, so paid_at is the honest signal.
    IF v_pay.paid_at IS NULL THEN
        RETURN QUERY SELECT v_pay.user_id, NULL::timestamptz, 'not_paid'::text;
        RETURN;
    END IF;

    -- The rank gate. A NULL prior price covers three real cases at once, and all
    -- three are correct no-ops:
    --   * prior_plan_id IS NULL — 113 leaves it NULL for a SAME-PLAN restack, so
    --     the anti-arbitrage guard (decision 2) falls out of the snapshot
    --     semantics for free, and the strict `>` below catches it a second time;
    --   * the prior plan was rank-less (free, marketing_*, dev);
    --   * the prior plan row is gone.
    -- A NULL new price means the grant itself was rank-less — a promo code path,
    -- which earns no reset for the same reason it earns no upgrade credit.
    IF v_pay.new_price IS NULL
       OR v_pay.prior_price IS NULL
       OR v_pay.new_price <= v_pay.prior_price THEN
        RETURN QUERY SELECT v_pay.user_id, NULL::timestamptz, 'not_an_upgrade'::text;
        RETURN;
    END IF;

    -- GREATEST already ignores NULL operands, so the COALESCE is documentation
    -- rather than logic — it states in the code that NULL means -infinity, which
    -- is the same claim §1's column comment and §3's window CTE make.
    --
    -- Two guards live in this single statement:
    --   * monotonicity — a replayed OLDER payment cannot rewind a newer reset;
    --   * concurrency — under READ COMMITTED a second writer blocks on the row
    --     lock and then re-evaluates SET against the freshly committed version,
    --     so a simultaneous webhook + /verify pair converges on the same value
    --     instead of one clobbering the other.
    --
    -- updated_at is set by hand: trg_user_subscriptions_assignment is BEFORE
    -- UPDATE **OF plan_id**, so a statement that touches neither plan_id nor
    -- expires_at does not fire it. That is also why this write cannot disturb the
    -- expiry clock — which is the point (keep the clocks).
    UPDATE public.user_subscriptions s
       SET usage_reset_at = GREATEST(
               COALESCE(s.usage_reset_at, '-infinity'::timestamptz),
               v_pay.paid_at),
           updated_at     = now()
     WHERE s.user_id = v_pay.user_id
    RETURNING s.usage_reset_at INTO v_at;

    IF NOT FOUND THEN
        -- Paid for an upgrade with no subscription row to reset. grant_plan runs
        -- before this and creates one, so this is an anomaly worth naming rather
        -- than swallowing.
        RETURN QUERY SELECT v_pay.user_id, NULL::timestamptz, 'no_subscription'::text;
        RETURN;
    END IF;

    RETURN QUERY SELECT v_pay.user_id, v_at, 'reset'::text;
END;
$$;

COMMENT ON FUNCTION public.stamp_usage_reset(uuid) IS
    'Zeroes a user''s POINTS usage on a rank increase by stamping '
    'user_subscriptions.usage_reset_at from the payment''s paid_at (131). Rank is '
    'plans.price_sar; both sides must be non-NULL and the new price strictly '
    'greater, so same-plan restacks and rank-less plans no-op. Stamps paid_at and '
    'never now(), and moves the floor forward only, so the webhook + /verify '
    'double-run is idempotent by value. OCR pages are deliberately untouched. '
    'Called AFTER grant_plan; failures are non-fatal. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.stamp_usage_reset(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.stamp_usage_reset(uuid) TO service_role;

-- ── 3. get_user_usage_windows — the anchor/sum split ─────────────────────────
--
-- REPLACE, NOT DROP-AND-RECREATE. 129 had to drop this function and, ahead of it,
-- get_user_quota_state and user_subscriptions_live, because it APPENDED
-- monthly_cost / monthly_oldest and PostgreSQL will not let CREATE OR REPLACE
-- change a function's return type. This migration changes only the BODY — the
-- signature and the eight-column RETURNS TABLE are byte-identical to 129's — so
-- CREATE OR REPLACE keeps the same OID, every dependency stays valid, and the
-- view is never dropped. That matters beyond convenience: rebuilding
-- user_subscriptions_live requires reading its LIVE pg_get_viewdef first, and
-- reconstructing it from an older migration silently drops operator columns
-- (129's own header carries this warning). Not touching it cannot get it wrong.
-- get_user_quota_state and the view are therefore absent from this file entirely.
-- Should a future migration change these columns, the full 129 dance returns:
-- drop view → drop quota_state → drop usage_windows → create → create → rebuild
-- view from the live definition → re-grant all three.
--
-- THE SPLIT — THE ONE PLACE THIS CAN SILENTLY DO THE WRONG THING ───────────────
-- The session anchor is computed by a recursive CTE over the SAME call set the
-- function sums. Filtering that call set by usage_reset_at would move the anchor
-- forward to the first post-reset call and hand the user a brand-new 5-hour
-- block — the exact opposite of "keep the time, erase the usage", and nothing in
-- the type system, the tests, or a code review of the diff alone would catch it.
-- The behaviour would simply be more generous than the owner agreed to. So:
--
--   UNFILTERED  calls, flagged, burst_start, tiles, sess   — the anchor CTEs.
--               The 5-hour block keeps its original boundary.
--   UNFILTERED  session_oldest — it is NOT an "oldest call"; it is the ANCHOR,
--               and the gate derives resets_at from it. Filtering it would move
--               the countdown, which is the clock we promised not to touch.
--   FILTERED    session_cost, weekly_cost, monthly_cost — the usage itself.
--   FILTERED    weekly_oldest, monthly_oldest — to MATCH their sums, so the
--               countdown a user is shown is the countdown for the usage they are
--               actually charged for. An unfiltered oldest beside a filtered sum
--               is a lie with no visible symptom.
--   UNFILTERED  ocr_pages, ocr_oldest — points only (owner decision 3).
--
-- The floor is read here rather than passed in, because the signature must stay
-- (p_user_id uuid) — see the REPLACE note above. This adds one indexed
-- single-row lookup on user_subscriptions. It grants nothing new: the only two
-- callers are get_user_quota_state, which is SECURITY DEFINER and already selects
-- from that very table in its own body, and service_role, which bypasses RLS.

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
        -- OCR is a meter over consumed pages, not an allowance an upgrade buys
        -- back (decision 3) — no reset_at anywhere on these two.
        COALESCE(SUM(pages_used) FILTER (WHERE created_at >= now() - interval '30 days'), 0)::bigint,
        -- The session ANCHOR, not an oldest call. Unfiltered on purpose.
        (SELECT anchor FROM sess),
        -- Matches the weekly sum's lower bound exactly; the two must never drift
        -- apart or the countdown stops describing the usage it accompanies.
        MIN(created_at) FILTER (
            WHERE created_at >= GREATEST(now() - interval '7 days', ur.reset_at)),
        MIN(created_at) FILTER (WHERE pages_used > 0 AND created_at >= now() - interval '30 days'),
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
  'anchor, 083), 7d, and 30d for both OCR pages and points (129). Points sums '
  'and their oldest-call timestamps start at user_subscriptions.usage_reset_at '
  'when set (131) — the session ANCHOR and the OCR figures deliberately do not, '
  'so an upgrade zeroes the usage without moving any clock.';

REVOKE EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_usage_windows(uuid) TO service_role;

-- ── 4. marketing_lawyer: weekly 76 → 74 ─────────────────────────────────────
--
-- 082 set 76, which sits ABOVE pro's 75. That one point makes pro a downgrade on
-- the weekly window, so Part C's ladder — which offers only plans with a strictly
-- GREATER limit on the window that blocked you — would never offer pro to a
-- marketing_lawyer user, no matter how the copy is written. 74 makes pro a
-- genuine upgrade and the ladder correct without special-casing anything.
--
-- points_session STAYS 15 (owner: "keep it that way"). It ties pro's 15, so on a
-- SESSION block the ladder still offers max only — which is right: pro would not
-- raise the limit that blocked them. The two windows disagreeing about which
-- plans help is the ladder working, not a bug to smooth over.
--
-- Plan rows are data; the backend's plan cache picks this up within 5 minutes.
-- marketing_* plans are code-activated promos and are not on /pricing, so
-- lib/pricing.ts needs no sync.

UPDATE public.plans
   SET points_weekly = 74,
       updated_at    = now()
 WHERE plan_id = 'marketing_lawyer';

COMMIT;
