# Subscription Auto-Renewal (التجديد التلقائي) — pro + max

**Status:** PLANNING. Phase 0 (copy) DONE 2026-08-11. Everything else NOT BUILT.
**Blocked on:** Moyasar merchant-side enablement for tokenized merchant-initiated
transactions (MIT). Nothing in Phases 2–6 can be validated until that is confirmed.
**Origin:** owner, 2026-08-11 — «I want the pro and the max plan to have auto renewal».
This is not a new direction: `/terms` §5.2 has promised it publicly since 2026-08-10.
What is new is the instruction to stop the point-of-sale copy contradicting it.

**Related:** [subscription_cancellation.md](subscription_cancellation.md) (the
`renewal_cancelled_at` flag this feature finally makes load-bearing),
[moyasar_payments.md](moyasar_payments.md), [financial_integration.md](financial_integration.md).

---

## 1. The model (locked)

| plan | term | renews? | stated where |
|---|---|---|---|
| `basic` | 7 days | **NO** — one-time, ends with no further charge | stated explicitly on the card: «بدون تجديد تلقائي · فترة الاشتراك ٧ أيام فقط» |
| `pro` | 30 days | **YES**, every 30 days | `/terms` §5.2 + `product_docs.pricing`. The card says nothing. |
| `max` | 30 days | **YES**, every 30 days | same |

Two rules that follow, and both are load-bearing:

1. **Only `source='payment'` subscriptions renew.** Code redemptions, marketing
   grants, manual grants and signup grants have no card behind them and must never
   be touched by the renewal job. This mirrors the visibility rule the cancel
   feature already uses (`subscription_service.py:74`).
2. **Renewal is opt-out, never opt-in-by-default-without-consent.** The card is
   charged only where `renewal_cancelled_at IS NULL` **and** a stored token exists
   **and** the user gave explicit recurring consent at purchase (Phase 3).

### Why the cards are silent instead of promising renewal

The owner's instruction was: strip «بدون تجديد تلقائي» from pro/max, keep it on
basic, say nothing elsewhere. That is exactly right for today, and the reasoning
should survive into whoever reads this next:

- «بدون تجديد تلقائي» on a pro card **contradicts the live terms**. A buyer who
  reads the card and then gets charged on day 31 has a chargeback and a complaint,
  and the card is the document they were looking at when they paid.
- «تجديد تلقائي» on a pro card would be **equally false in the other direction**
  until the engine exists — and worse, printing a recurring promise at the point
  of sale is precisely the disclosure that card schemes treat as the consent
  artefact. Printing it before it is true poisons the consent record.
- **Silence is the only sentence true in both worlds.** It is a stopgap, not the
  destination: Phase 6 replaces it with a real disclosure + consent checkbox, and
  that must land in the SAME deploy as the engine.

---

## 2. What exists today

| piece | state |
|---|---|
| `user_subscriptions.renewal_cancelled_at` | **EXISTS** (migration 120). Written by the deployed self-serve cancel + exit survey. **No job reads it.** |
| Cancel / undo UI + API | **DEPLOYED** — `AccountSettingsDialog` «إلغاء الاشتراك» / «تراجع عن الإلغاء», `subscription_service.cancel_renewal` / `reactivate_renewal`. |
| `clear_renewal_cancellation` on new purchase | **EXISTS** — `subscription_service.py:483`. A fresh paid grant clears a prior opt-out. |
| `plans.billing_cycle` | column exists; `one_time` for basic/pro/max, NULL for free/dev/marketing. **Nothing branches on it** — `payment_service.py:595` selects it and never reads it. |
| `credit_card: { save_card?: boolean }` | **TYPED but never passed** — `frontend/lib/moyasar.ts:90`; the `moyasar.init` call at `app/pay/[plan]/page.tsx:107` omits it. No card is ever tokenized. |
| Card token storage | **DOES NOT EXIST.** No table, no column. |
| Renewal job | **DOES NOT EXIST.** |
| Dunning / retry | **DOES NOT EXIST.** |
| Scheduler infrastructure | **EXISTS and is reusable** — `AsyncIOScheduler` in `backend/app/main.py:168`, daily `CronTrigger` jobs at 03:00 / 03:15 / …, single-worker backend so each job fires exactly once. |
| `/terms` §5.2, §5.3, §5.7 | **LIVE since 2026-08-10** saying pro/max auto-renew and renewal is stoppable self-serve. |
| `product_docs.terms` + `product_docs.pricing` | **Already say auto-renew** — verified in prod 2026-08-11. No change needed; the router quotes these. |

**Net:** the intent, the legal text, the opt-out flag and the cancel UI are all in
place. The money movement is the entire missing half.

---

## 3. Phase 0 — stop the contradiction (DONE 2026-08-11)

One string per plan, rendered on four surfaces (`/pricing`, the landing teaser
`PricingSection`, `QuotaUpgradeDialog`, and the `/pay/{plan}` page header):

| file | change |
|---|---|
| `frontend/lib/pricing.ts` pro `billingNote` | «بدون تجديد تلقائي · فترة الاشتراك ٣٠ يوماً» → «فترة الاشتراك ٣٠ يوماً» |
| `frontend/lib/pricing.ts` max `billingNote` | same |
| `frontend/lib/pricing.ts` basic `billingNote` | **UNCHANGED** — keeps «بدون تجديد تلقائي · فترة الاشتراك ٧ أيام فقط» |
| `frontend/lib/pricing.ts` header comment | rewrote the "ALL THREE plans are ONE-TIME / no card may promise تجديد تلقائي" block, which forbade this change |
| `frontend/types/index.ts` `SubscriptionState` doc | dropped the now-false «/pricing promises بدون تجديد تلقائي» citation |
| `frontend/components/Settings/AccountSettingsDialog.tsx` cancelled-note comment | same citation removed |

No other file in the repo asserts a no-renewal position to a user. (`/terms` and
both `product_docs` rows already say the opposite and are correct.)

**Not deployed yet.** Frontend-only, no migration.

---

## 4. Phase 1 — Moyasar enablement (BLOCKING, external)

Before any code: confirm with Moyasar, in writing, all four —

1. **Tokenization** is enabled on the live merchant account (`save_card` returning
   a reusable token, not just a display mask). Per
   [subscription_cancellation.md](subscription_cancellation.md), as of 2026-08-08
   tokenization was **not** enabled — that is what cut the downgrade feature.
2. **MIT (merchant-initiated transactions)** are permitted — charging a stored
   token with no cardholder present is a separate permission from tokenization.
3. **mada** behaviour for stored-credential recurring. mada is the dominant network
   here and has its own rules; if mada cannot do MIT, a large share of subscribers
   simply cannot be renewed and the whole feature needs a different shape
   (e.g. a pre-expiry «جدّد الآن» prompt instead).
4. **3DS on renewal** — whether a stored-credential charge is exempt, or whether a
   challenge can be thrown at a user who is not present. A renewal that needs 3DS
   and has nobody to answer it is a guaranteed decline, and the retry ladder in
   Phase 5 has to know that.

⚠ Answers 3 and 4 can change the design, not just the config. Do not start Phase 3
before they are in hand.

---

## 5. Phase 2 — schema (migration `132_subscription_auto_renewal.sql`)

131 is the last used number (`131_usage_reset_on_upgrade.sql`).

1. **`public.payment_methods`** — the stored-credential record. Token only; never
   a PAN, never a CVV, never an expiry the user typed.
   - `payment_method_id uuid PK default gen_random_uuid()`
   - `user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE`
   - `provider text NOT NULL DEFAULT 'moyasar'`
   - `provider_token text NOT NULL` — the Moyasar token
   - `brand text`, `last4 text`, `exp_month int`, `exp_year int` — display only,
     returned by the provider; enough to render «مدى ••1234» in settings
   - `consent_given_at timestamptz NOT NULL` — when the user ticked the recurring
     disclosure. **This is the consent artefact**; a token with no consent row is
     not chargeable.
   - `consent_text_hash text NOT NULL` — sha256 of the exact Arabic disclosure
     shown. When the wording changes, you can still prove what a given user agreed
     to. Cheap now, impossible to reconstruct later.
   - `revoked_at timestamptz`, `created_at`, `updated_at`
   - partial unique index: one active method per user
     (`WHERE revoked_at IS NULL`)
   - **RLS enabled, zero policies, `REVOKE ALL` from anon + authenticated** — the
     118 lockdown pattern. Reads go through the backend, which returns only
     brand/last4. There is no user-facing SELECT of `provider_token`, ever.
2. **`payment_transactions`** — mark renewals apart from purchases:
   - `initiated_by text NOT NULL DEFAULT 'user' CHECK (initiated_by IN ('user','renewal'))`
   - `renewal_attempt int NOT NULL DEFAULT 0` — 0 = first try, 1..n = dunning retries
   - `payment_method_id uuid REFERENCES payment_methods` (nullable; NULL for
     browser-form purchases)
   - ⚠ **Do NOT `ON DELETE CASCADE` from payment_methods** — 117 established that
     payment rows are financial records that outlive everything else.
3. **`user_subscriptions`**:
   - `renewal_attempt_at timestamptz` — last attempt, for dunning-state derivation
   - `renewal_failed_count int NOT NULL DEFAULT 0`
   - ⚠ writing these must **not** touch `plan_id` — `handle_subscription_assignment`
     triggers on `UPDATE OF plan_id`. Same "set expiry ALONE" trap that bit 120.
4. **`plans.billing_cycle`** → `'recurring_30d'` for `pro` and `max`; `basic` stays
   `one_time`. Add the branch that reads it — a column nothing reads is how this
   drifted in the first place.
5. Recreate `user_subscriptions_live` per the 093 pattern (DROP + CREATE,
   `security_invoker`, operator-only grants) if any new column must surface there.

**⚠ Migration-before-deploy** (the 119/Moyasar lesson): apply 132 to prod BEFORE
deploying the backend that writes these columns. Never the reverse.

---

## 6. Phase 3 — tokenize at first purchase

`frontend/app/pay/[plan]/page.tsx`:

- Pass `credit_card: { save_card: true }` to `moyasar.init` — **only** for `pro`
  and `max`, and **only** when the consent checkbox (Phase 6) is ticked. `basic`
  never tokenizes; it does not renew, so storing its card is collecting a
  credential with no purpose, which PDPL does not love.
- The token arrives on the payment object. Persist it server-side in the existing
  `/verify` + webhook paths — **both**, idempotently, exactly like the grant. The
  callback path alone is not sufficient (3DS destroys the page; that is why
  `on_completed` exists at all).
- Store `consent_given_at` + `consent_text_hash` in the same write. A token row
  without them must be treated as unusable by the renewal job.

---

## 7. Phase 4 — the renewal job

New `backend/app/services/renewal_service.py`, registered as an APScheduler
`CronTrigger` job in `main.py` alongside the existing sweeps (suggest **03:30 UTC**,
15 min after the upload reconciler, same rationale: don't fight for the postgrest
pool).

Selection — every condition is a hard gate:

```
plan_id IN ('pro','max')
AND source = 'payment'
AND renewal_cancelled_at IS NULL
AND expires_at BETWEEN now() AND now() + interval '24 hours'
AND an active payment_methods row exists (revoked_at IS NULL, consent_given_at NOT NULL)
AND no payment_transactions row already exists for this user + this period
```

Per user:

1. Insert `payment_transactions` (`status='initiated'`, `initiated_by='renewal'`,
   amount from `plans.price_sar` — **never** a cached or client-supplied figure)
   **before** calling the provider. Crash-safe ordering, same as the user message
   rule.
2. Charge the token via Moyasar.
3. On success: extend `expires_at` by `duration_days` **from the old `expires_at`,
   not from `now()`** — renewing at 03:30 must not shave hours off the term every
   cycle. Stamp `paid_at`, `fulfilled_at`, `receipt_no`.
4. On failure: hand to Phase 5.

**Idempotency is the whole job.** Two ticks, a retry, or a redeploy mid-run must not
double-charge. Enforce it in the DB, not in Python: a partial unique index on
`(user_id, plan_id, period_start)` for `initiated_by='renewal'`, so the second
insert fails loudly instead of the second charge succeeding quietly.

⚠ `_expire_open_checkouts` (`payment_service.py:667`) supersedes a user's open
`initiated` rows on every new checkout. A renewal row is `initiated` too — it MUST
be excluded from that sweep, or a user who happens to open `/pay` during a renewal
window silently kills their own renewal row.

---

## 8. Phase 5 — dunning

A declined card must not silently end a subscription the user believes is running.

- Retry ladder: day 0, +1, +3 (then stop). Each attempt is its own
  `payment_transactions` row with an incremented `renewal_attempt`.
- Email at first failure — «تعذّر تجديد اشتراكك» + a link to update the card.
- After the last failure: let the term lapse into the existing expired→free
  fallback (`reference_quota_expiry_free_fallback`). No new mechanism.
- Needs a **card-update surface** in settings: replace the stored method. Without
  it, dunning emails point nowhere and the ladder is theatre.

⚠ **Receipts are currently blocked** (the 465/SSL issue, parked). Renewal roughly
triples receipt volume and a silent recurring charge with no receipt is the
complaint that turns into a chargeback. Unblock receipts before, or with, this.

---

## 9. Phase 6 — consent + copy (ships WITH the engine, not before)

- Pre-purchase disclosure on `/pay/{pro,max}`: the amount, the cadence, the next
  charge date, and how to stop — with a checkbox the user must tick. Store its hash
  (Phase 2). This is both the KSA e-commerce disclosure and the card-scheme
  stored-credential consent; one artefact serves both.
- Only then flip `pricing.ts` pro/max `billingNote` from silence to an explicit
  renewal line, and delete the ⚠ block in that file's header.
- `basic` keeps «بدون تجديد تلقائي» permanently.
- Pre-renewal notice email (~3 days out). `/terms` does not promise one; send it
  anyway — it is the cheapest chargeback insurance there is.

---

## 10. Interactions to get right

| existing behaviour | what renewal does to it |
|---|---|
| **Refund (24h)** | The window now applies **per renewal charge**, not just the first purchase. `revoke_plan_grant` (113) must handle "refund the renewal, restore the prior term". |
| **Cancel** | Already correct — `renewal_cancelled_at` becomes load-bearing on the day the job ships. The «لن يُجدَّد» copy was written to be true both before and after. |
| **Undo cancel** | Already correct. |
| **Upgrade credit** | `create_checkout` prorates an upgrade against remaining days. A user who upgrades mid-term must end with **one** active method and **one** renewal schedule, at the new plan. |
| **Re-purchase stacking** | Buying the same plan early extends the term. The renewal job keys on `expires_at`, so stacking just pushes it — verify with a test. |
| **Delete account** | The purge path MUST revoke the token at Moyasar, not merely delete the row. A live token on a deleted account is the worst version of this bug. |
| **Plan codes / marketing / manual grants** | Never renew — excluded by `source='payment'`. |

---

## 11. Traps

1. **The silent card is a stopgap with a deadline.** Right now `/terms` says pro
   renews and the engine does not renew. A pro buyer today is told their plan
   continues and it will stop at day 30. Phase 0 stopped the *contradiction*; it did
   not fix the *under-delivery*. This is the reason to ship, and the reason not to
   let this plan sit.
2. **Never charge from a client-supplied amount.** The renewal job reads
   `plans.price_sar` like `create_checkout` does. Same rule, new caller.
3. **Term arithmetic from `expires_at`, not `now()`.** See Phase 4 step 3.
4. **`UPDATE OF plan_id` trigger.** Write renewal bookkeeping columns alone.
5. **Single-worker assumption.** The existing sweeps rely on the backend running one
   worker. If the service is ever scaled out, every job in `main.py` double-fires —
   and this one moves money. Add the DB-level idempotency guard regardless.
6. **`plans.billing_cycle` must be read, not just written.** It has been a decorative
   column since 076.
7. **Test with a real card in the live account**, as the Moyasar Wave 1 smoke did
   (RYH-000007). A renewal path validated only against the test key is not validated.

---

## 12. Open questions for the owner

1. **Grandfathering** — the existing pro/max subscribers bought under a card that
   said «بدون تجديد تلقائي». They gave no recurring consent and have no stored
   token, so they cannot be renewed regardless. Confirm: they finish their term and
   are invited to re-subscribe under the new terms?
2. **Pre-renewal notice** — send one? (Recommended; not promised by `/terms`.)
3. **Retry ladder** — 0/+1/+3 then lapse, or a longer tail?
4. **Failed renewal grace** — keep access during the retry window, or cut at
   `expires_at`? (Recommended: keep access through the ladder; a paying customer
   whose bank declined once should not lose their case files mid-week.)
