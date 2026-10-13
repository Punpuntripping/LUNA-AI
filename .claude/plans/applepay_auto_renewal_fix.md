# Apple Pay buyers never auto-renew — diagnosis and fix plan

**Status: ✅ FIXED AND PROVEN IN PRODUCTION, 2026-09-23.** Phases 0/2/3 shipped (`2640247`),
migration 163 applied, Phase 4 live test PASSED end to end on the Beqo account:

- Apple Pay purchase `76dc8959` returned a real `source.token`; `payment_methods` row written with
  `source_type=applepay`, mada ••6042, expiry 7/2030, consent stamped. Moyasar reports the token
  `status: active`.
- **A/B proof:** the SAME physical card (dpan …4007 / number …6042) bought `basic` via Apple Pay on
  2026-08-18 and got **no token**; today's purchase got one. Only our code changed.
- Forced renewal `9a37f744`: `initiated_by=renewal`, provider **APPROVED**, 49.90 SAR,
  `period_start` = the old expiry, term extended **+30d from the old expiry, not from now()**.
  So a **mada card tokenized through Apple Pay is chargeable merchant-initiated** — the one question
  no documentation anywhere could answer.
- Two defects found and fixed the same day: `on_failure` must be `async` in 2.x (`e551152` — it took
  prod checkout down for ~25 min), and a successful renewal was writing false `card_token_missing`
  audit rows (`38d9032`).

Written 2026-09-20.
**Related:** `.claude/plans/subscription_auto_renewal.md` (the engine plan — note it mentions Apple Pay **zero** times), `.claude/plans/moyasar_payments.md` (Apple Pay integration + traps).

---

## 1. The defect, in one paragraph

`pro`/`max` are sold as 30-day auto-renewing plans. Renewal works by charging a stored Moyasar
token, and that token is created by passing `save_card` to the checkout form. We pass it **only
inside `credit_card`** — `frontend/app/pay/[plan]/page.tsx:152`. The `apple_pay` config object
(`page.tsx:153-175`) is gated on a completely separate condition (`canApplePay`) and has never
carried a tokenization key. So every Apple Pay buyer completes a normal purchase, gets a normal
grant, and is silently never enrolled in renewal. Nothing warns anyone: the capture logs
`"no card token on payment=… — nothing stored"` at **INFO** (`payment_method_service.py:755-759`),
and the renewal sweep drops them with `skipped_no_method` (`renewal_service.py:441`).

**Verified production evidence (2026-09-20):**

| Buyer | Method | `source.token` | Card stored | Auto-renewed 2026-09-17 |
|---|---|---|---|---|
| Arwa (pro) | creditcard | present | yes | yes — 89.90 charged 03:30 |
| Anwar (max) | creditcard | present | yes | yes — 99.90 charged 03:30 |
| الزندي (pro) | **applepay** | **null** | no | never a candidate |
| Awad Kazman (pro) | **applepay** | **null** | no | never a candidate |

Re-fetched الزندي's payment live from Moyasar today: `source.token` is still `null`, and the
`token` field **exists** on their applepay source object. Nothing is recoverable retroactively —
affected buyers can only be re-enrolled through a new payment, never backfilled.

**Exposure:** 3 of 10 paid purchases were Apple Pay (139.70 SAR), 2 on renewable plans. Both flags
are live in prod (`MOYASAR_APPLEPAY_ENABLED=true`, `SUBSCRIPTION_AUTO_RENEWAL_ENABLED=true`), so
this is an **active leak**, not a historical one.

---

## 2. Whose fault it is — settled

Not Moyasar's. Three layers, each independently verified:

1. **Moyasar supports it.** `save_card` (snake_case) is documented inside `apple_pay`,
   `samsung_pay` and `credit_card`. Their tokenization guide states outright: *"`save_card` is
   supported on `creditcard`, `applepay`, and `samsungpay` sources"*, and *"Once you have an
   `active` token … you can use it to charge the customer again without them re-authenticating."*
   The `/v1/tokens` create endpoint is card-only and explicitly redirects wallet users to the
   `save_card` flow.
2. **Our pinned bundle cannot send it.** `cdn.moyasar.com/mpf/1.19.0/moyasar.js` — downloaded and
   read — has only `credit_card_save_card`; its applepay source builder is
   `{type:"applepay", token: JSON.stringify(...)}` with no save_card, and its apple_pay config keys
   are country/label/merchant_capabilities/supported_countries/validate_merchant_url/version.
3. **Our code never passes it**, and `MoyasarApplePayConfig` (`frontend/lib/moyasar.ts:82-98`)
   wouldn't compile if it did.

### 2.1 The finding that changes the shape of the fix

**We are on a frozen distribution track.** Moyasar's current docs no longer reference
`cdn.moyasar.com/mpf/` at all. The live form is the npm package `moyasar-payment-form`, served from
jsDelivr, currently **2.2.13** (published 2026-09-12):

```
https://cdn.jsdelivr.net/npm/moyasar-payment-form@2.2.13/dist/moyasar.umd.min.js
https://cdn.jsdelivr.net/npm/moyasar-payment-form@2.2.13/dist/moyasar.css
```

`apple_pay.save_card` first appears in the shipped code at **2.0.14** (2025-06-22), confirmed by
diffing bundles — Moyasar publishes no changelog. Our `mpf/1.19.0` was last modified 2025-07-26 and
`mpf/` now 403s for every other path, including `2.2.13`.

So this is **not a version bump**. It is a migration to a different distribution channel and a
different major version of the library. That is the single biggest risk item in this plan, and it
also means the 1.19.0 pin is a standing liability independent of Apple Pay.

---

## 3. What else the audit turned up (all verified, all load-bearing)

1. **Nothing in the backend branches on `source.type`.** Capture is a pure duck-type on
   `source.token` (`payment_method_service.py:399-461`). The moment Apple Pay returns a token it is
   stored as a plain card: `brand` from `company` → `"visa"`, `last4` from the funding PAN `number`
   → `2796`, **not** the `dpan` (`2764`). `dpan` is read by no code in the repo. No column records
   that a credential is a device token, so the two populations become indistinguishable.
2. **The asymmetric status rule becomes a wider hole.** `fetch_token_at_provider` returning `None`
   (unknown) means *proceed and store* (`payment_method_service.py:808-819`). For a DPAN that the
   scheme won't accept for MIT, one flaky provider call stores a permanently unchargeable
   credential — and the user sees «فيزا ••2796» in settings while every renewal declines.
3. **`charge_saved_card`'s no-3DS assumption is unverified for network tokens**
   (`payment_service.py:614-620`). It reasons the instrument "was already verified when the token
   was created" — a PAN-token argument. `retries=0`, and an ambiguous outcome blocks the whole
   period.
4. **A later Apple Pay purchase would silently destroy a working card token** — capture revokes the
   previous one and **DELETEs it at Moyasar** (`payment_method_service.py:833-837`). Irreversible,
   and with no add-card route the user cannot restore it without buying again.
5. **The dunning email already promises a surface that doesn't exist.** `receipt_service.py:385`
   tells a declined subscriber to «افتح ريحان ← إعدادات الحساب» to update their card. Settings can
   only **remove** a card. The CTA URL is hard-coded to `/chat` because `frontend/app/settings/` is
   an empty directory.
6. **A subscriber with no stored card sees nothing at all.** The whole «وسيلة الدفع» section is
   wrapped in `{hasCard && …}` (`AccountSettingsDialog.tsx:888`) — no empty state, no warning that
   the subscription will not renew.
7. **`source='payment'` is deliberate policy, not an accident** — stated in the plan (§1 rule 1), in
   a code comment (`renewal_service.py:185-187`), and pinned by a test that stores a card first and
   still asserts no charge (`test_subscription_renewal.py:1288-1296`). Consequence worth knowing:
   **one manual grant on top of a paying subscriber removes them from auto-renewal permanently**,
   and the only way back is a real paid checkout (`grant_plan(..., p_source='payment')` is the sole
   writer). الزندي is now excluded on *both* counts after yesterday's manual renewal.
8. **No test anywhere exercises an Apple Pay payload.** The assumption is encoded implicitly: every
   tokenization fixture hard-codes `"type":"creditcard"`, and the negative parametrize list at
   `test_subscription_renewal.py:943-951` has no applepay shape.
9. **Visa postponed the DPAN→MPAN standing-instruction restriction indefinitely** (Visa Business
   News AI15042). Relevant because our Apple Pay payload carries a `dpan` with `company: visa`.
10. **Undocumented anywhere:** whether a Saudi-issued mada card tokenized via Apple Pay is
    chargeable for MIT domestically, and whether it routes as `mada` or as the co-badge network.
    Moyasar, mada and SAMA are all silent. This is a provider question, not a docs question.

---

## 4. Decisions needed before building

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Stopgap while the fix is built | (a) leave Apple Pay on and accept non-renewal; (b) disable it for `pro`/`max` only (one-line server gate, `payment_service.py:1673`); (c) kill it entirely via the Railway env flag | **(a)** — at 10 total purchases, losing a sale costs more than a missed renewal, and outreach to 3 people is trivial. Revisit if Phase 2 slips past ~2 weeks. |
| D2 | Ask Moyasar about mada/DPAN MIT | before building / in parallel / skip | **In parallel, now** — the answer gates Phase 4, not Phase 2, and their reply time is outside our control. |
| D3 | Enrollment surface (Phase 5) | build now / defer | **DEFERRED — owner decision 2026-09-20.** The already-affected cohort is small enough to ignore. Revisit when a *future* renewal declines, since that is the other thing this surface fixes. |
| D4 | الزندي + Awad Kazman | leave / tell them / comp | **Closed — leave them.** الزندي is comped to 2026-10-20; Awad lapsed 2026-09-20. Not worth engineering around. |

---

## 5. The plan

### Phase 0 — Make the cohort visible (no provider risk, ship immediately)

Independent of everything else. Nothing here touches the checkout.

1. **Raise the silent log.** `payment_method_service.py:755-759` — a renewable purchase that yields
   no token is currently INFO and indistinguishable from a `basic` purchase or a flag-off deploy.
   Make it a WARNING plus an `audit_logs` row carrying `source.type`, so the population is
   queryable.
2. **Empty state in settings.** `AccountSettingsDialog.tsx:888` — when a `pro`/`max` subscriber has
   no stored card, say so: «لن يُجدَّد اشتراكك تلقائياً» plus what to do about it. Today they see
   nothing.
3. **Fix the dunning CTA** (`receipt_service.py:344-349, 385`) — either point it somewhere that can
   actually help, or stop promising a card update until Phase 5 lands.
4. **Stale artefacts** to correct while in there: `subscription_auto_renewal.md:3` still says
   "Everything else NOT BUILT" (Phases 2–4 are live); `receipt_service.py:401-408` still says the
   mail transport is blocked (it isn't — `RECEIPTS_SMTP_PASSWORD` is set in prod);
   `frontend/lib/moyasar.ts:92-97` still describes `validate_merchant_url` as our own proxy route,
   contradicting the 2026-08-18 incident 40 lines above it; `frontend/types/index.ts:1856` asserts
   `credit_card.save_card` is the only tokenization path; `useRecurringConsent` /
   `acceptRecurringConsent` are dead code with stale docstrings describing a checkbox that was
   removed in `a72fdba`.

### Phase 1 — Moyasar's answers — **RECEIVED 2026-09-22**

Their reply, plus a direct inspection of the `moyasar-payment-form@2.2.13` bundle to verify it.

**Answered, and it closes the open risks:**

| Q | Their answer | Consequence |
|---|---|---|
| Wallet tokenization on our account | **NOT ENABLED.** It is a separate backend flag from the 2026-08-12 card enablement. Must be requested from the registered contract email | **NEW BLOCKER — see below** |
| Token status | Apple Pay tokens are created **directly `active`**; device auth (Face/Touch ID) already supplied SCA, no `verification_url` flow | Our `status != active` refusal never bites. Risk closed |
| MIT chargeability | Supported for domestic + international, under SAMA/Visa/Mastercard/mada rules, given the initial transaction was cardholder-authenticated | The DPAN standing-instruction worry is closed |
| `last_four` / expiry vs `dpan` | `last_four` + expiry = the **FPAN** (funding card, ...2796). `dpan` = the device PAN (...2764), which is what actually processes | Our current parse already reads the funding PAN — **correct as-is**, keep displaying 2796 |
| mada co-badge routing | `company` returns `visa`/`mastercard` on international rails, `mada` on local. Both fully supported domestically | No action |
| 3DS on renewal charges | **Not triggered** — cardholder-absent token charges are exempt; the initial Apple Pay auth establishes the relationship | `charge_saved_card`'s assumption is confirmed |
| MPF 1.x | **Deprecated**, no feature updates, explicitly lacks Apple Pay tokenization support. npm/jsDelivr 2.x is the production standard | Migration confirmed necessary |

**⚠ One support claim is WRONG — verified against the shipped 2.2.13 bundle:**

Support wrote that 2.x *"integrates 3DS seamlessly using an embedded iframe overlay … eliminating the
need to redirect customers away from your checkout flow."* **The bundle contains zero iframes**
(`grep -oi iframe` → 0). 3DS in 2.2.13 is still a full-page redirect:

```js
function R(nA){ const {transaction_url: Pt} = nA.source;
  Pt && (apple_pay?.auto_redirect_3ds) !== false && (window.location.href = Pt) }
```

Good news: **no `frame-src` CSP change is needed**, and our existing architecture is still correct —
`on_completed` firing before the redirect remains load-bearing, and the webhook + callback recovery
still carries the flow. 2.x does add a new `auto_redirect_3ds: false` opt-out if we ever want to own
the redirect ourselves. Do not design around the iframe claim.

**Also verified directly in the 2.2.13 bundle:**

- **`methods` is the correct option name**, confirmed three ways: the bundle, support, and the docs
  page itself (its *heading* is "Payment Methods (optional)", but the documented key is `methods` —
  `payment_methods` appears **0** times on the page). Our existing call already passes `methods`.
- The referenced anchor `form-configuration#tokenize-save-apple-pay-optional` reads verbatim:
  *"This option allows you to tokenize credit card information after successfully completing the
  transaction."* + `apple_pay: { save_card: true, }` — **with no prerequisite or activation note**,
  identical in form to the `credit_card` variant. The documentation sides with Moyasar's second
  reply.
- The docs page documents **nothing** about 3DS presentation (no `iframe`, no `auto_redirect_3ds`),
  so the iframe claim is contradicted by the bundle and unsupported by their own docs.
- The Apple Pay source builder is
  `{type:"applepay", token: …, manual: !!apple_pay.manual, save_card: !!apple_pay.save_card}` —
  `apple_pay.save_card` is exactly the key that reaches the source. Confirmed present.
- Including `"applepay"` in `methods` now **throws** unless an `apple_pay` config object is supplied.
  Our capability gate already adds both together.
- The ASCII-only label rule survives: `/^[\x20-\x7E]+$/` → *"should be English characters only"*.
  Keep `label: "Rayhan"`.
- `X-Moyasar-Form-Version` is still sent on merchant validation.

**🚨 NEW CSP REQUIREMENT, discovered in the bundle, not mentioned by support:** whenever an
`apple_pay` config is present, 2.x dynamically injects **Apple's own SDK**:

```
https://applepay.cdn-apple.com/jsapi/1.latest/apple-pay-sdk.js
```

That host is **not** in our `script-src`. Without adding it, the script is blocked — silently, as
always. This is now a hard requirement of Phase 2, not a maybe.

The bundle also ships an **AppSignal error-reporting client** that POSTs to
`https://appsignal-endpoint.net/collect`. It is not in `connect-src`, so it is blocked by omission —
**leave it that way**. Sending our checkout's error telemetry to an unnamed third party is a
data-protection decision nobody made, and the form does not need it to work.

### Phase 1b — the account flag — **NOT A BLOCKER (resolved 2026-09-23)**

Moyasar gave two contradictory answers:

- **Reply 1 (2026-09-22):** *"Tokenizing Apple Pay payments requires an additional backend feature
  flag on Moyasar's platform … If `payment.source.token` returns null … Apple Pay tokenization is
  not yet active for your account."*
- **Reply 2 (2026-09-23):** *"Tokenization is already enabled on your merchant account. To link
  Tokenization with Apple Pay, please access our developer documentation … `form-configuration
  #tokenize-save-apple-pay-optional`. Once the configuration is completed, you can proceed with a
  live Apple Pay transaction to verify that the token is returned successfully."*

**Decision: proceed, do not chase a third reply.** Both answers converge on the same next action —
ship the 2.x migration with `apple_pay.save_card: true`, then run one live Apple Pay purchase. That
transaction adjudicates:

- token returned → reply 2 was right, nothing was ever needed from them;
- `source.token` still `null` with the config provably correct → reply 1 was right; reopen the
  ticket quoting reply 1 verbatim, with the new payment id as evidence. Arguing it beforehand costs
  days and wins nothing.

Keep reply 1 on file. It is the leverage if the live test fails.

### Phase 1 (original) — the questions that were sent

Two questions, both unanswerable from documentation:

1. Can a token created from an `applepay` source be charged merchant-initiated / cardholder-absent
   with the same reliability as a `creditcard`-sourced token, or do mada scheme rules / DPAN
   standing-instruction limits make it unsuitable for subscription billing?
2. When a Saudi-issued mada card is tokenized via Apple Pay and charged domestically, does `company`
   come back as `mada` or as the co-badge network? We observe `company: visa` with a populated
   `dpan`.

Also worth asking: is there a changelog for `moyasar-payment-form`, and is `cdn.moyasar.com/mpf/`
deprecated?

### Phase 2 — Migrate the form, then pass `apple_pay.save_card`

**This is the real work.** Not a version bump — a channel + major-version migration.

1. `frontend/lib/moyasar.ts:23-26` — move from `cdn.moyasar.com/mpf/1.19.0/moyasar.js` to the
   jsDelivr npm build, pinned exactly (`moyasar-payment-form@2.2.13`, `moyasar.umd.min.js`).
2. `frontend/next.config.mjs:50-63` — add to `script-src`: `https://cdn.jsdelivr.net` **and
   `https://applepay.cdn-apple.com`** (2.x injects Apple's SDK itself — see Phase 1). Add
   `https://cdn.jsdelivr.net` to `style-src`. Do **not** add `appsignal-endpoint.net` to
   `connect-src` — leave that telemetry blocked. `frame-src` needs nothing: 3DS is still a redirect.
   CSP is baked at build time, so this needs a frontend rebuild+redeploy, not an env flip. A missing
   host is a **silently blank form**, not an error.
3. ~~Verify the 2.x option contract~~ — **DONE, see Phase 1.** `methods` is correct (their docs page
   saying `payment_methods` is wrong), `apple_pay.save_card` reaches the source, `on_completed` /
   `on_failure` / `element` / `supported_networks` / `credit_card.save_card` all still present.
4. `frontend/lib/moyasar.ts:82-98` — add `save_card?: boolean` to `MoyasarApplePayConfig`.
   **Mandatory**: without it `npx tsc --noEmit` fails, and that runs in CI (`ci.yml:80`).
5. `page.tsx:153-175` — pass `save_card: true` inside `apple_pay` when **`requiresConsent &&
   canApplePay`**. The two gates are currently disjoint; the fix is precisely to cross them. Do not
   tokenize a wallet purchase on a plan that doesn't renew — that is the same PDPL
   data-minimisation rule the card path already follows.
6. **Preserve two byte-exact source strings** that backend tests grep (neither runs in CI, run them
   by hand): `"validate_merchant_url: MOYASAR_APPLEPAY_VALIDATE_URL"` in `page.tsx`
   (`test_payments.py:1345`), and the two-line `MOYASAR_APPLEPAY_VALIDATE_URL = \n  "https://api.moyasar.com/v1/applepay/initiate"`
   in `moyasar.ts` including indentation (`test_payments.py:1358`). A Prettier reflow breaks these.

**Traps a bundle migration can re-trigger** — every one of these was paid for in production once:

- Apple Pay in `methods` on a browser without `ApplePaySession` **kills the entire form**, card
  fields included ("Element: null is not a valid element"). Keep the three-factor capability gate
  regardless of what 2.x claims to fix.
- `element` must be the **DOM node**, never an id selector — 1.19.0 overwrites the container id and
  re-`querySelector`s lazily. The node reference is immune by construction; keep it.
- Merchant validation must keep pointing at `api.moyasar.com` directly. Proxying it through our
  origin silently killed every Apple Pay payment until 2026-08-18. Note the SDK sends
  `X-Moyasar-Form-Version` on that request — **the header value is the bundle version**, so a
  migration changes what Moyasar sees on the one request that already broke once.
- `apple_pay.label` is **ASCII only** — «ريحان» is a 400 and a payment sheet that vanishes in a
  second.
- ~~`frame-src` / iframe 3DS~~ — **resolved: 2.2.13 uses a full-page redirect, no iframe.** Support
  claimed otherwise; the bundle disproves it. No `frame-src` change.
- `on_completed` must still fire **before** the 3DS redirect and still be awaited, or the early
  payment-id binding is lost (recoverable via webhook, invisible from the client).
- Amounts are **halalas**. Stylesheet is deliberately not awaited. There is no `latest` alias.

**Test on a real iPhone.** There is no frontend test runner in this repo; `tsc --noEmit` and
`npm run lint` are the only automated frontend gates.

### Phase 3 — Teach the backend that a wallet token is a wallet token

1. Migration: add `source_type` (`creditcard` | `applepay` | `samsungpay`) to `payment_methods`.
   Today nothing distinguishes a DPAN credential from a PAN one — and given open item #10 above,
   you may need to treat them differently later. Capture it now or lose the ability.
2. `extract_card_token` — carry `source.type` through; consider storing the `dpan` last4 alongside
   the funding-PAN last4 so support can reconcile what the customer sees on their statement.
3. `describe_method` + `AccountSettingsDialog.tsx:141-153` — display «Apple Pay» rather than
   silently claiming «فيزا». The frontend brand map falls back to the raw string, so an unmapped
   value renders as bare Latin text inside an RTL panel.
4. Note the DB tripwire gap: the `provider_token !~ '^[0-9]{12,19}$'` PAN check is bare-digits only;
   a dashed masked DPAN would pass it.
5. **Tests that will break and must be updated** (they assert exact dicts, so any new key fails
   them): `test_subscription_renewal.py:937-941`, `:954-966`, `:770-781`. Add the real applepay
   payload to the negative parametrize list at `:943-951`, and add applepay twins for the capture
   behaviours at `:1004-1149`.

### Phase 4 — Prove it with one real charge

Nothing before this proves anything. The card path only became trustworthy after a live test in
August; the wallet path needs the same. **Decision 2026-09-23: zero-code runbook on the existing
`pro` plan — no test plan, no new code, no cleanup to forget in production.** A cheap fast-cycling
test plan (`test_daily`, 2 SAR, `duration_days=1`, allowlisted by user id) was designed and
deliberately deferred: it buys cheap *repetition*, which is only worth its machinery once the dunning
ladder, the cancel flow, or several consecutive renewals need exercising. Revisit then; the design is
in the conversation record.

**Test account: Beqo** — `beqo.o.lata@gmail.com`, `user_id = 3fc0059c-40b1-4611-933d-2a122909cbfa`.
Verified clean 2026-09-23: `basic` expired 2026-08-25, effective plan `free`, **no active
subscription** (so no downgrade guard, no upgrade proration), **no stored card**,
`renewal_cancelled_at` NULL, and `source` already `payment`. It also has a proven Apple Pay history
on this checkout (its `basic` purchase was applepay), so the device and card are known to work.

1. **Buy `pro` with Apple Pay on a real iPhone**, after Phase 2 is deployed. This alone answers the
   only question that matters first: does `source.token` come back.
2. **Verify capture** — a `payment_methods` row with a non-null `provider_token`, `source_type =
   'applepay'` (Phase 3), and a **non-null expiry**. Non-null expiry is the tell that the
   `GET /v1/tokens/:id` enrichment ran; the payment response itself carries no month/year.
   If instead the new WARNING + audit row from Phase 0 fires, the token did not come back → reopen
   the Moyasar ticket quoting their 2026-09-22 reply (the account-flag one) with this payment id.
3. **Force the renewal** — `UPDATE user_subscriptions SET expires_at = now() + interval '1 hour'
   WHERE user_id = '3fc0059c-…'`. **That column ALONE**: touching `plan_id` in the same statement
   fires `trg_user_subscriptions_assignment`, which recomputes `expires_at` from
   `plans.duration_days` and silently overwrites what you just set.
4. **Run the sweep manually** — `python scripts/run_renewal_once.py --list` to confirm it is
   selected, then `--run`. Do not wait for the 03:30 UTC cron.
5. **Verify the charge** — `initiated_by='renewal'`, `status='paid'`, `fulfilled_at` set, and
   `expires_at` extended 30 days **from the old expiry, not from `now()`**. Refund that renewal
   payment afterwards if the money should come back.
6. **If it declines with a scheme-level reason**, stop: that is the DPAN merchant-initiated question
   arriving as a decline, despite Moyasar's written assurance. Capture `decline_reason` verbatim and
   go back to them with it.

Note: `SUBSCRIPTION_AUTO_RENEWAL_ENABLED=true` is already live in prod, so the 03:30 cron will also
pick up whatever you leave inside the 24h window. Run the script immediately after step 3 to stay in
control of when the charge happens.

### Phase 5 — Close the enrollment gap — **DEFERRED (owner, 2026-09-20)**

Scoped out: the already-affected cohort is small and is being written off deliberately. Kept here
because the same surface is what a **future** declined renewal needs — the dunning ladder currently
emails a user asking them to update a card through a surface that cannot update a card. Revisit on
the first real decline, or when Phase 4 proves wallet tokens decline more often than card tokens.

Today the *only* way to store a card is to complete a full-price purchase. That is why the dunning
email points nowhere, why grandfathered subscribers can never enroll, and why every Apple Pay buyer
so far is unreachable.

The clean path exists and we don't use it: **Moyasar's `POST /v1/tokens` create-token flow.** It is
card-only, and save-only tokens always trigger 3DS — which is fine, because the user is present and
choosing to add a card. No repo code calls it today (`grep create_token` → nothing), and there is no
zero-amount or verification-charge path (`MIN_CHARGE_SAR = 1.00`).

Needs:

- `POST /payments/method` (or a save-only session endpoint) — the payments router has no write route
  for payment methods at all today.
- A consent artefact for a capture with no purchase behind it. The current v2 rule is literally *the
  completed purchase is the consent* (`payment_method_service.py:764-790`), hashing `paid_at`
  against the disclosure constant. There is no purchase here, so this needs its own model — the dead
  `POST /{payment_id}/consent` path is keyed on a payment id and cannot be reused as-is.
- An add-card UI and an actual settings destination (`frontend/app/settings/` is empty, which is why
  the dunning email links to `/chat`).
- Cache invalidation of `paymentKeys.method()` after a successful capture — nothing invalidates it
  after a purchase today either, so a newly stored card doesn't appear until a refetch.

### Phase 6 — The `source='payment'` policy

Decide explicitly, then write it down:

- A manual grant on a paying subscriber silently and permanently un-enrolls them from renewal. Is
  that intended? It is currently a side effect of `grant_plan`'s unconditional
  `source = EXCLUDED.source`.
- If not intended, the fix is an operator path that preserves enrollment — not relaxing the filter,
  which exists to stop billing people who never bought.
- Whatever is decided, `test_a_non_payment_grant_is_never_charged` (`:1288-1296`) is the test that
  encodes the current answer.

---

## 6. Sequencing

```
Phase 0 (visibility)        ──┐  ship alone, today, no provider dependency
Phase 1 (ask Moyasar)       ──┤  in parallel, answer gates Phase 4's interpretation
Phase 2 (form migration)    ──┴─→ Phase 3 (backend provenance) ─→ Phase 4 (live charge)
Phase 5 (enrollment)             DEFERRED
Phase 6 (policy)                 decision, not code
```

The critical path is **2 → 3 → 4**, and only Phase 4 proves anything. Phase 0 ships independently
and is what stops the next occurrence from being silent.

---

## 7. Open questions

1. D1 — stopgap or not while Phase 2 is built.
2. ~~2.x option names / 3DS presentation / `on_completed` contract~~ — **answered 2026-09-22** by
   support plus direct bundle inspection. See Phase 1.
3. ~~MIT chargeability on mada via Apple Pay~~ — **answered: supported.**
4. Whether to treat wallet credentials differently at renewal time — Moyasar says no difference;
   Phase 3 still records provenance so the question stays answerable if renewals start declining.
5. ~~Whether the account flag has been enabled~~ — **no longer blocking.** Moyasar contradicted
   themselves; the Phase 4 live transaction settles it either way. See Phase 1b.
