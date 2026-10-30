# Deep search gate — free = 1 per 30 days, low-points block for free + basic

Status: PLANNED 2026-10-30 · Owner decisions locked 2026-10-30 · Migration `197_deep_search_gate.sql`
(NOT written yet; 195 = workspace client revoke, 196 = `renewal_retry_grace.md`)

## 0. Locked decisions (owner, 2026-10-30)

1. **Free: 1 deep search per rolling 30 days** — same window as free's 5 points.
2. **Free: no deep search when remaining points < 2.**
3. **Basic: no deep search when remaining points ≤ 1** (1.0 left = blocked).
4. Pro / Max: unchanged.

Free's < 2 rule is NOT redundant with the 1-search cap: it blocks a free user who spent > 3 of
their 5 points on chat before their first search. Neither threshold prevents overruns — the
cheapest real deep search was 2.68 pts (median 5.14, p90 9.16); the gate only stops a NEW search
from starting.

Impact today: 16 free users ran 2–3 deep searches each in the last 30 days (avg 6.47 pts).

## 1. Every entry point (all `agents/orchestrator.py`) — the gate must cover all four

| # | Path | Gate location |
|---|---|---|
| 1 | Router dispatch `_route` :2646 → `_dispatch` → `_run_deep_search` :3003 (also re-routes after a planner abort :1394 / writer abort) | beside the cap pre-flight :2896, BEFORE `agent_run_started` :2910 and the progress bar :2997 |
| 2 | simple_search → deep_search fallback, fresh turn :3238-3263 (incl. `_ss_fallback_result` :3144) | before the fallback dispatch |
| 3 | simple_search fallback on resume :1733-1756 | before the fallback dispatch |
| 4 | Paused deep search resume (planner call :1249, run :1456) | :1246 — and a blocked resume MUST resolve its pause row, or every next message is pulled back into it |
| — | Blog editorial `pinned_plan` :2370 (from `deepsearch_api/generate.py:203`) | **EXEMPT** — headless, no plan |

Neither composer (`ChatInput.tsx`, mobile `Composer.tsx`) has a deep-search toggle, so no UI gate.

## 2. Schema — migration `197_deep_search_gate.sql`

- `plans.deep_search_min_points numeric NULL` — block when `remaining < value`.
  free = `2`, basic = `1.01` (encodes "≤ 1 blocked"; comment it in the migration).
- `plans.deep_search_per_window int NULL` — free = `1`, everyone else NULL (= no cap).
- NULL on pro / max / dev / promo plans = no gate. ⚠ `marketing_lawyer*` promo plans share
  basic's session/weekly shape — default NULL until the owner says otherwise (§7).
- `count_user_deep_searches(p_user_id uuid, p_since timestamptz) RETURNS int` — `SECURITY
  DEFINER`, service-role EXECUTE only: `count(DISTINCT run_id)` from `llm_calls` where
  `agent_family = 'deep_search'` and `agent` is a `deep_search.search_fee.*` or
  `deep_search.sector_picker` row (= retrieval actually ran), `created_at >= p_since`. Uses the
  existing `(user_id, created_at)` index. Verified live: resumes keep the same `run_id`; the
  simple_search fallback shares simple_search's `run_id`; neither double-counts.
- ⚠ Do NOT widen `get_user_quota_state` — that forces a DROP/CREATE of `user_subscriptions_live`
  (`shared/quota/__init__.py:526-532`, 129/129a `security_invoker` trap).

## 3. Backend

- `shared/quota.check()` (:610) already reads `get_user_quota_state` once per send
  (`message_service.py:777`) and discards it. Return the row; build a small `DeepSearchGate`
  (plan_id, remaining, min_points, per_window, window_start) and carry it into `handle_message`
  via a ContextVar (same pattern as `active_codec`). Absent ContextVar (headless) = no gate.
- remaining: free = `points_monthly − monthly_cost×100`; basic = min(session left, weekly left).
  An expired basic falls back to free inside the RPC — the free rule then applies. Points spent
  earlier in the SAME turn (router, a simple_search run before its fallback) are not in the
  snapshot — accepted (≤ ~0.5 pt).
- Window start for the free count = `max(now − 30d, usage_reset_at)`, matching the points window.
- Plan catalog values via the 60-second cached catalog read used for `max_parallel_runs`
  (:538-572) — tunable without a deploy.
- Count is read only when `per_window` is set (free), once per gated dispatch.

## 4. What the user sees (hard block, no router change in v1)

- Emit the Arabic reply as normal tokens (it becomes the saved assistant message; old app builds
  still show it) + a NEW SSE event `deep_search_gated` `{reason: "free_limit" | "low_points",
  plan_id, upgrade_options, resets_at}`. `upgrade_options` from `_upgrade_options` (:412).
- ⚠ Do NOT reuse `quota_exceeded`: web `use-chat.ts:555` and mobile `run-send.ts:374` assume
  nothing was saved and delete the optimistic message. Unknown SSE events are dropped by
  `sse-parser.ts:172`, so the new event is safe for shipped app builds.
- No agent_run row; a Logfire `deep_search.gated` event with plan_id + reason + remaining.
- Web: a `QuotaUpgradeDialog.tsx` variant (title for this case, :161-169). Mobile: a new notice
  kind in `stream-store.ts:57` + `notice-upgrade.ts`. Mobile mirrors web exactly
  (`mobile/CLAUDE.md` rule 10 — owner OK required; this plan is that ask).
- Contracts: `frontend/lib/contracts/sse.ts` (+ README), shared by web and mobile.
- Router hint (telling the router not to offer deep search) is deferred until routing evals run.

## 5. Copy that becomes false

- `frontend/components/learn/UsageLimitsView.tsx:155`, `:360-361`.
- `product_docs` rows `usage_limits` and `pricing` (the router reads them). `pricing` is ALREADY
  wrong: it says free covers "one per session and one per week" but free has neither window.
- `frontend/lib/pricing.ts` has no free card; basic card :128-129 stays true.
- Latin digits only; copy Arabic from the owner, never retype.

## 6. Tests

- `shared/quota` gate unit tests (beside `backend/tests/test_parallel_runs.py`): thresholds
  per plan, ≤ 1 boundary for basic (1.00 blocked, 1.01 allowed), free count, NULL = no gate.
- `agents/tests/test_simple_search_orchestration.py` blocked-fallback variants (:633, :705,
  :953, :1044).
- Fresh + resume block tests asserting the pause row is resolved.
- Pin the agent names the count depends on (`test_cost_ledger.py`).
- Editorial exemption (`test_editorial_publishing.py`).
- `test_unsent_messages.py` / `test_turn_row_atomicity.py` if `check()` changes signature.
- `sse-parser` event-name test; mobile `run-send.test.ts`, `notice-upgrade.test.ts`.
- SQL self-tests in 197.

## 7. Open (non-blocking, defaults in brackets)

- Do failed / crashed deep searches count toward free's 1? [count only runs that reached
  retrieval — a crash before retrieval does not count; a re-send of the same question counts].
- `marketing_lawyer*` promo plans: basic's ≤ 1 rule? [NULL = no gate].
- Exact Arabic copy + upgrade button label [owner].
- Show «بحث معمّق: 0/1» in the usage dialog / learn page? [not in v1].

## 8. Release order

Apply 197 → deploy backend (gate inert until the catalog has values — 197 seeds them, so the
gate is live on deploy) → web (new event + dialog) → mobile OTA. Backend first is safe: old
clients show the saved Arabic reply and drop the unknown event.
