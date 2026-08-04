# Moyasar Payments — Wave 1: Self-Serve One-Time Checkout

**Status (2026-08-04): DEPLOYED to prod + E2E-VALIDATED with test keys.**
Full purchase (mada test card → 3DS → callback → grant) and self-serve refund
(47.90/2.00, prior-plan RESTORE) verified against payment_transactions,
user_subscriptions_live, and Moyasar's own ledger. Three prod bugs found+fixed
during Phase F (commits a7de51f, a421565, bea6271 — see §6 traps 15–17).
**Remaining before real money:** swap test→live keys on Railway (until then
real cards decline at the form), register the webhook + secret (webhook path
UNVALIDATED), Phase E legal pages (/terms refund clause, /privacy PDPL
sub-processor disclosure, /learn/data-protection naming Moyasar), and the §7
go-live items (ZATCA, VAT registration, retention). Moyasar's observed per-txn
fee in sandbox: 1.73 SAR on a 49.90 charge (§8-11b data point).
Reports: `agents_reports/migration_113_report.md`, `payments_backend_report.md`,
`payments_frontend_report.md`.
Companions: `.claude/plans/financial_integration.md` (the DB contract — still
accurate except §3, see below) and `.claude/plans/subscription_system_state.md`
(how quota/plans work today).

---

## 0. Decisions locked (2026-08-03)

| Decision | Value | Consequence |
|---|---|---|
| Provider | Moyasar | No subscription engine — see §1 |
| Checkout surface | **Embedded `moyasar.js` form** on `rayhanai.com` | CSP work; the documented path to `save_card` for Wave 2 |
| Billing model, Wave 1 | **All three plans are one-time purchases** with a term | `/pricing` copy must stop promising «تجديد تلقائي شهري» |
| Auto-renewal | **Wave 2**, via saved tokens + our own scheduler | Not in this plan's scope beyond not painting us into a corner |
| Card storage | **Tokenization approved** (2026-08-03) — cards stored **at Moyasar**, never at Rayhan | Wave 2 confirmed; needs consent copy, cancel-renewal flow, and processor disclosure |
| Trust claim | «جميع العمليات المالية تتم عبر مُيسّر» — **not** «لا نحفظ بطاقتك» | See Phase E for the exact defensible wording and what it may not say |
| Prices | **49.90 / 89.90 / 189.90**, VAT-inclusive (15%) | Repriced 2026-08-03 (x.00 → x.99 → **x.90** final). Charge amount == `plans.price_sar`; both the DB and `lib/pricing.ts` change together |
| Refunds | **Refundable within 24 hours of purchase**, minus a **3 SAR processing fee** (2→3, owner 2026-08-04 — covers Moyasar's non-returned ~1.73+1 SAR txn fee) | A refund must **revoke the granted term** — this settles the open question in `financial_integration.md` §4. The fee makes every refund a **partial** refund → credit-note VAT, see Phase B |
| Payment methods | **Cards (mada/Visa/MC) + Apple Pay. No STC Pay** | Decision 2026-08-03; `methods: ['creditcard','applepay']` — re-adding STC Pay later is one line |
| Upgrades | **Prorated charge** — `charge = new_price − (remaining_days/duration × old_price)` | Decision 2026-08-03 (settles §8.B.6). Credit only when `source='payment'`; refunding an upgrade restores the prior plan. Same-plan re-purchase stacks; downgrades blocked |

---

## 1. The finding that changes the contract

**Moyasar has no subscription engine.** No plans, no schedules, no dunning. Their
SaaS solutions page: *"Securely save customer payment tokens at first checkout and
charge on a flexible schedule."* The merchant decides **when** to charge; Moyasar
handles **how**.

So this clause in `financial_integration.md:79-82` is **wrong for Moyasar**:

> Provider-side recurring billing. Each cycle fires the same webhook → … **No cron
> needed on our side.**

Auto-renewal is *our* cron, *our* retry/dunning, *our* cancel flow. That is why
Wave 1 ships one-time purchases: it gets real money flowing without building a
billing engine, and Wave 2 adds tokens on top rather than rewriting.

**Everything else in the DB contract holds unchanged.** `payment_transactions` +
`grant_plan()` map onto Moyasar cleanly:

| Our column | Moyasar |
|---|---|
| `provider` | `'moyasar'` |
| `provider_ref` | payment `id` (uuid) — `UNIQUE(provider, provider_ref)` = webhook idempotency |
| `raw_payload` | the webhook/API payment object |
| `fulfilled_at` | makes their **5 webhook retries** no-ops |
| same-plan stacking in `grant_plan` | an early re-purchase extends the term instead of destroying paid days |

---

## 2. Moyasar facts this plan is built on

| Piece | Detail |
|---|---|
| API | `https://api.moyasar.com/v1`, HTTP Basic auth (secret key as username, blank password) |
| Keys | `sk_test_`/`sk_live_` (backend only) · `pk_test_`/`pk_live_` (browser) — test keys ARE the sandbox, same host |
| Amounts | **Halalas** (smallest unit), min 100. `49.90 SAR → 4990` |
| Idempotency | `given_id` (UUIDv4) on create-payment; replay returns the original response, reuse for a *different* payment → 400 |
| Metadata | Free string key/value, echoed in webhooks + searchable — carries our `payment_id` |
| Form assets | `https://cdn.moyasar.com/mpf/1.19.0/moyasar.{js,css}` — **version pinned in the path, no `latest` alias** |
| Form config | `element`, `amount`, `currency`, `description`, `publishable_api_key`, `callback_url`, `metadata`, `methods`, `supported_networks`, `language: 'ar'`, `on_completed`, `on_failure`, `credit_card.save_card` |
| 3DS | **Full-page redirect** (`window.location` → `source.transaction_url`) — no iframe, no `postMessage`, so no `frame-src` needed |
| Return flow | Browser redirects to `callback_url?id=<payment_uuid>` after payment/3DS |
| Webhook events | `payment_paid`, `payment_failed`, `payment_refunded`, `payment_voided`, `payment_authorized`, `payment_captured`, `payment_abandoned`, `payment_verified` |
| Webhook retries | 5 more attempts over ~2h on non-2xx, **then dropped** |
| Webhook auth | ⚠️ **`secret_token` in the JSON body — NOT an HMAC signature header** |
| Refund | `POST /v1/payments/:id/refund`, optional partial `amount`; fires `payment_refunded` |
| Apple Pay (web) | **No Apple Developer account** — Moyasar Web Merchant Registration: dashboard domain registration + association file at `/.well-known/` + `GET /v1/applepay/initiate` session. Apple account is a *mobile-SDK* requirement only |
| Tokens (Wave 2) | `credit_card.save_card: true` → `source.token`; charge later with `source: {type:'token', token:…}`; **3DS not triggered by default** on token charges |
| Fees | ~1.5% + 1 SAR mada, ~2.5% + 1 SAR cards *(third-party figures — **confirm against the signed merchant contract**)* |

---

## 3. Architecture

```
/pricing  ──CTA──►  /pay/[plan]                     (authed page)
                        │  POST /api/v1/payments/checkout {plan_id}
                        │  ← {payment_id, amount_halalas, description, publishable_key}
                        ▼
                   moyasar.js form  ──cards/ApplePay/mada──►  Moyasar
                        │                                        │
             redirect  ?id=<moyasar_id>                    webhook POST
                        ▼                                        ▼
                   /pay/callback  ──POST /verify──►  backend  ◄──/webhook/moyasar
                                                        │
                                          GET /v1/payments/{id}  (re-fetch = the truth)
                                                        │
                                       payment_transactions → 'paid'
                                                        │
                                            grant_plan(user, plan, 'payment', payment_id)
```

**Two independent confirmation paths, both idempotent.** Neither alone is
sufficient: the redirect can be lost (user closes the tab), and the webhook is
dropped after 5 failed retries. Whichever arrives first grants; the second
no-ops on `fulfilled_at`.

**The webhook body is a trigger, not evidence.** Because Moyasar authenticates
with a shared token in the body rather than an HMAC over it, both paths
**re-fetch `GET /v1/payments/{id}` with our secret key** and trust only that
response — status, amount, currency, and metadata are all verified against our
own row before any grant. A forged webhook body then buys nothing.

---

## 4. Build

### Phase 0 — Test environment (do this first; parts need Mohammed)

**There is no separate sandbox host.** `https://api.moyasar.com/v1` serves both
modes — **the key prefix decides**. `pk_test_`/`sk_test_` = sandbox; test mode
"doesn't affect your live data or interact with the banking networks." So
"switching to production" is an env-var change, nothing more. That is convenient
and also the risk: a stray live key in a dev `.env` charges real cards.

**Guard rail:** on startup, assert that `MOYASAR_SECRET_KEY` and
`MOYASAR_PUBLISHABLE_KEY` share the same mode, and refuse to boot if
`ENVIRONMENT != production` while either is `_live_`. Cheap, and it makes the
class of accident impossible.

#### Test cards (verified from the docs 2026-08-03; page last updated 2026-07-22)

Any card **not** on their list fails. Name must be **two or more words**, expiry
any future month/year, CVC any 3 digits (4 for Amex).

| Scenario | Card | Result |
|---|---|---|
| **mada approved** | `4201320111111010` | `paid` / 00 |
| **Visa approved** | `4111111111111111` | `paid` / 00 |
| Visa approved, frictionless 3DS | `4111114005765430` | `paid` / 00 |
| **Mastercard approved** | `5421080101000000` | `paid` / 00 |
| Insufficient funds | `4201320000311101` (mada) · `4123120001090000` (Visa) | `failed` / 51 |
| Declined | `4201321234411220` (mada) · `4123120001090109` (Visa) | `failed` / 05 |
| Expired card | `4201322267774310` (mada) | `failed` / 54 |
| Stolen card | `4201321144311528` (mada) | `failed` / 43 |
| 3DS enrollment error | `4111113343111067` | `failed` |
| Card not enrolled in 3DS | `4111116611600661` | `failed` |
| 3DS rejected by issuer | `4111115784228433` | `failed` |
| 3DS attempted, unavailable (ECI 06) | `4111118250252531` | `failed` |

Full matrix (Amex, UnionPay, more decline codes):
`https://docs.moyasar.com/guides/card-payments/test-cards`

#### The webhook problem in local dev

`callback_url` is a **browser** redirect, so `http://localhost:3000/pay/callback`
works fine in dev. The **webhook is server-to-server** and cannot reach
`localhost`. Three ways out, in order of preference:

1. **Tunnel** — `cloudflared tunnel --url http://localhost:8000` (or ngrok), then
   register that URL in the dashboard. Best fidelity; the URL changes each run, so
   re-register per session.
2. **Test the two paths separately** — the `/verify` redirect path fully locally,
   the webhook path against a deployed backend. Acceptable, because the two paths
   converge on the same `_mark_paid_and_grant` function; testing that function once
   covers both.
3. **A Railway staging environment** carrying test keys. ⚠️ **Open question — is
   there one?** The Railway CLI token is currently expired, so I could not check.
   If prod is the only environment, do **not** put test keys on it; use option 1 or 2.

#### ⚠️ The `live` flag trap

Webhook payloads carry `live: true|false`. If one endpoint URL is registered for
both modes, **a sandbox payment could grant a real subscription**. The handler
must reject any event whose `live` value disagrees with the mode of the configured
secret key — before it touches the DB. Same check on the `/verify` path.

#### What only Mohammed can do (dashboard is authenticated)

1. Copy `pk_test_…` + `sk_test_…` from the dashboard.
2. Create the **test webhook** → register the URL → copy its **secret token**.
   Note whether the dashboard separates test and live webhooks or shares one.
3. Later, the same three for live.

### Phase A — Config & secrets

`shared/config.py` (plain field names == env var names; **no `validation_alias`** —
see `feedback_pydantic_validation_alias_trap`):

```python
MOYASAR_SECRET_KEY: Optional[str] = None       # sk_test_… / sk_live_…
MOYASAR_PUBLISHABLE_KEY: Optional[str] = None  # pk_… — served to the browser by /checkout
MOYASAR_WEBHOOK_SECRET: Optional[str] = None   # the dashboard's secret_token
```

Serve the publishable key **from the checkout response**, not `NEXT_PUBLIC_*`.
It avoids the Docker build-arg trap (`project_domain_rayhanai`) and lets test↔live
switch without a frontend rebuild.

**Fail closed:** if `MOYASAR_SECRET_KEY` is unset, `/checkout` returns 503 and the
webhook 401s — same posture as `INTERNAL_WEBHOOK_SECRET` in
`backend/app/api/internal_webhooks.py:85-91`.

### Phase B — Migration `113_payment_refund_revoke.sql`

*(112 is taken — `112_bm25_entity_weight.sql`.)*

1. `payment_transactions.revoked_at timestamptz` — the mirror of `fulfilled_at`.
2. `revoke_plan_grant(p_payment_id uuid)` RPC — symmetric with `grant_plan`:
   - requires `status='refunded' AND fulfilled_at IS NOT NULL AND revoked_at IS NULL`;
     if the payment was never fulfilled (paid but no grant ever ran), just stamp
     `revoked_at` and return — nothing to revoke, not an error;
   - **plan-match guard:** subtract days **only if** the subscription's current
     `plan_id` still equals the refunded payment's plan. If the user has since
     switched plans, the granted window was already destroyed by `grant_plan`'s
     fresh-window logic — subtracting would eat days of the *new* plan they paid
     for (buy basic 10:00 → upgrade pro 12:00 → refund basic 13:00 must not cost
     pro days). In that case stamp `revoked_at` only.
   - **upgrade restore:** if the refunded payment carries `prior_plan_id` (it was
     a prorated upgrade), don't just revoke — **restore** the snapshot: set
     `plan_id = prior_plan_id`, then `expires_at = prior_expires_at` in a
     **second statement** (the assignment trigger recomputes `expires_at` on any
     statement that changes `plan_id`). The user refunds the 111.99 upgrade and
     gets their 26 pro days back — refund means *undo*, not *destroy*.
   - when the guard passes:
     `expires_at := expires_at - (plans.duration_days || ' days')::interval`
     — subtract exactly what was granted, so a refund of one purchase in a stack
     leaves the others intact; landing in the past = expired = free fallback;
   - **its own UPDATE statement** — the `handle_subscription_assignment` trigger
     recomputes `expires_at` whenever `plan_id` changes (see the trap in
     `subscription_system_state.md:109-113`);
   - stamps `revoked_at` → webhook retries are no-ops;
   - `EXECUTE` revoked from `anon`/`authenticated` (service-role only, like every
     other grant RPC).
3. Flip `plans.billing_cycle` for `pro` and `max` from `recurring_monthly` →
   `one_time`. **A field that lies is the bug 091 was written to kill** — flip it
   back in Wave 2 when auto-renew is real.
4. `payment_transactions.vat_amount_sar numeric` + `net_amount_sar numeric`,
   stamped at initiation. VAT is computed **once, at purchase**, and stored —
   never recomputed at display time (rate changes must not rewrite history).

5. **Refund fee columns** — `refund_fee_sar`, `refunded_amount_sar`, stamped on
   the row when a refund executes. The fee itself is a **server-side constant**
   (`REFUND_FEE_SAR = 2.00`), never a client input — same rule as prices. Record
   what was actually charged on the row, so a future fee change doesn't rewrite
   history.

5b. **Upgrade proration columns** — `upgrade_credit_sar` (the credit deducted at
   checkout), `prior_plan_id`, `prior_expires_at` (snapshot of the subscription
   being replaced, stamped when the grant executes). The snapshot is what makes
   an upgrade refund able to restore the old plan instead of leaving the user
   with nothing. `amount_sar` on an upgrade row holds the **charged** (prorated)
   amount — the "charge == catalog price" invariant becomes
   "charge == catalog price − stored credit", still 100% server-computed.

6. **Reprice to `.90`** — `plans.price_sar` becomes 49.90 / 89.90 / 189.90.
   `frontend/lib/pricing.ts` must be updated in the same commit (it is display
   copy only; the DB is authoritative — they drift silently otherwise).

VAT split at 15%, inclusive:

| Plan | Charged (SAR) | Halalas | Net | VAT |
|---|---|---|---|---|
| basic | 49.90 | 4990 | 43.39 | 6.51 |
| pro | 89.90 | 8990 | 78.17 | 11.73 |
| max | 189.90 | 18990 | 165.13 | 24.77 |

**Refund amounts after the 3 SAR fee** (2→3, 2026-08-04) — every refund is a
*partial* refund, so each needs its own VAT split for the credit note:

| Plan | Refunded (SAR) | Halalas | Net | VAT |
|---|---|---|---|---|
| basic | 46.90 | 4690 | 40.78 | 6.12 |
| pro | 86.90 | 8690 | 75.57 | 11.33 |
| max | 186.90 | 18690 | 162.52 | 24.38 |

The retained 3.00 SAR, if treated as a VATable service fee, is 2.61 net + 0.39 VAT.
⚠️ **Whether it is a VATable fee or a non-refunded portion of the original supply
is an accountant's call, not ours** — it changes what the credit note says. Ask
before the first live refund, not after.

### Phase C — Backend `backend/app/api/payments.py`

Mounted at `prefix="/api/v1/payments"` in `backend/app/main.py` (after the plans
router, ~line 638). New `ErrorCode` members in `backend/app/errors.py`:
`PAYMENT_PLAN_NOT_PURCHASABLE`, `PAYMENT_PROVIDER_ERROR`, `PAYMENT_NOT_FOUND`,
`PAYMENT_REFUND_WINDOW_CLOSED`. Arabic messages, per rule 5.

**`POST /checkout`** (authed) — body `{plan_id}` **only**:
1. Plan-transition guard (decision 2026-08-03): allow same-plan re-purchase
   (`grant_plan` stacks) and upgrades (prorated, below); **block downgrades**
   while a higher plan is active (`PAYMENT_PLAN_NOT_PURCHASABLE` variant with a
   clear Arabic message), mirroring `redeem_plan_code`'s guard.
2. Read `plans.price_sar`; `NULL` → `PAYMENT_PLAN_NOT_PURCHASABLE`.
3. **Upgrade proration** — when the user holds a *different, active, paid* plan:
   ```
   credit = 0
   if sub.source == 'payment' and sub.plan_id != new_plan and not expired:
       remaining_days = (sub.expires_at - now())          # fractional
       credit = round(remaining_days / old.duration_days * old.price_sar, 2)
   charge = new.price_sar - credit                        # always > 0 here (max credit 89.90 < 189.90)
   ```
   `source != 'payment'` (code/marketing/manual grants) earns **no credit** —
   otherwise promo codes convert into cash discounts. All server-side; the
   client never sends or sees an amount it can influence.
4. Insert `payment_transactions` (`initiated`, charged amount + VAT split of the
   *charged* amount + `upgrade_credit_sar`, `provider='moyasar'`). **Never accept
   an amount from the client.**
5. Return `{payment_id, amount_halalas, credit_sar, description, publishable_key,
   callback_url}` — `credit_sar` so the page can show «خصم القيمة المتبقية من
   باقتك الحالية: −٧٧٫٩١». No Moyasar API call happens here — the browser form
   creates the payment.

**`POST /verify`** (authed) — body `{moyasar_id}`. Called from **two** moments,
so it is a *sync*, not a paid-assert:
`GET /v1/payments/{id}` → match `metadata.payment_id` to our row **and** the
caller's `user_id` → assert `amount == our halalas`, `currency == 'SAR'` → then
branch on the **fetched** status:
- `initiated` — the `on_completed` pre-3DS call: store `provider_ref` +
  `raw_payload`, grant nothing, return `{status: 'pending'}`. This is what makes
  an abandoned 3DS redirect recoverable (we now hold the Moyasar id).
- `paid` — the callback-page call: mark paid → `grant_plan`. Idempotent via
  `fulfilled_at`.
- `failed` — mark failed, return the failure so the page can offer retry.

**`POST /webhook/moyasar`** (no JWT):
1. Constant-time compare `secret_token` (`hmac.compare_digest` on bytes — the
   `internal_webhooks.py:96` pattern; a non-ASCII value would turn a clean 401
   into a 500).
2. Re-fetch the payment from the API; ignore the body's own fields.
3. Route by event: `payment_paid` → the same paid+grant path as `/verify`;
   `payment_failed`/`payment_abandoned` → `status='failed'`;
   `payment_refunded` → `status='refunded'` **then `revoke_plan_grant`**;
   any other event (`authorized`, `captured`, `voided`, `verified` — we don't use
   manual capture) → **200 + log, no DB write**. An unhandled event must never
   error, or it burns retries on flows we don't participate in.
4. Unknown `provider_ref` → **200 + log** (never 500 a replay; a 5xx burns one of
   only 5 retries).
5. Answer 200 fast.

**`POST /{payment_id}/refund`** (authed, self-serve — this is what makes the
24-hour promise real):
1. Guard `now() - paid_at <= 24h` **server-side**. Outside the window →
   `PAYMENT_REFUND_WINDOW_CLOSED` with the support email.
2. `refund_halalas = amount_halalas - 300` (the 3 SAR fee; 2→3 owner 2026-08-04). Guard
   `refund_halalas >= 100` — Moyasar's minimum — so a hypothetical cheap plan can
   never produce a zero or negative refund.
3. `POST /v1/payments/:id/refund` with that **partial** `amount`.
4. Stamp `refund_fee_sar` + `refunded_amount_sar`, mark refunded, then
   `revoke_plan_grant`. The webhook arrives after and no-ops.

**The confirmation dialog must show the arithmetic before the user commits** —
«سيُعاد إليك ٤٦٫٩٠ من أصل ٤٩٫٩٠ · رسوم معالجة ٣ ريال». Terse, but the numbers
stay: a user who expects 49.90 and receives 46.90 files a complaint; one who
agreed to 46.90 does not. This is a confirmation, not marketing copy.

**`GET /history`** (authed) — the user's own `payment_transactions` rows for a
receipts list in Settings. RLS already allows self-SELECT.

### Phase D — Frontend

- **Form assets** (verified live 2026-08-03 — the docs page renders its code block
  client-side, so these came from probing the CDN + Moyasar's own Magento plugin
  CSP whitelist):

  ```html
  <link rel="stylesheet" href="https://cdn.moyasar.com/mpf/1.19.0/moyasar.css" />
  <script src="https://cdn.moyasar.com/mpf/1.19.0/moyasar.js"></script>
  ```

  **The version is pinned in the path — there is no `latest` alias** (`/mpf/latest/`
  → 403). Available: 1.13.0, 1.14.0, 1.15.0, 1.16.0, 1.18.0, 1.19.0 (no 1.17.0).
  Newest is **1.19.0**, published 2025-07-26 (98 KB JS + 70 KB CSS). Bumping is a
  manual code change — put the version in one constant, not inline in JSX.

- **`frontend/next.config.mjs:44` CSP** — three additions, all confirmed by
  scanning the 1.19.0 bundle (the only hosts it references are `api.moyasar.com`
  and `moyasar.com`):

  | Directive | Add |
  |---|---|
  | `script-src` | `https://cdn.moyasar.com` |
  | `style-src` | `https://cdn.moyasar.com` |
  | `connect-src` | `https://api.moyasar.com` |

  **`frame-src` needs nothing.** The bundle contains zero `iframe` and zero
  `postMessage` references — 3DS is a **full-page redirect** (`window.location` →
  `source.transaction_url`), not an embedded challenge. This is why `on_completed`
  matters: the page is torn down at 3DS, so the payment id must be persisted before
  the redirect fires.

  `style-src` already carries `'unsafe-inline'`, so the form's runtime styles are
  covered. Deploy the CSP change **before or with** the page — the frontend CSP is
  baked at build time (`project_domain_rayhanai`), and a missing host is a silently
  blank form.
- **`/pay/[plan]/page.tsx`** — authed. Calls `/checkout`, mounts `Moyasar.init`
  with `language: 'ar'`, `metadata: {payment_id}`, `methods:
  ['creditcard','applepay']`, `supported_networks: ['mada','visa','mastercard']`.
  (**STC Pay excluded by decision 2026-08-03** — `methods` must list it explicitly
  to enable it, so exclusion is just omission. Re-adding later is a one-line
  change + a Phase F retest.)
  `on_completed` POSTs the Moyasar id to `/verify` **before** the 3DS redirect
  (the `initiated` branch — survives an abandoned redirect).
- **Apple Pay — in Wave 1 via Moyasar's Web Merchant Registration.** ~~Initially
  descoped on the belief it required an Apple Developer account~~ — that is the
  **mobile SDK** path only. For web, Moyasar registers the domain with Apple on
  the merchant's behalf ("without the need for an Apple Developer account"):
  1. Dashboard (Mohammed): Settings → **Apple Pay Domains** → add `rayhanai.com` →
     download the association file → Validate → Register.
     ⚠️ **Not the «Apple Pay – Certificate» page** — that sibling page is for
     native iOS apps / platforms without Web Merchant Registration (its own text
     says so) and requires an Apple Developer account. Leave it empty. If the
     dashboard shows *no* Apple Pay Domains page, Web Merchant Registration isn't
     enabled on the account → support ticket to Moyasar, not a certificate.
  2. Us: serve that file at `/.well-known/apple-developer-merchantid-domain-association`
     — **extensionless**, so `frontend/public/.well-known/`; confirm the Next.js
     middleware matcher skips it, and carve it out of any future Cloudflare
     challenge rules (grey-cloud today makes this moot, but the hardening plan
     will flip that).
  3. Merchant validation session: `GET /v1/applepay/initiate` (publishable key +
     `validation_url`). ⚠️ Verify in sandbox how `apple_pay.validate_merchant_url`
     wires to it — likely a thin backend route proxying the call.
  Fallback: if step 3 fights back in sandbox, ship Wave 1 as `['creditcard']`
  alone and add Apple Pay days later — it is additive.
  (Button renders only in Safari on Apple devices; test there.)
- **`/pay/callback/page.tsx`** — reads `?id=`, POSTs `/verify`, then success
  (→ `/chat` with the new plan) or failure with a retry CTA.
  ⚠️ **Session-restore first:** the access token lives in memory (rule 4), and
  the 3DS round-trip is a full page unload — the app cold-boots on return and
  must finish restoring the session from the refresh token **before** POSTing
  `/verify`, or the call 401s and the page misreads a paid payment as a failure.
  Gate the POST on auth-ready state.
- **`/pricing`** — replace the «الاشتراك غير مُفعّل بعد» notice
  (`frontend/app/pricing/page.tsx:31-47`) and enable the disabled CTA
  (`:98-108`) → link to `/pay/[plan]`; anonymous visitors → `/signup?next=/pay/[plan]`
  (the `?next=` machinery from `project_anon_conversion_popup` already exists).
- **`frontend/lib/pricing.ts`** — three changes: `price` becomes `٤٩٫٩٠` / `٨٩٫٩٠`
  / `١٨٩٫٩٠` (Arabic decimal separator `٫` U+066B, matching the `ar-EG` locale the
  usage dialog already formats with); `billingNote` for pro/max
  (`:59,73`) stops saying «تجديد تلقائي شهري»; add «شامل الضريبة» + the refund
  line.
  **Layout note:** `frontend/app/pricing/page.tsx:74-76` renders the price at
  `text-5xl`. `٤٩٫٩٠` is materially wider than `٤٩` — render the fractional part
  smaller (or the cards will reflow awkwardly at md breakpoints).
- **Settings → receipts list** from `GET /history`, with a refund button inside
  the 24h window.

### Phase E — Copy & the trust claim

**The claim: «جميع العمليات المالية تتم عبر مُيسّر».** Chosen over «لا نحفظ
بطاقتك» because tokenization is approved — a no-storage promise would contradict
auto-renewal the moment Wave 2 ships. This one stays true in both waves.

What makes it defensible, line by line:

| Statement | True? | Why |
|---|---|---|
| Card data never reaches Rayhan's servers | ✅ | The form owns the card fields and posts straight to `api.moyasar.com`; our backend only ever sees a payment UUID + status |
| Moyasar processes, stores, and secures card data | ✅ | They are the PCI-compliant party; we are not in scope |
| Charges, refunds, and settlement are executed by Moyasar | ✅ | We only ever call their API |
| Rayhan stores **no card data** | ✅ | Wave 2 stores an opaque token + brand + last4 + expiry — a reference, not a card |
| ~~"Nothing about your card is stored anywhere"~~ | ❌ | Moyasar retains a payment record (masked PAN, brand, last4) as a legal financial record. **Never write this.** |
| ~~"Rayhan stores nothing about your payment"~~ | ❌ | `payment_transactions` holds amount, VAT split, status, dates. **Never write this.** |

Draft wording — **short**. The claim is the headline; the mechanics (token, brand,
last4) belong in `/privacy` where PDPL requires them, not on a pricing card. Users
don't want a tokenization lecture at the moment they're deciding to pay.

> جميع العمليات المالية تتم عبر **مُيسّر**؛ بيانات بطاقتك لا تمرّ عبر خوادم ريحان.

And, only once Wave 2 ships:

> تُحفظ بطاقتك لدى مُيسّر لتجديد اشتراكك. يمكنك إيقاف التجديد في أي وقت.

⚠️ **Verify before printing:** if the copy is to describe Moyasar as licensed by
SAMA / البنك المركزي السعودي, confirm it from the signed merchant agreement. Do
not take it from a search result — it is a claim about a third party on a page
lawyers will read.

Pages to touch:

- **`/pricing`** — VAT-inclusive note, the 24h refund line, and the payment claim
  above near the CTA.
- **`/terms`** (`frontend/app/terms/page.tsx`) — a real refund clause: the window,
  **the 3 SAR processing fee**, that the subscription is revoked, and how to
  request it. Plus the auto-renewal terms when Wave 2 lands.
- **`/privacy`** (`frontend/app/privacy/page.tsx`) — ⚠️ **PDPL requires disclosing
  processors.** Moyasar becomes a named sub-processor receiving payment data. This
  is not optional copy.
- **`/learn/data-protection`** — ⚠️ currently names **Alibaba Cloud as the only
  named partner** (see `project_discover_rayhan_data_protection`). Once payments
  ship, that page is incomplete until Moyasar is named alongside it. Easy to miss,
  because nothing in the payment code touches that page.

⚠️ **The fee must be disclosed before purchase, not at refund time.** An
undisclosed deduction is a bigger exposure than the fee itself. It belongs in
three places: `/pricing` near the CTA, `/terms`, and the refund confirmation
dialog. Keep it to one clause:

> استرداد خلال أول ٢٤ ساعة من الاشتراك · رسوم معالجة ٣ ريال

The 24h refund window is stated relative to **purchase time**, and the server
measures it from `paid_at` — same clock, no ambiguity.

### Phase F — Sandbox validation (before any live key)

Run with the Phase 0 keys + test cards against prod-shaped data. Add to the list
below: **a test-mode event must never grant on a live-key backend** (the `live`
flag check), and every decline code above should land as `failed` with no grant.

1. Happy path per plan → `payment_transactions` paid, `user_subscriptions_live`
   shows the new term.
2. **Webhook retry** — replay the same event 3× → exactly one grant
   (`fulfilled_at` holds).
3. **Redirect lost** — close the tab after payment; webhook alone must grant.
4. **Webhook lost** — disable it; the callback alone must grant.
5. **Forged webhook** — right `secret_token`, wrong amount in the body → refused
   (the re-fetch catches it).
6. Wrong/absent `secret_token` → 401.
7. Failed card (3DS decline) → `status='failed'`, no grant.
8. **Refund inside 24h** → term revoked, user back on free.
9. **Refund attempt at 25h** → `PAYMENT_REFUND_WINDOW_CLOSED`, term untouched.
10. **Stacking** — buy `pro` twice → term extends, does not reset.
10b. **Prorated upgrade** — pro with N days left → buy max → charged exactly
     `189.90 − round(N/30 × 89.90, 2)`; `user_subscriptions_live` shows max with
     a fresh 30-day window.
10c. **Upgrade refund restores** — refund the upgrade within 24h → user is back
     on pro with the *original* expiry, refunded `charge − 2.00`.
10d. **Code-sourced sub earns no credit** — redeem a `marketing_lawyer` code,
     then buy pro → charged full 89.90, `upgrade_credit_sar = 0`.
11. Cross-user: user B calls `/verify` with user A's `moyasar_id` → refused.
12. Cloudflare: `/api/v1/payments/webhook/moyasar` must not be challenged at the
    edge (same carve-out `/internal/*` has — see
    `cloudflare_navigation_hardening.md` WAF rule 3).

---

## 5. Wave 2 — auto-renewal (approved, sequenced after Wave 1)

Tokenization was approved 2026-08-03. Sketched here so Wave 1 doesn't foreclose
it; the surface Wave 1 must not break is `credit_card.save_card` on the form and
`metadata.payment_id` on every payment.

**Prerequisite, start now:** the **tokenization feature must be enabled on the
merchant account** — it is not on by default. This is a support ticket with
lead time, not a code task, so raise it while Wave 1 is being built.

- **Storage** — new `payment_methods` table: Moyasar token id, brand, last4,
  expiry month/year, `is_default`, `revoked_at`. **Never card data.** Cascades on
  account deletion, and deleting a method must also `DELETE /v1/tokens/:id` so the
  card stops existing at Moyasar too — a local-only delete leaves a live token.
- **Capture** — `credit_card.save_card: true` at checkout → the payment response
  carries `source.token` → store it. Only `active` tokens are chargeable;
  `initiated`/`inactive` are rejected.
- **The clock** — daily job: subscriptions expiring within 24h that have an active
  token and renewal enabled → `POST /v1/payments` with
  `source: {type: 'token', token: …}` → same webhook → same `grant_plan` → stacks.
  3DS is **not** re-triggered on token charges.
- **Failure handling** — retry ladder (+1d, +3d, +5d), Arabic dunning email at each
  step, grace period, then fall to free. This is the piece with no provider support
  at all; budget for it accordingly.
- **Lifecycle we don't have yet** — `past_due` (charge failed, don't cut off yet)
  and "canceled but paid through <date>". Today there is only `expires_at`.
- **Consent** — a recurring-mandate checkbox at first checkout stating the card is
  saved, the amount, the cadence, and how to cancel. Card-scheme requirement, not
  courtesy. Pair it with the Phase E wording.
- **Cancel-renewal UI** in Settings — stops future charges, keeps access to
  `expires_at`.
- **Expiring cards** — a token whose card expires next month will fail silently on
  renewal day. Notify before, using the stored expiry.
- Flip `plans.billing_cycle` back to `recurring_monthly` **in the same wave** as
  the copy that promises it — never before.
- Update `/privacy` + `/learn/data-protection` for the stored-token disclosure.

---

## 6. Traps

1. **`billing_cycle` must never promise what the code doesn't do** — flip pro/max
   to `one_time` in the same migration that ships Wave 1.
2. **Halalas, not SAR.** `price_sar * 100`, integer. A missed ×100 charges 0.49 SAR.
3. **The trigger overwrites `expires_at`** on any statement that also changes
   `plan_id`. `revoke_plan_grant` updates `expires_at` alone.
4. **Never 500 a webhook** — you get 5 retries total, then it's gone forever.
5. **Never trust the webhook body** — no HMAC. Re-fetch, always.
6. **Never trust the redirect** either — `?id=` is attacker-controllable; `/verify`
   must bind the payment to the caller via `metadata.payment_id` + `user_id`.
7. **CSP before deploy** — the form is a CDN script; a missing `script-src` host
   is a silently blank form, exactly like the Turnstile trap at
   `next.config.mjs:29-32`.
8. **VAT is stored, not derived** — a future rate change must not rewrite old rows.
9. **3DS destroys the page.** It is a full-page redirect, not an iframe — any
   state held only in React memory is gone. Persist the payment id server-side via
   `on_completed` → `/verify` before the redirect, and gate the callback page's
   `/verify` on session restore (in-memory token is gone after the round-trip).
10. **The form version is pinned in the CDN path**, with no `latest` alias. It will
    silently rot; a stale version is the likely cause of a future "payment method
    stopped appearing" report.
11. **The 3 SAR fee makes every refund partial** — never call the refund endpoint
    without an explicit `amount`, or it refunds in full and silently gives the fee
    away.
12. **The fee is server-side.** It must never arrive from the client, and the
    charged value must be stamped on the row — a later fee change must not
    retroactively alter past refunds.
13. **`given_id` idempotency applies to Wave 2 only.** In Wave 1 the *form*
    creates the payment (and `given_id` is not a form config key) — Wave 1's dedup
    is `metadata.payment_id` + `UNIQUE(provider, provider_ref)`. Wave 2's
    backend-created token charges must send a fresh UUIDv4 `given_id` per attempt;
    reusing one across different amounts 400s.
14. **Refund-after-upgrade must not touch the new plan's days** — the plan-match
    guard in `revoke_plan_grant` (Phase B). Blind subtraction is the bug.
15. **(found in Phase F)** Never select `user_subscriptions.status` from the
    base table — 091 dropped it; it exists only on the `_live` view. Fake-DB
    tests cannot catch column drift; only a real-DB call can.
16. **(found in Phase F)** `'applepay'` in `methods` on a browser without
    `ApplePaySession` makes moyasar.js 1.19.0 kill the ENTIRE form, card
    fields included. Capability-gate on
    `window.ApplePaySession?.canMakePayments()`.
17. **(found in Phase F)** **Pass the DOM node to `Moyasar.init`, never an id
    selector.** The library overwrites the container's id with its own
    (`mysr-form-form-el`) during mount and lazily re-resolves the stored
    selector string on every internal access — an id selector stops matching
    mid-render and the form self-destructs with "Element: null". Their docs'
    `.mysr-form` class selector is load-bearing, not stylistic.

---

## 7. Open items (not blocking the build, blocking go-live)

- **ZATCA e-invoicing.** VAT-inclusive pricing means we owe a compliant tax
  invoice per purchase. Decide: Moyasar dashboard receipts, an accounting
  integration, or our own generated invoice. Needs an accountant's answer, not a
  code decision.
- **VAT registration.** Charging VAT-inclusive assumes a VAT registration number
  exists and belongs on the invoice.
- **Record retention vs `ON DELETE CASCADE`.** `payment_transactions.user_id`
  cascades so the 090 delete-account flow works, but Saudi rules want financial
  records ~6 years. Options: export-before-delete, or switch to anonymized
  retention (null the FK, keep the row). **Must be resolved before live keys.**
- **Confirm the fee schedule** against the signed merchant contract — the ~1.5%/
  ~2.5% figures here are third-party.
- **Deploy 093's `shared/quota/__init__.py` rewrite** — still local + uncommitted.
  Not blocking, but it belongs in the same wave so the gate reads the unified RPC.
- Whether a **refunded user may re-purchase immediately** (currently yes — nothing
  blocks it). Fine unless abuse appears.

---

## 8. What's needed from Mohammed

### A. Blocking — Phases C/D can't be finished without these

| # | Item | Where |
|---|---|---|
| 1 | `pk_test_…` + `sk_test_…` | Dashboard → API keys. Secret keys display **once on creation** — store it before closing the page |
| 2 | Test webhook registered + its **secret token** | Dashboard → webhook settings. Needs a public URL first (tunnel or staging — see Phase 0) |
| 3 | Does a **Railway staging environment** exist? | If prod is the only one, test keys must never live on it |

Phases 0/A/B (config, migration 113) and most of C can be written before these
land — they only block end-to-end runs.

### B. Product decisions (small, but they change the build)

| # | Question | My recommendation |
|---|---|---|
| 4 | Where does checkout start — `/pricing` only, or also from the quota-exceeded dialog? | **Both.** Hitting the limit is the highest-intent moment there is; `/pricing` alone wastes it |
| 5 | Refund: self-serve button, or email support? | **Self-serve.** The promise is "you can refund within 24 hours" — routing that through email makes it a favour rather than a right, and creates support load |
| 6 | ~~Buying a different plan mid-term?~~ | **RESOLVED 2026-08-03: prorated upgrade charge** — `new_price − remaining value`, credit only for payment-sourced subs, upgrade refund restores the prior plan. Same-plan stacks; downgrades blocked |

### C. Before go-live (not blocking the build)

| # | Item | Note |
|---|---|---|
| 7 | `pk_live_…`, `sk_live_…`, live webhook secret | Last step, after Phase F passes |
| 8 | **Confirm the fee schedule** from the signed merchant agreement | The ~1.5% / ~2.5% in §2 are third-party figures |
| 9 | **Open a tokenization enablement ticket** with Moyasar support | Wave 2 prerequisite, has lead time — raise it during Wave 1 |
| 10 | VAT registration number + **ZATCA e-invoicing** decision | VAT-inclusive pricing means a compliant tax invoice per purchase |
| 11 | Is Moyasar **SAMA-licensed**? Confirm from the agreement | Only if the copy will say so — it's a claim about a third party on a page lawyers read |
| 11b | ~~Does Moyasar return its processing fee on a refund?~~ | PARTIALLY ANSWERED: sandbox showed their txn fee = 1.73 SAR on 49.90. Fee raised 2→3 SAR (2026-08-04) to cover it with margin either way |
| 11c | **Legal read on charging a refund fee** | Your own call — you have the expertise. The question: is your 24h refund a *voluntary* policy (fee is yours to set) or does it overlap a statutory right under نظام التجارة الإلكترونية (deductions may not be permitted)? |
| 11d | **Accountant: is the retained 3 SAR a VATable service fee** or a non-refunded part of the original supply? | Changes what the credit note says |
| 12 | **Retention vs deletion** decision | `payment_transactions.user_id` cascades (090 flow) vs ~6-year financial record retention |
| 13 | `railway login` — the CLI token is expired | Needed for deploys and for me to inspect environments |
| 14 | **Apple Pay domain registration** (Wave 1, ~10 min of dashboard work) | **No Apple Developer account needed for web** — Moyasar's Web Merchant Registration handles Apple. Dashboard → Settings → Apple Pay Domains → add `rayhanai.com`, send me the association file to host at `/.well-known/`, then Validate + Register. (An Apple Developer account becomes relevant only if we ever ship Apple Pay in a native mobile app) |
