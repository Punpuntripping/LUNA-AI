# Financial Integration (Payments) — Contract & Remaining Work

**Status:** DB layer BUILT + applied to prod (migrations 091, 092, 093 — 2026-07-15).
Provider integration NOT started (no provider chosen yet).
The 093 Python rewrite of `shared/quota/__init__.py` is local/uncommitted —
deploy required to switch the backend to the single-RPC path (old path keeps
working meanwhile).

## What exists today (applied to prod)

### Migration 091 — truthful subscription state
- `user_subscriptions.status` column **dropped** (it was stamped `'active'` on
  assignment and never updated — expired subs read as active).
- `user_subscriptions_live` **view** derives `status`
  (`locked`/`expired`/`active`), `is_expired`, `effective_plan_id` (expired →
  `free`, the gate's fallback), plus both plan names — same logic as
  `shared/quota/__init__.py _user_limits`, so a DB glance always matches
  enforcement. **Operators glance at the view, not the base table.**

### Migration 092 — financial-ready layer (provider-agnostic)
- `plans.price_sar` + `plans.billing_cycle` — **server-authoritative prices**:
  basic 49/`one_time`, pro 89/`recurring_monthly`, max 189/`recurring_monthly`;
  free/dev/marketing NULL = not purchasable. `frontend/lib/pricing.ts` remains
  display copy — keep in sync by hand.
- `payment_transactions` — append-only money ledger. One row per checkout
  attempt: `initiated → paid | failed | refunded`. `UNIQUE(provider,
  provider_ref)` = webhook idempotency at the DB level. `raw_payload` stores
  the provider event for audit. RLS: self SELECT only; all writes service-role.
- `grant_plan(p_user_id, p_plan_id, p_source, p_payment_id)` RPC — the **single
  grant/renew entry point**:
  - payment-backed grants require the row to be `status='paid'` and match
    (user, plan); `fulfilled_at` stamps the grant so **webhook retries are
    no-ops** (verified: no double-extension).
  - early renewal of the same still-active plan **stacks** (`expires_at + duration`);
    plan change or expired sub gets a fresh window from `now()`.
  - `EXECUTE` **revoked** from anon/authenticated (no internal auth guard —
    service role + SQL operators only).
  - The `handle_subscription_assignment` trigger recomputes expiry only when
    `plan_id` changes — which equals grant_plan's fresh-window value, so the
    two agree by construction. Same-plan stacks pass through untouched.

### Migration 093 — unified quota state (ONE source)
- `get_user_quota_state(user_id)` RPC: plan identity + **effective** limits
  (expired→free fallback + overrides, resolved in SQL — the one definition) +
  live usage windows, in a single row. The gate (`shared/quota check`), the
  حدود الاستخدام dialog (`current_usage_report`), and the operator view all
  read this.
- `user_subscriptions_live` recreated with used-vs-limit points columns.
- Quota RPCs locked to service_role (closed a pre-existing cross-user usage
  IDOR on `get_user_usage_windows` via PostgREST); view is operator-only.
- Usage remains **derived** from `llm_calls` — never materialized (stored
  counters were the pre-079 drift bug).

All verified E2E on prod 2026-07-15: unpaid-payment guard, paid grant,
retry idempotency, stacking, cleanup; quota gate + report re-verified through
the new RPC (expired, healthy, and locked users).

## Integration contract (build when provider is chosen)

Candidate providers (Saudi market): Moyasar, Tap, HyperPay; Stripe if global.

### 1. Checkout endpoint — `POST /api/v1/payments/checkout`
1. Auth user; body = `{plan_id}` ONLY — **never accept an amount from the client**.
2. Read `plans.price_sar` (reject NULL = not purchasable).
3. Insert `payment_transactions` row (`initiated`, amount from DB, `provider`).
4. Create the provider checkout session (amount, currency SAR, metadata =
   `payment_id`), store `provider_ref`, return the redirect/checkout URL.

### 2. Webhook endpoint — `POST /api/v1/payments/webhook/{provider}`
1. **Verify signature** (provider secret from env) before touching the DB.
2. Locate the row by `(provider, provider_ref)` — the unique index guarantees
   one match; unknown ref → 200 + log (don't 500 a replay).
3. On success event: set `status='paid'`, `paid_at`, `raw_payload`; then call
   `grant_plan(user_id, plan_id, 'payment', payment_id)`. Retries are safe —
   grant_plan no-ops on `fulfilled_at`.
4. On failure event: `status='failed'` + `raw_payload`.
5. Always answer 200 quickly; do heavy work outside the handler if needed.

### 3. Auto-renewal (pro/max, `billing_cycle='recurring_monthly'`)
Provider-side recurring billing. Each cycle fires the same webhook → new
`payment_transactions` row → same `grant_plan` call → same-plan stack extends
the term. No cron needed on our side.

### 4. Refunds
Set `status='refunded'` on the row. Policy decision still open: whether a
refund also shortens/revokes the granted term (manual `UPDATE
user_subscriptions SET expires_at=...` for now).

### Open questions / traps
- **Retention vs deletion:** `payment_transactions.user_id` is `ON DELETE
  CASCADE` to keep the delete-account flow (090) working. Saudi VAT rules may
  require retaining financial records (~6y) — revisit before launch: either
  export before delete, or switch to anonymized retention.
- `redeem_plan_code` still does its own upsert (not via grant_plan) — works,
  but a future refactor could delegate its grant step for one code path.
- Frontend: /pricing CTA is an intentional dead-end today; wire it to the
  checkout endpoint when built. Consider reading prices from the DB then.
