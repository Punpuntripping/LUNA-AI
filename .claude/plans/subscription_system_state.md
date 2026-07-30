# Subscription & Quota System — Current State

**As of 2026-07-15.** Snapshot of how subscriptions, limits, usage, and (future)
payments actually work in prod. Companion doc: `.claude/plans/financial_integration.md`
(payment-provider contract). Supersedes the scattered notes in migrations 068–093.

---

## 1. The model in one paragraph

A user has **one subscription row** naming a **plan**. The plan carries the
**limits**. Actual **usage** is never stored — it is summed on demand from the
`llm_calls` ledger (every LLM call writes a row with its USD cost). Limits are
denominated in **points: 1 USD = 100 points**. If a subscription's `expires_at`
has passed, the user silently falls back to the **free** plan's limits while
keeping their assigned `plan_id` on the row. One RPC —
`get_user_quota_state` — assembles identity + effective limits + live usage and
is the **single source** the gate, the UI, and operators all read.

## 2. Sources of truth

| Question | Answer lives in | Notes |
|---|---|---|
| Which plan, until when, any per-user overrides? | `user_subscriptions` | One row per user (`UNIQUE(user_id)`). `plan_id NULL` = locked. |
| What are that plan's limits? | `plans` catalog | Tiny, hand-edited. Also holds `price_sar` + `billing_cycle`. |
| How much has the user consumed? | `llm_calls` ledger | Derived at read time. **Never materialized** — stored counters drift (that was the pre-079 Redis bug). |
| Money paid / owed | `payment_transactions` | Built, not yet fed (no provider wired). |

There is **no** other source. Redis is off the quota path entirely (`settle_*`
in `shared/quota` are no-op shims). The legacy `users` plan columns were
dropped in migration 080.

## 3. Windows (what actually gets enforced)

| Meter | Window | Unit | Enforced? |
|---|---|---|---|
| `ord` session | **Fixed 5h block**, anchored at the user's first message; tumbles forward in 5h tiles (migration 083) | points | Yes |
| `ord` weekly | Rolling last 7 days | points | Yes |
| `ocr` | Rolling last 30 days | pages | Yes (projected: `current + estimated > limit` blocks *before* the OCR runs) |
| `ord` monthly | — | — | **Retired at 079.** Column still exists on `plans`; not enforced, reported as `null`. |
| `web` | — | — | **Never shipped.** All plans set 0; the gate branch exists but no caller passes `needs_web=True`. |

`resets_at` = oldest-call-in-window (or session anchor) + window length. When a
window has zero usage the API sends `resets_at: null` → the UI says "fully
available" rather than showing a bogus countdown against a possibly-skewed
client clock.

Limit semantics: **`NULL` = unlimited** (window skipped entirely) · **`0` =
feature not in the plan** · a per-user `*_override` beats the plan value.

## 4. Plan catalog (live values)

| plan_id | name_ar | SAR | billing | days | session | weekly | OCR/30d |
|---|---|---|---|---|---|---|---|
| `free` | المجانية | — | — | ∞ | 5 | 5 | 0 |
| `basic` | الأساسية | 49 | one_time | 7 | 10 | 50 | 15 |
| `pro` | الاحترافية | 89 | recurring_monthly | 30 | 15 | 75 | 40 |
| `max` | القصوى | 189 | recurring_monthly | 30 | 50 | 250 | 200 |
| `marketing_lawyer` | ترويجي | — | — | 7 | 15 | 76 | 20 |
| `dev` | حساب مطوّر | — | — | 60 | ∞ | ∞ | ∞ |

`price_sar` is the **server-authoritative** checkout amount (092). Checkout must
read it from here — never accept an amount from the client.
`frontend/lib/pricing.ts` is *display copy only*; keep the two in sync by hand.

## 5. Read paths — all three go through one RPC

```
get_user_quota_state(user_id)          ← THE source (migration 093)
  ├─ enforcement gate    shared/quota check()          → PlanInactive | QuotaExceeded | QuotaUnavailable
  ├─ حدود الاستخدام      GET /api/v1/usage             → current_usage_report() → UsageLimitsDialog
  └─ operator glance     user_subscriptions_live view  → identity + status + used-vs-limit
```

The RPC returns, in one row: plan identity, `is_expired`, **effective** limits
(expiry fallback + overrides already resolved **in SQL** — one definition, no
Python copy), and the three usage windows. Because all three consumers read the
same row, **what the dialog shows is exactly what the gate enforces** — the old
"blocked on an invisible window" class of bug is structurally impossible now.

The gate fires **once per message**, before OCR and before the router, from
`backend/app/services/message_service.py`. It **fails closed**: if the RPC is
unreachable it raises `QuotaUnavailable` rather than letting unmetered spend
through.

## 6. Write paths — how a subscription changes

| Trigger | Mechanism | Result |
|---|---|---|
| Signup | `handle_new_user` trigger | Seeds a `free` row (in an EXCEPTION sub-block, so a failure here can never break signup). |
| User redeems a code | `redeem_plan_code(code, user_id)` RPC | Validates shelf-life → dedup → capacity, consumes one use slot, upserts the subscription. Has a **downgrade guard**: refuses if an active paid plan is already in place. |
| Operator grant / renewal | **`grant_plan(user, plan, source, payment_id)`** | The preferred entry point. |
| Payment (future) | Provider webhook → `grant_plan(..., payment_id)` | Not wired — no provider chosen. |

**`grant_plan` is the one to use for manual work:**

```sql
select * from grant_plan('<user_id>', 'max', 'manual');
```

It handles the semantics you'd otherwise get wrong by hand:
- **Expired or different plan** → fresh window from `now()`.
- **Same plan, still active** → **stacks** (`expires_at + duration`), so paid-for
  days are never destroyed by an early renewal.
- **Payment-backed** → refuses unless the payment row is `status='paid'` and
  matches (user, plan); stamps `fulfilled_at` so a **webhook retry is a no-op**
  (no double-extension).

> **Trap (why you should not hand-write UPDATEs):** the
> `handle_subscription_assignment` BEFORE-UPDATE trigger recomputes
> `expires_at = now() + plans.duration_days` whenever `plan_id` *changes* —
> silently overriding any expiry you set in the same statement. To set a custom
> expiry, update `expires_at` **alone**.

## 7. Schema inventory

**`user_subscriptions`** — `subscription_id`, `user_id` (unique), `plan_id`
(FK, NULL = locked), `source` (signup|manual|code|payment), `started_at`,
`expires_at`, `redeemed_code`, 5 × `*_override`, timestamps.
RLS: self-SELECT only; all writes service-role.
*No `status` column* — it was dropped in 091 (see §9).

**`plans`** — catalog above + `duration_days`, `price_sar`, `billing_cycle`.

**`payment_transactions`** (092, built, unfed) — append-only money ledger.
`initiated → paid | failed | refunded`, `amount_sar` copied from the catalog at
initiation, `provider` + `provider_ref` with a `UNIQUE` index (webhook
idempotency at the DB level), `raw_payload` jsonb for audit, `fulfilled_at`
(grant anchor). RLS self-SELECT; writes service-role.

**`plan_codes`** (081) — N-use redemption codes. `gen_plan_codes.py --max-uses`.

**`user_subscriptions_live`** (view) — the operator surface: identity + derived
status + used-vs-limit. Query this, **not the base table**.

**Functions:** `get_user_quota_state` (the source) · `get_user_usage_windows`
(ledger windows; called by the former) · `grant_plan` · `redeem_plan_code` ·
`handle_new_user` · `handle_subscription_assignment`.
All quota/grant RPCs are **service-role only** — `EXECUTE` was revoked from
`anon`/`authenticated` in 093, closing a pre-existing IDOR where any logged-in
user could read *any* other user's usage and cost through PostgREST.

## 8. Deployment state

| Piece | Prod DB | Backend code |
|---|---|---|
| 091 truthful status | ✅ applied | n/a (nothing read `status`) |
| 092 payments layer | ✅ applied | n/a (no code path yet) |
| 093 unified quota RPC | ✅ applied | ⚠️ **`shared/quota/__init__.py` rewrite is local + uncommitted** |

The deployed backend still runs the **old two-read path** (`user_subscriptions`
+ usage RPC, limits resolved in Python). It keeps working correctly — the new
RPC is additive. Deploying the rewrite switches enforcement to the single-source
path. **Nothing is broken until then; nothing is unified in the running app
either.**

## 9. History — why it looks like this

- **Pre-079:** three disagreeing sources (users columns + Redis accumulators +
  the ledger). Users got blocked on windows the dialog didn't show.
- **079/080:** identity moved to `user_subscriptions`; usage moved to rolling
  reads off `llm_calls`; monthly enforcement dropped; legacy `users` columns
  dropped.
- **083:** session redefined from rolling-5h to a **fixed 5h block** anchored at
  the first message (the help copy had been claiming this all along).
- **091:** `status` column **dropped**. It was stamped `'active'` at assignment
  and never updated, so an expired subscription still read `active/max` in the
  DB while the gate had already fallen the user back to free — the "why does
  Anwar's row say max but he can't send anything?" confusion. Status is now
  derived at read time.
- **092:** money layer + `grant_plan` as the single grant/renew entry point.
- **093:** effective-limit resolution moved out of Python into SQL; one RPC now
  feeds gate + dialog + operator view.

## 10. Known gaps / open decisions

- **Deploy 093's Python rewrite** (above) — the only thing standing between the
  current app and the unified path.
- **No payment provider chosen.** Moyasar / Tap / HyperPay (Saudi) vs Stripe.
  The DB contract is provider-agnostic and ready; see `financial_integration.md`.
- **No self-serve upgrade.** `/pricing` CTA is an intentional dead-end; every
  paid grant today is a manual `grant_plan` call or a redeemed code.
- **VAT retention vs account deletion.** `payment_transactions.user_id` is
  `ON DELETE CASCADE` (keeps the 090 delete-account flow working), but Saudi VAT
  rules likely require retaining financial records ~6 years. **Must be resolved
  before taking real money.**
- **Refund policy undefined.** `status='refunded'` exists; whether a refund also
  revokes the granted term is unanswered (manual expiry edit for now).
- **No expiry notification.** Users discover expiry by getting blocked. Several
  accounts have been sitting silently expired on the free fallback.
- **Dead leftovers** (harmless, cleanup pending a decision):
  `users.ord_cost_daily_limit_usd` / `ord_cost_weekly_limit_usd` (pre-points
  era, nothing reads them, all 23 users at default) · `plans.points_monthly` +
  `web_calls_monthly` and their override columns (retired windows) ·
  `subscription_tier` field still declared in `backend/app/models/responses.py`
  and `frontend/types/index.ts` though the DB column is long gone.
- **`redeem_plan_code` still self-upserts** rather than delegating its grant step
  to `grant_plan` — works fine, but it's a second write path to keep in sync.

## 11. Operator runbook

```sql
-- Look at anyone's real state (identity + status + used vs limit)
select * from user_subscriptions_live where email = '<email>';

-- Grant / renew a plan  (handles stacking + the trigger trap)
select * from grant_plan('<user_id>', 'pro', 'manual');

-- Lock an account
update user_subscriptions set plan_id = null where user_id = '<user_id>';

-- Custom expiry — MUST be its own statement, or the trigger overwrites it
update user_subscriptions set expires_at = '2026-12-31' where user_id = '<user_id>';

-- Lift a limit for one user without changing their plan
update user_subscriptions set points_weekly_override = 500 where user_id = '<user_id>';
```

Debugging "لماذا أنا محظور؟": query the view. If `status = 'expired'`, the user
is on the **free** fallback (5/5 points, 0 OCR pages) regardless of what
`plan_id` says — that is working as designed, not a bug.
