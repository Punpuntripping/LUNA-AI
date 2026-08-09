# Subscription Cancellation (إلغاء الاشتراك) + Exit Survey

**Status:** BUILT — not deployed, migration 120 NOT applied.
**Origin:** 2026-08-08 discussion. Started as "downgrade calculated from the next
bill"; scope was deliberately cut to **cancellation only** once it became clear a
downgrade requires the Wave 2 auto-renewal engine (tokenization not yet enabled
on the Moyasar merchant account). Downgrade-at-renewal is DEFERRED to Wave 2 and
must reuse the flag this feature introduces.

---

## 1. What this is (decisions locked with the user, 2026-08-08)

A self-serve **cancel subscription** action for paid subscribers:

1. **Records renewal-cancellation intent** — `user_subscriptions.renewal_cancelled_at`.
   Wave 1 is one-time purchases, so **no real charge is stopped today**; the flag
   is declarative now and becomes load-bearing when the Wave 2 renewal job ships
   (the job MUST skip users where `renewal_cancelled_at IS NOT NULL`).
2. **Current term untouched** — access continues until `expires_at`, then the
   existing expired→free fallback takes over. Cancel ≠ refund; the 24h refund
   path (`POST /payments/{id}/refund`) is unrelated and unchanged.
3. **Shows the end date** — «تبقى باقتك فعّالة حتى ⟨date⟩ ثم تنتقل إلى الباقة المجانية».
4. **Exit survey at cancel time** — single-select reason (REQUIRED) + free-text
   comment (OPTIONAL). This survey data is the main product value today.
5. **Undo allowed** («تراجع عن الإلغاء») any time before `expires_at` — no money
   moves, so reversal is free.
6. **Visibility: `source='payment'` subscriptions ONLY.** Code/marketing/manual/
   signup grants never see the option (they expire on their own; a cancel button
   there is noise). Expired or free users don't see it either.

Survey options (user's wording, keep the spirit):

| key | Arabic label |
|---|---|
| `expensive` | السعر مرتفع |
| `no_longer_needed` | لم أعد بحاجة إلى التطبيق |
| `something_wrong` | عدم الرضا عن الخدمة |
| `other` | سبب آخر (with optional comment) |

Comment textarea is offered for every choice, optional always.

---

## 2. Schema — migration `120_subscription_cancellation.sql`

**⚠ Migration-before-deploy (the 119 lesson):** backend code writes a NEW column;
apply 120 to prod BEFORE deploying the backend, never the reverse.

1. `ALTER TABLE public.user_subscriptions ADD COLUMN renewal_cancelled_at timestamptz;`
   - NULL = renewal on (default). Set = user opted out at that moment.
   - Do NOT touch `plan_id`/`expires_at` when writing it — the
     `handle_subscription_assignment` trigger fires on `UPDATE OF plan_id` and
     must not be tickled (see the "set expiry ALONE" trigger trap).
2. `CREATE TABLE public.subscription_cancellations` — append-only survey ledger:
   - `id uuid PK default gen_random_uuid()`
   - `user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE` (survey data is
     not a financial record — unlike payment_transactions/117, cascade is fine
     and PDPL-friendlier)
   - `plan_id text NOT NULL` (what they were cancelling)
   - `reason text NOT NULL CHECK (reason IN ('expensive','no_longer_needed','something_wrong','other'))`
   - `comment text` (nullable)
   - `expires_at_snapshot timestamptz` (term end at cancel time — survives later grants)
   - `created_at timestamptz NOT NULL DEFAULT now()`
   - `revoked_at timestamptz` (stamped by undo; rows are never deleted)
   - **RLS enabled, zero policies; REVOKE ALL from anon/authenticated** (118
     lockdown pattern — service-role only; there is no user-facing read of this
     table).
3. Recreate `user_subscriptions_live` **exactly as 093 did (DROP + CREATE,
   security_invoker, operator-only grants)** adding `renewal_cancelled_at` so the
   operator glance shows who opted out.

---

## 3. Backend

Routes live in `backend/app/api/payments.py` (existing router, auth'd), logic in
`backend/app/services/payment_service.py` (or a small `subscription_service.py`
if payments.py is getting crowded — implementer's call, follow the
routes → service → Supabase pattern, errors in Arabic).

| Route | Behavior |
|---|---|
| `GET /api/v1/payments/subscription` | Current sub state for the settings dialog: `{plan_id, plan_name_ar, expires_at, source, cancellable, renewal_cancelled_at}`. `cancellable` = source=='payment' AND plan active (expires_at in the future). The existing `/usage` report does NOT expose `source` — that's why this endpoint exists; don't bolt `source` onto the quota report. |
| `POST /api/v1/payments/subscription/cancel` | Body `{reason, comment?}`. Guards: sub exists, `source=='payment'`, active, not already cancelled. Writes `renewal_cancelled_at=now()` + INSERT survey row (same service call; survey insert failure must NOT roll back the flag — log loudly instead). Returns the new state. Arabic errors, e.g. «لا يوجد اشتراك مدفوع فعّال لإلغائه». |
| `POST /api/v1/payments/subscription/reactivate` | Undo. Guards: flag set, term still active. Clears `renewal_cancelled_at`, stamps `revoked_at` on the newest un-revoked survey row. |

**Re-purchase clears the flag:** in the paid-fulfilment path of
`payment_service` (`_on_paid`, right after `grant_plan` succeeds), clear
`renewal_cancelled_at` — buying again IS re-opting in. Do this in **Python, not
inside `grant_plan`** (113 precedent: never touch the live money-path RPC for a
side concern). Cancel-then-buy-again leaves the survey row standing (revoked_at
stays NULL there — the survey recorded a true moment; only explicit undo revokes
it).

Idempotency: double-cancel → 409-style Arabic error, not a second survey row.

---

## 4. Frontend

- **`frontend/lib/api.ts`** — `SubscriptionState` type + `paymentsApi.getSubscription()`,
  `.cancelSubscription({reason, comment})`, `.reactivateSubscription()`.
- **`AccountSettingsDialog.tsx`** — new «الاشتراك» section (above the
  danger-zone / delete-account area), rendered only when `cancellable` or
  already-cancelled-but-active:
  - Active: plan name + «تنتهي في ⟨date⟩» + subdued «إلغاء الاشتراك» button.
  - Cancelled: «لن يُجدَّد اشتراكك — تبقى باقتك فعّالة حتى ⟨date⟩ ثم تنتقل إلى
    الباقة المجانية» + «تراجع عن الإلغاء» button.
- **Cancel flow** — AlertDialog (component already imported there): survey radio
  group (4 options above) + optional textarea; confirm button disabled until a
  reason is selected; on success show the cancelled state inline.
- **Copy honesty:** never write «سيتم إيقاف الدفع التلقائي» — there is no
  auto-charge in Wave 1 and /pricing already promises «بدون تجديد تلقائي». The
  copy above («لن يُجدَّد اشتراكك») stays true both today and after Wave 2.
- Dates in Arabic locale formatting, consistent with PaymentHistoryDialog.

---

## 5. Tests

`backend/tests/test_payments.py` additions (FakeSupabase — remember it cannot
catch column drift; the live smoke below is mandatory):
- cancel happy path: flag set, survey row written with snapshot fields
- cancel guards: no sub / free / expired / `source='code'|'manual'|'signup'` → Arabic refusal
- double-cancel → error, single survey row
- reactivate: flag cleared, newest survey row revoked
- paid fulfilment after cancel → flag cleared, survey row NOT revoked
- comment optional; bad reason value rejected

Frontend: `npx tsc --noEmit` + build. Live validation: one real cancel/undo on
prod (dev account) checking `user_subscriptions_live.renewal_cancelled_at` and
the survey row via SQL.

---

## 6. Traps & out-of-scope

- **Deploy order:** migration 120 → then backend deploy (new column write).
- **Trigger trap:** write `renewal_cancelled_at` ALONE — never in an UPDATE that
  also touches `plan_id`.
- **Wave 2 contract (forward reference):** the future renewal job charges only
  where `renewal_cancelled_at IS NULL`; the Wave 2 "Cancel-renewal UI" item in
  `.claude/plans/moyasar_payments.md` §5 is THIS feature — don't rebuild it.
- **Downgrade at next bill: OUT OF SCOPE** (user decision 2026-08-08). It becomes
  a renewal-intent mutation (`renewal_plan_id`) on top of the Wave 2 engine;
  basic will have no auto-charge initially.
- No emails on cancel (SMTP receipt path still unconfirmed); survey is the signal.
