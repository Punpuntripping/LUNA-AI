# Quota upgrade ladder — plan-aware upsell + usage reset on upgrade

**Status:** PLANNED, not built. **Owner decisions locked 2026-08-11.**

## Why

The quota block is currently a dead end for anyone who has already paid. A `basic`
user who burns their weekly points gets a thin banner and a countdown; the product
has nothing to say to them. Meanwhile the free-tier paywall (built, unshipped —
see *Prerequisites*) shows all three plans to free users only.

This extends the block into a ladder: **show the plans that would actually unblock
you, and be honest that waiting is the other option.**

Two facts make this worth doing properly rather than as a banner tweak:

1. **Usage is a rolling sum over `llm_calls`, not a balance.** Re-buying the same
   plan changes nothing — the same spend still sits in the same window against the
   same cap. Only a *higher limit* clears the block. So the ladder is not merely a
   nicer upsell; offering the current plan or a lower one would be selling
   something that provably does not solve the user's problem.
2. **The server already enforces this ladder.** `payment_service.PLAN_RANK`
   (`basic:1, pro:2, max:3`) plus `DOWNGRADE_BLOCKED_AR` means checkout already
   refuses a downgrade. The UI is surfacing existing policy, not inventing it.

## Prerequisites

This builds directly on work that is **written and not yet deployed**:

- Free-tier paywall — `QuotaUpgradeDialog`, `QuotaBanner` rewrite, `plan_id` on the
  `quota_exceeded` SSE event.
- Message discard on quota block — gate moved above the insert in
  `message_service.py`, composer restore in `use-chat.ts`.
- **Migration 129** (`free_monthly_window`) — free gets one 30-day window;
  `pro`/`max` `points_monthly` set NULL.

**129 must be applied before any of this ships.** The window-reporting change in
Part B assumes the post-129 shape (free = monthly only, paid = session + weekly).

## Locked decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Reset **zeroes usage, keeps the clocks** | Owner: "if the user has 4 hours left and consumed 60 points, those points become 0" |
| 2 | Reset fires on **rank increase only** | Same-plan restack resetting would make re-buying `basic` (50 pts / 49.90) cheaper per point than upgrading — quiet arbitrage against the ladder |
| 3 | **Points only** — OCR pages are not reset | Owner. Upgrading raises the page ceiling enough to unblock on its own |
| 4 | Refund-window exposure **accepted** | Owner: "do not overthink it, it's an applicable price". See *Traps* |
| 5 | `max` block → **wait only**, no escalation | Owner |
| 6 | Ladder computed **server-side** | `lib/pricing.ts` carries limits only as Arabic prose; a frontend ladder needs a second copy of every number to drift against |
| 7 | Entry points: **banner button + account settings** | Owner: "both" |
| 8 | `marketing_lawyer` weekly `76 → 74` | Makes `pro` (75) a genuine upgrade. Session stays 15 (owner: "keep it that way") |
| 9 | Paid users get **no auto-modal** | A full-screen pitch at someone who paid 89.90 reads differently than at a free user |

## Part A — usage reset on upgrade

### A1. `usage_reset_at`

`ALTER TABLE public.user_subscriptions ADD COLUMN usage_reset_at timestamptz;`

NULL = never reset (every existing row). The windows read
`GREATEST(window_start, COALESCE(usage_reset_at, '-infinity'))`.

### A2. Rank comparison uses `price_sar`, not a new column

The rank ladder already exists twice — `PLAN_RANK` in Python and the implicit
price order in `plans`. Adding `plans.rank` would make it three copies. Instead the
RPC compares `plans.price_sar`, which is **already the authoritative amount** the
checkout charges.

> **Invariant this rests on:** price order equals capability order
> (49.90 < 89.90 < 289.90). True today — re-verified after migration 147 raised
> `max` on 2026-08-29. Rank-less plans (`free`, `marketing_*`,
> `dev`) have `price_sar IS NULL` and therefore never trigger a reset — which
> matches the existing "rank-less earns no credit" rule in `create_checkout`.
> **If a plan is ever priced out of capability order, add an explicit `rank`
> column and switch both this RPC and `PLAN_RANK` to it.**

### A3. `stamp_usage_reset(p_payment_id uuid)` — new service-role RPC

Deliberately a **separate RPC**, not a change to `grant_plan`. Migration 113 set
this precedent: `grant_plan` is the live money path and gets touched as little as
possible, which is why `stamp_payment_prior_snapshot` exists as its own call.

Body:

1. Read `payment_transactions` → `user_id`, `plan_id` (new), `prior_plan_id`, `paid_at`.
2. Join `plans` twice for `price_sar` of new and prior.
3. No-op unless `new.price_sar IS NOT NULL AND prior.price_sar IS NOT NULL AND
   new.price_sar > prior.price_sar`.
4. `UPDATE user_subscriptions SET usage_reset_at = GREATEST(COALESCE(usage_reset_at, '-infinity'), pt.paid_at) WHERE user_id = …`

> **Stamp `paid_at`, never `now()`.** The paid path can run twice (webhook +
> client confirmation) and `grant_plan` is idempotent by early-return, so this RPC
> must be idempotent *by value*. `now()` on a second run would push the reset
> forward and silently erase usage accrued in between. `paid_at` is deterministic,
> so a re-run writes the identical value. The `GREATEST` guard additionally stops
> an out-of-order replay of an older payment from rewinding a newer reset.

### A4. Call site

`payment_service.py`, in the paid path that already reads `prior_plan_id`
(≈ line 1471). Order becomes:

```
mark paid → stamp_payment_prior_snapshot → grant_plan → stamp_usage_reset
```

**After** `grant_plan`, so a failure to reset can never block a grant the customer
paid for. Wrap in try/except and log — matching how `_stamp_prior_snapshot` treats
its own failure as non-fatal.

### A5. `get_user_usage_windows` — the anchor/sum split

This is the subtle part and the one place the implementation can silently do the
wrong thing.

The function computes the session anchor with a recursive CTE over the **same call
set** it sums. Filtering that call set by `usage_reset_at` would move the anchor
too, handing the user a fresh 5-hour block. The decision is "keep the time, erase
the usage", so:

- **Anchor CTEs (`calls`, `flagged`, `burst_start`, `tiles`, `sess`) — UNFILTERED.**
  The session keeps its original boundary.
- **Cost sums (`session_cost`, `weekly_cost`, `monthly_cost`) — FILTERED** by
  `created_at >= COALESCE(usage_reset_at, '-infinity')`.
- **`weekly_oldest` / `monthly_oldest` — FILTERED** to match their sums, so the
  countdown a user sees matches the usage they are charged for.
- **`ocr_pages` / `ocr_oldest` — UNFILTERED** (decision 3).

The function takes only `p_user_id`, so it reads `usage_reset_at` itself via a
scalar subquery on `user_subscriptions`.

## Part B — report the window that actually binds

`quota.check()` today checks shortest-first and raises on the **first** breach, and
its docstring calls this deliberate: "so the user sees the soonest-to-recover
limit". That is wrong when more than one window is blown — it reports «٤ ساعات» to
someone who is actually stuck for five days. Same class of dishonesty as the free
tier's 5-hour message.

**Change:** evaluate every ord window, collect the breaches, raise on the one with
the **furthest `resets_at`**. Single breach behaves exactly as today.

The OCR and web meters keep raising immediately — they are independent meters, not
competing windows over the same resource.

## Part C — the ladder on the wire

`QuotaExceeded` gains `upgrade_options: list[str]`, emitted in `to_event_payload()`
alongside `plan_id`.

Computed on the block path only (blocks are rare, so a second query is free), by a
helper `_upgrade_options(supabase, plan_id, meter, period)`:

```sql
SELECT plan_id FROM plans
 WHERE price_sar IS NOT NULL
   AND (<current plan price> IS NULL OR price_sar > <current plan price>)
   AND <limit column for the blocking window> > <current limit>
 ORDER BY price_sar
```

Two filters, both load-bearing:

- **price** — mirrors the downgrade guard, so the dialog never offers something
  checkout will refuse.
- **strictly greater limit on the blocking window** — this is what makes the
  `marketing_lawyer` case fall out for free. On a *session* block their 15 ties
  `pro`'s 15, so `pro` is not offered; only `max` (50) is. Offering a plan that
  does not raise the limit that blocked you is the same error as offering a
  downgrade.

`max` blocked → empty list → frontend shows wait-only. `PlanInactive` keeps
`plan_id: null` and emits an empty list: an unactivated account is not fixed by
buying anything.

## Part D — frontend

### D1. `QuotaUpgradeDialog`

- Renders **only** the plans named in `upgrade_options`, in the given order.
  One card for a blocked `pro`, two for a blocked `basic`, three for free.
- Headline branches: free keeps «انتهى حدّ الاستخدام المجاني»; paid gets
  «انتهت نقاط باقتك — {plan_name}».
- **Both options stated, wait first.** «تعود نقاطك {resets} — أو رقِّ باقتك الآن.»
- Add the honest, load-bearing selling point: **upgrading unblocks immediately.**
  This follows from Part A and is worth stating plainly — «الترقية تصفّر استهلاكك
  الحالي وتعيدك للعمل فوراً» — because it is the strongest true thing we can say.

### D2. `QuotaBanner`

- `shouldOfferUpgrade` becomes `upgrade_options.length > 0` (replacing the
  `plan_id === "free"` check).
- **Auto-modal stays free-only** (decision 9). Paid users get the banner plus a
  «ترقية الباقة» button that opens the same dialog on demand.
- Empty `upgrade_options` (i.e. `max`) → banner only, no button, no dialog.

### D3. `AccountSettingsDialog`

A «ترقية الباقة» button in the existing `الاشتراك` section
(`data-testid="subscription-section"`, ≈ line 588), beside «إلغاء الاشتراك».

Opens the same dialog with the plans priced above the current one. There is no
blocking window here, so the ladder is display-only and can be derived client-side
from `PRICING_PLANS` by price — no limit numbers needed, no drift risk.

This is the entry point that matters commercially: it lets a `pro` user upgrade
*before* hitting a wall, rather than only in the moment of frustration.

## Part E — data

`UPDATE plans SET points_weekly = 74 WHERE plan_id = 'marketing_lawyer';`

## File manifest

| File | Change |
|---|---|
| `shared/db/migrations/131_usage_reset_on_upgrade.sql` | NEW — `usage_reset_at`, `stamp_usage_reset`, `get_user_usage_windows` rebuild, `marketing_lawyer` 74. Numbered **131**, not 130: a `130_judgment_sitemap_indexable.sql` already existed |
| `shared/quota/__init__.py` | `upgrade_options` on `QuotaExceeded`; furthest-reset selection in `check()`; `_upgrade_options()` helper |
| `backend/app/services/payment_service.py` | `_stamp_usage_reset()` + call after `_grant_plan` |
| `frontend/types/index.ts` | `upgrade_options` on `SSEQuotaExceeded` |
| `frontend/components/chat/QuotaUpgradeDialog.tsx` | Filter by `upgrade_options`; paid headline; wait-or-upgrade copy |
| `frontend/components/chat/QuotaBanner.tsx` | Trigger on `upgrade_options`; «ترقية الباقة» button; free-only auto-modal |
| `frontend/components/Settings/AccountSettingsDialog.tsx` | «ترقية الباقة» in the `الاشتراك` section |
| `frontend/components/learn/UsageLimitsView.tsx` | `marketing_lawyer` is not on this page — no change, listed so the check is not skipped |

## Traps

1. **~~131 must repeat 129's drop-view → drop-functions → rebuild dance.~~
   RESOLVED — it does not, and should not.** This trap assumed a return-type
   change. 131 only changes the *body* of `get_user_usage_windows`; its eight-column
   signature is byte-identical to 129's, so `CREATE OR REPLACE` is legal, keeps the
   OID, and leaves every dependency valid. `get_user_quota_state` and
   `user_subscriptions_live` are therefore **absent from 131 entirely** — which is
   strictly safer, since not touching the view cannot get it wrong. The full drop
   order is recorded in 131's §3 header for whoever next changes those columns.
   The original trap still applies to any future migration that *does* alter a
   signature: drop the view first and rebuild it from the **live**
   `pg_get_viewdef`, never from an older migration file.
2. **Do not filter the session anchor CTEs.** See A5. This is the difference
   between "keep the time" and "fresh 5-hour block", and nothing in the type system
   will catch it.
3. **`paid_at`, not `now()`.** See A3.
4. **Accepted, not overlooked:** upgrade → reset → spend the fresh window →
   refund inside the 24-hour window. The money returns, the LLM spend does not.
   Migration 119's supersede logic protects *credit*, not consumed usage. Exposure
   is bounded by the plan cap (`max` = 250 points ≈ $2.50/cycle). Owner accepted
   this explicitly. If it is ever abused, the fix is to defer the reset until the
   refund window closes — at the cost of the instant gratification the upsell rests on.
5. **`PLAN_RANK` (Python) and `price_sar` (SQL) now both encode the ladder.**
   Adding a purchasable plan means touching both. See A2 for the invariant and the
   escape hatch.
6. **Deploy order** — see below. Getting it wrong un-caps free users.

## Verification

- `basic` user over weekly → dialog offers `pro` + `max`, not `basic`.
- `pro` user over weekly → offers `max` only.
- `max` user over weekly → banner only, no button, no dialog.
- `marketing_lawyer` over **session** (15) → offers `max` only, not `pro`.
- `marketing_lawyer` over **weekly** (74) → offers `pro` + `max`.
- Session *and* weekly both breached → message quotes the **weekly** reset.
- Upgrade `basic`→`pro` mid-session: `session_cost` and `weekly_cost` read 0
  immediately; the session still expires at its original boundary; `ocr_pages`
  is unchanged.
- Re-running the paid path (webhook + client confirm) writes the same
  `usage_reset_at` — no drift.
- A `free` user is unaffected by Part A (`price_sar IS NULL` → no reset).

## Deploy order

129 and 131 both invert if run in the wrong order relative to code.

1. **Ship the already-built free paywall + discard + Part B/C code.**
   Safe pre-migration: `monthly_cost` and `usage_reset_at` do not exist yet, so the
   monthly check reads 0 and never fires, and `upgrade_options` comes back empty.
2. **Apply migration 129.** Free users move to the 30-day window. *Three of ten
   current free users are over 5 points in the last 30 days and block immediately* —
   migrate at a time you are watching.
3. **Ship Part A + D code.**
4. **Apply migration 131.** Resets begin applying to upgrades from this point;
   `usage_reset_at` is NULL for everyone, so no existing usage is retroactively
   forgiven. 131 hard-requires 129 — it replaces 129's version of
   `get_user_usage_windows`, `monthly_cost`/`monthly_oldest` columns included.

Reversed at any step, the failure mode is quotas that do not enforce — not quotas
that over-enforce. That is the wrong direction to fail in, hence the ordering.
