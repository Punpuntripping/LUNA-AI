# عرض الشهر الأول — Profession Intro Offer (Pro)

**Status:** PLANNED 2026-10-21 · nothing built · nothing applied
**Owner decisions locked:** 2026-10-21 (reflection session, §1)
**Migrations:** `182_early_adopter_500_and_period_boundary.sql` (Phase 0) ·
`183_profession_intro_offer.sql` (Phase 1). 181 is taken by
`blog_ask_similar_questions.md`.

**Read first:** `early_adopters.md` (the seat machinery this rides on),
`subscription_auto_renewal.md`, `quota_upgrade_ladder.md`.

---

## 1. The rules, as decided

| Profession (`users.profession_group`) | Month 1 | Months 2–3 | Month 4 → |
|---|---|---|---|
| `legal` «قانوني» | **1.99** | 49.90 each | 94.90 list |
| `specialist` «مختص» · `entrepreneur` «رائد أعمال» | **9.90** | 49.90 each | 94.90 list |
| `individual` · `declined` · NULL | — no offer — | | |

1. **Pro only.** Max and Basic are untouched by this offer.
2. **No code.** Eligibility is decided server-side; the client never names a price.
3. **Eligible = all of:**
   - the **first** profession answer (`users.profession_first_group`, write-once —
     owner, 2026-10-21) ∈ {legal, specialist, entrepreneur}. Changing the
     profession later via «تغيير المهنة» neither unlocks nor loses the offer.
     A NULL/`declined` user's first pick (incl. the one re-ask) IS their first
     answer;
   - not subscribed to anything — effective plan is `free` (or locked/NULL).
     An active `basic`, «ترويجي» trial, `pro`, `max` or `dev` ⇒ not eligible;
   - **never paid for `pro` or `max`** (a `paid` or `refunded` row). A user whose
     only past purchase was `basic` IS eligible;
   - campaign open: `intro_offer_campaign.enabled` AND `now() < ends_at` AND
     `early_adopter_open()` (seats remain).
4. **Every offer purchase claims an early-adopter seat.** Months 2–3 at 49.90 are
   simply the seat's existing 90-day price; nothing new prices them.
5. **Shown:** (a) right after the user saves an eligible profession, and (b) on
   **every** quota block while eligible.
6. **The first month is non-refundable.** Months 2+ (renewals) keep the normal
   24h self-serve refund.
7. **Card saving + auto-renewal rules apply unchanged** — consent box, card /
   Apple Pay token, renewal 24h before expiry, dunning 0/+1/+3, cancel forfeits
   the seat (`release_reason='cancelled'`), undo restores it.
8. **Ends** at **2026-12-31 23:59:59 Asia/Riyadh = `2026-12-31T20:59:59Z`**, or
   earlier when the seats run out. Purchases made before then keep their months
   2–3 price.
9. **Never shown:** an end date, a seat count. «المقاعد محدودة» is the only
   scarcity signal (same rule as the early-adopter campaign).
10. **Early-adopter seats 100 → 500**, for the whole campaign, not just this
    offer.
11. **Upgrade credit is NOT capped** (owner, 2026-10-21): an offer holder who
    upgrades pro → max is credited at the seat price 49.90 even though they paid
    1.99/9.90. `_upgrade_credit` stays exactly as it is. Do not "fix" this.
12. **The 4-step «اتعرف على ريحان» tour is retired** (both the A2 post-purchase
    auto-open and the sidebar entry). «المحادثة التجريبية» is the only
    walkthrough. The sidebar item becomes **«تغيير المهنة»** (profession step
    alone).
13. **Users who declined the profession question are asked once more.** A
    second dismissal is final.
14. **Intro-offer users may come back after cancelling** (owner, 2026-10-21).
    For everyone else a cancellation that runs out still forfeits the
    early-adopter price for good (`/promo-terms` §3.1, unchanged). A user who
    ever paid an intro-offer first month is exempt from that forfeiture: after a
    cancellation lapses, buying Pro again while the early-adopter campaign is
    open costs **49.90 with a fresh 90-day seat**. This is the refund rule
    (§3.3), extended to their cancellations. They never get the 1.99/9.90 price
    again, because they have now paid for Pro. "Still activated" means
    `early_adopter_open()` (switch on and seats left), **not** the intro
    campaign's `ends_at`.
15. **Accepted risks:** `legal` is self-declared and also covers law students and
    researchers. (Switching into an offer group via «تغيير المهنة» is CLOSED —
    eligibility follows the first answer, migration 183 §4b.) Moyasar's
    per-transaction fee eats most of a 1.99 charge (≈1.17 SAR on mada by the
    fee seen so far), so a lawyer signup nets ≈0.8 SAR on month 1.

Out of scope: mobile app (sells nothing yet; every backend change below is
additive), the quota dialog for non-eligible users, Basic/Max pricing.

---

## 2. Phase 0 — ship first, independent of the offer (deadline ≈ 2026-11-14)

### 2.1 Bug: the 4th seat-holder charge is billed at the promo price

`effective_plan_price(..., 'current')` tests `s.promo_ends_at > now()`
(`138_early_adopters.sql:745`). The renewal job charges inside `DUE_HORIZON = 24h`
before expiry (`renewal_service.py:123`), so the charge for the period that
**begins** on day 90 runs on ~day 89, while the seat is still live → **49.90
instead of list**. Every seat holder gets four promo charges, not the three the
rule (`early_adopters.md` §1.3: "the first period that BEGINS after the window is
full price") and `/promo-terms` §2 promise.

First seat affected: claimed 2026-08-18 19:31Z → `promo_ends_at` 2026-11-16
19:31Z → its day-90 renewal runs ≈ 2026-11-15 03:30Z. **Phase 0 must be live
before then.**

**Fix (migration 182 + backend):**
- `DROP FUNCTION public.effective_plan_price(uuid, text, text)` and
  `CREATE` `effective_plan_price(p_user_id uuid, p_plan_id text, p_context text
  DEFAULT 'purchase', p_at timestamptz DEFAULT NULL)`; branch (b) becomes
  `s.promo_ends_at > COALESCE(p_at, now())`. Re-apply the REVOKE/GRANT
  (service_role only) to the new signature.
  - ⚠ DROP + CREATE, never CREATE OR REPLACE with an extra param: that makes a
    second overload, and PostgREST cannot resolve a 3-named-arg call between
    `(uuid,text,text)` and `(uuid,text,text,timestamptz DEFAULT …)`.
  - The old backend's 3-named-arg call resolves to the new function via the
    default, so **migration first, then deploy** is safe. The reverse is NOT:
    a new backend sending `p_at` to the old function fails the RPC, and
    `_effective_price` falls back to the catalog → seat holders charged list
    price. Never deploy ahead of 182.
- `payment_service._effective_price(..., at: Optional[datetime] = None)` passes
  `p_at` only when given.
- `renewal_service._renew_one` passes `at=expires_at` (the `period_start` it
  already computes, `renewal_service.py:386`).
- Every other caller keeps `now()` semantics (checkout, upgrade credit,
  revalidation) — they price "the term running now", which is correct.
- Boundary check: `promo_ends_at = paid_at + 90d`; the third renewal's
  `period_start = fulfilled_at + 90d ≥ paid_at + 90d` (paid_at is stamped before
  `grant_plan` runs), so `promo_ends_at > period_start` is false → list price.
  Day 30 and day 60 periods are inside → 49.90. Exactly three promo charges.

### 2.2 Seats 100 → 500

Same migration: `UPDATE public.early_adopter_campaign SET seat_limit = 500 WHERE
id AND seat_limit = 100;`. No code reads 100; `/promo-terms` and
`product_docs.pricing` never state the count (verified 2026-10-21).

### 2.3 Tests (Phase 0)
- `test_subscription_renewal.py`: renewal at day 29 / 59 → promo; day 89 → list
  (mock RPC asserts `p_at` is sent = period start).
- `test_early_adopters.py`: `_effective_price` forwards `p_at` only when given.
- Live SQL after apply: for the first seat holder,
  `effective_plan_price(u,'pro','current', promo_ends_at - interval '1 hour')`
  → 49.90 and `(…, promo_ends_at + interval '1 second')` → 94.90.

---

## 3. Phase 1 — DB + backend for the offer

### 3.1 Migration 183 `183_profession_intro_offer.sql`

1. **`intro_offer_campaign`** — singleton (`id boolean PK DEFAULT true CHECK
   (id)`), `enabled boolean NOT NULL DEFAULT false`, `ends_at timestamptz NOT
   NULL`. Seed `(true, false, '2026-12-31T20:59:59Z')`. **Ships disabled**; the
   launch is a one-row UPDATE (§6).
2. **`intro_offer_prices`** — `profession_group text PK`, `first_month_sar
   numeric(10,2) NOT NULL CHECK (first_month_sar >= 1.00)`. Seed legal 1.99,
   specialist 9.90, entrepreneur 9.90. Changing a price later = one UPDATE.
3. Both tables: RLS ON, no policies, REVOKE ALL from anon/authenticated, GRANT
   ALL to service_role (138's pattern).
4. **`payment_transactions.intro_offer_group text NULL`** — stamped at checkout
   on an offer-priced row. Non-NULL ⇒ the row is the offer's first month ⇒
   **non-refundable**. Renewal rows never carry it. Alongside it
   **`intro_offer_terms jsonb NULL`** (`{first_month_sar, then_sar, then_months,
   list_sar}`), stamped at checkout so the `v3-intro` disclosure (§3.3) is
   rebuilt from the row, never from a catalog that may have moved since.
5. **`intro_offer_quote(p_user_id uuid) RETURNS TABLE(profession_group text,
   first_month_sar numeric)`** — zero rows = not eligible. SECURITY DEFINER,
   `search_path = public`, EXECUTE service_role only. Implements §1 rule 3:
   - campaign row enabled AND `now() < ends_at` AND `early_adopter_open()`;
   - `users.profession_group` joins `intro_offer_prices`;
   - `user_subscriptions_live.effective_plan_id` IS NULL OR = 'free' (the view
     already folds expiry → free; reuse it, never re-derive expiry);
   - NOT EXISTS `payment_transactions` with `plan_id IN ('pro','max')` AND
     `status IN ('paid','refunded')`;
   - NOT EXISTS a `cancelled` seat (implied by the line above; kept explicit).
6. **Forfeiture exemption (§1 rule 14):**
   - new `is_intro_offer_member(p_user_id uuid) RETURNS boolean`: EXISTS a
     `payment_transactions` row with `intro_offer_group IS NOT NULL AND status =
     'paid'`. SECURITY DEFINER, service_role only.
   - `effective_plan_price` branch (a): a `cancelled` seat forfeits only
     `WHEN NOT is_intro_offer_member(p_user_id)`. CREATE OR REPLACE on 182's
     4-arg signature (no signature change, so no DROP is needed here).
   - `claim_early_adopter_seat`: same condition on its `'forfeited'` early
     return. A returning member then claims a NEW seat, and its
     `promo_ends_at = paid_at + 90d` gives the fresh window with no extra logic.
     Capacity is unaffected: the cancelled seat is already released, so it no
     longer counts.
   - The cancel → undo → restore path is unchanged.
7. `product_docs.pricing` — hedged append, same rule as 140: no amounts, no
   end date. One sentence: an in-app first-month offer exists for some
   professions and appears to eligible users inside the app.

### 3.2 Backend — `payment_service.py`

- `_intro_offer_quote(supabase, user_id) -> Optional[tuple[str, Decimal]]` —
  **fail-closed**: any error → `None` (the user then sees the ordinary price;
  never advertise a price the checkout can't honour).
- `create_checkout`: when `plan_id == "pro"` and the quote is non-None →
  `price = first_month_sar`, `payload["intro_offer_group"] = group`. Credit is
  0 by construction (eligible users hold no paid term). Response adds:
  - `intro_offer`: `{"profession_group", "first_month_sar", "then_sar":
    <pro promo>, "then_months": 2, "list_sar": <pro list>}` or `null`;
  - `non_refundable: bool`.
- **Seat claim needs no change**: `claim_early_adopter_seat` only rejects when
  `amount + credit > promo + 0.01` — 1.99/9.90 pass, `promo_ends_at = paid_at +
  90d` as for any seat.
- `_revalidate_credited_charge` needs no change (credit 0 ⇒ `no_credit`).
- `_upgrade_credit` — **NO CHANGE** (§1 rule 11).
- `_TXN_COLUMNS` += `intro_offer_group`. ⚠ selecting it before 183 is applied
  42703s `/history` and checkout — migration strictly first.
- `transaction_summary`: `refundable = False` when `intro_offer_group`; new
  field `non_refundable: bool`.
- `refund_payment`: guard before the window check → 409, new
  `ErrorCode.PAYMENT_NOT_REFUNDABLE` (`backend/app/errors.py`), detail
  «اشتراك الشهر الأول بسعر العرض غير قابل للاسترداد». Webhook-initiated
  refunds (dashboard) are unaffected — an operator can still refund manually.
- New route **`GET /payments/intro-offer`** (authed) →
  `{"eligible": bool, "first_month_sar": str|null, "then_sar": str|null,
  "then_months": 2, "list_sar": str}`. Never `ends_at`, never seats.

### 3.3 Recurring disclosure for offer rows

The hashed disclosure (v2) carries no amount and leans on the `/pay` layout
(`payment_method_service.py:221-228`). A 1.99 → 49.90 step-up on a saved card is
exactly the dispute that leaves weak. For **offer rows only**:
- `recurring_disclosure_ar(plan, intro=…)` appends a fixed schedule sentence,
  built from the payment ROW's stamped values (never from live catalog), e.g.
  «يُخصم 1.99 ر.س اليوم، ثم 49.90 ر.س بعد 30 يوماً و49.90 ر.س بعد 60 يوماً، ثم
  94.90 ر.س كل 30 يوماً.»;
- version tag `v3-intro` for those consents; v2 stays for everyone else;
- `record_recurring_consent` and `capture_payment_method` must rebuild the SAME
  string from the row — one builder, both callers.
This resolves the early-adopter §5 TODO for offer rows only; ordinary seat
purchases stay on v2 (open owner decision, unchanged).

### 3.4 Analytics
`analytics_service.py` allowlist + `events.ts` union: `intro_offer_shown`
(`{trigger: "profession"|"quota", group}`), `intro_offer_cta`. Redemption is
measured from `payment_transactions.intro_offer_group`, not events.

### 3.5 Tests (Phase 1)
`test_payments.py`: eligible checkout → 1.99 / 9.90 + `intro_offer_group`
stamped; ineligible (individual, prior pro, active basic, campaign off, past
`ends_at`, seats full) → normal 49.90/94.90; `max` checkout never discounted;
quote RPC failure → normal price; summary `refundable=False`; refund → 409.
`test_early_adopters.py`: a 1.99 row claims a seat; intro member with a
`cancelled` seat → purchase price 49.90 + new seat with fresh `promo_ends_at`;
non-member with a `cancelled` seat → list price + `'forfeited'` (unchanged). `test_subscription_renewal.py`:
offer holder renews 49.90, 49.90, then 94.90. Remember `.gitignore` blankets
`backend/tests/*` — force-add new test files.

---

## 4. Phase 2 — frontend

### 4.1 Data
- `types/index.ts`: `IntroOfferState`; `PaymentCheckoutResponse.intro_offer`
  + `non_refundable`; `PaymentHistoryItem.non_refundable`.
- `lib/api.ts`: `paymentsApi.introOffer()`.
- New `stores/intro-offer-store.ts`: `{eligible, offer, isOpen, refresh(),
  open(trigger), close()}`. Default **not eligible** (fail-closed). `refresh()`
  on auth, after a profession save the server confirmed, and after a purchase.
- ⚠ **Never** put 1.99/9.90 into `PRICING_PLANS.promoPrice` —
  `pricingPlansAbove`/`cheapestPricingPlan` rank by it and Pro would drop below
  Basic and vanish from the Settings ladder (`pricing.ts:228-234`).

### 4.2 `components/promo/IntroOfferDialog.tsx` (new)
Arabic copy (draft — owner to tune), group-aware headline:
- legal: «عرض خاص للقانونيين — باقة «الاحترافية» بـ 1.99 ر.س للشهر الأول»
- specialist/entrepreneur: «عرض خاص — باقة «الاحترافية» بـ 9.90 ر.س للشهر الأول»
  (Pro's `name_ar` is «الاحترافية» — corrected 2026-10-21)
- body: schedule line («ثم 49.90 ر.س للشهرين الثاني والثالث، ثم 94.90 ر.س
  شهرياً»), Pro feature bullets from `PRICING_PLANS`, «المقاعد محدودة»,
  `<PromoTermsLink/>`, «الشهر الأول غير قابل للاسترداد», and **«تقدر تلغي
  اشتراكك أي وقت من خلال إعدادات الحساب»** (owner, 2026-10-21 — verbatim; the
  cancel flow lives in `AccountSettingsDialog`, so the pointer is accurate).
- CTA «اشترك الآن» → `/pay/pro`; secondary «لاحقاً».
- Latin digits only. No end date, no seat count.

### 4.3 Trigger A — after the profession is saved
- `auth-store.saveProfession` returns its promise (resolves `true` only when the
  PATCH succeeded — a swallowed failure leaves the DB NULL, and the server would
  then say "not eligible").
- `OnboardingDialog.finish()` awaits it; on success with an offer group →
  `introOfferStore.refresh()` → `open("profession")` if eligible. Applies to
  A1 (signup), the declined re-ask, and «تغيير المهنة».

### 4.4 Trigger B — every quota block
- `QuotaBanner` already auto-opens `QuotaUpgradeDialog` for free users on every
  new block (`QuotaBanner.tsx:46-48, 97-103`). When `introOfferStore.eligible`,
  `QuotaUpgradeDialog` renders the offer as a featured block on the Pro card
  (offer price + schedule + non-refundable line); the card list and ranking are
  otherwise unchanged. Emit `intro_offer_shown {trigger:"quota"}`.
- Paid users never reach it (they are not eligible anyway).

### 4.5 `/pay/[plan]` (`app/pay/[plan]/page.tsx`)
When `session.intro_offer`: «سعر الباقة» shows list 94.90 struck through and a
line «سعر العرض للشهر الأول»; under «المبلغ المستحق» a schedule block (same
numbers as the disclosure) and «الشهر الأول غير قابل للاسترداد». The header uses
the offer's billing note, not the catalog one. Disclosure text comes from the
server as today.

### 4.6 Refund UI — `components/Settings/PaymentHistoryDialog.tsx`
`non_refundable` rows: no «طلب استرداد» button; a muted «غير قابل للاسترداد —
عرض الشهر الأول». `isRefundable()` must respect the server's `false` (it already
prefers the server boolean — keep it that way).

### 4.7 Retire the 4-step tour
- `OnboardingDialog.tsx`: drop the A2 branch (`justPaid && !onboardingSeen`),
  `FULL_STEPS`, `withinPurchaseWindow`, the `wasFullTour` → `markOnboardingSeen`
  path. Profession mode is the only mode.
- Delete `steps/StepAgents.tsx`, `steps/StepWorkspace.tsx`,
  `steps/StepQuestions.tsx` and their copy in `onboarding-content.ts` (keep
  `PROFESSION_GROUPS`, `STEP_PROFESSION`). Keep `chat-store.composerInjection`
  — other features use it.
- `onboarding-store.ts`: mode type collapses to `"profession"`.
- `preferences-store.markOnboardingSeen` — remove if no caller remains; leave the
  `onboarding_seen` JSONB key alone (no migration).
- Backend `paid_activated_at` on `/auth/me` stays (additive, mobile-safe); only
  the comment at `subscription_service.py:318` is updated.
- `SidebarFooter.tsx:373-383`: «جولة التعريف السريعة» → **«تغيير المهنة»**,
  calls `open("profession")`; testid renamed `sidebar-settings-profession-trigger`.

### 4.8 One re-ask for `declined`
- New preference flag `profession_reask_done` — **fail-closed `true`** in all
  three places (initial state, `reset()`, hydrate `catch`), flat key,
  `markProfessionReaskDone()` mirrors `markPromoCodePopupSeen`.
- Gate in `OnboardingDialog`: `profession_group === "declined" &&
  !professionReaskDone` → `open("profession")`. ANY dismissal of that run
  writes the flag (a pick also saves the group). Only exact `"declined"` counts —
  never `"unknown"`.

### 4.9 Stacking
- `TourOverlay.useTourAutoStart` waits on onboarding + `professionPending` only;
  add `introOfferStore.isOpen` to its wait condition. edu-store / install nudge
  already defer to any `[role="dialog"]`.

### 4.10 Cancel dialog — `components/Settings/AccountSettingsDialog.tsx`
The early-adopter warning (l.976-1004, «تفقد سعر المشتركين الأوائل نهائيًا»)
is wrong for intro-offer members. `GET /payments/subscription` gains
`early_adopter.returnable: bool` (= `is_intro_offer_member`), and when it is true
the warning becomes: «إذا ألغيت ثم رجعت لاحقاً، تقدر تشترك بسعر 49.90 ما دام
عرض المشتركين الأوائل متاحاً». Every other member keeps today's wording.

### 4.11 `/promo-terms`
`content/legal/promo-terms-ar.md`: new section «عرض الشهر الأول». It covers:
- who qualifies: a declared profession, not subscribed, never paid Pro/Max;
- the first-month price by profession;
- months 2–3 at the early-adopter price, then list price;
- the first month is non-refundable;
- the offer ends with the campaign;
- **coming back after a cancellation**: 49.90 with a fresh 90 days while the
  early-adopter campaign is open. This is an explicit exception to §3.1 for
  this offer only.

Bump «آخر تحديث». Not in the consent bundle → no `LEGAL_VERSION` bump.

---

## 5. Files

| Area | File | Change |
|---|---|---|
| DB | `shared/db/migrations/182_early_adopter_500_and_period_boundary.sql` | new |
| DB | `shared/db/migrations/183_profession_intro_offer.sql` | new |
| BE | `backend/app/services/payment_service.py` | `_effective_price(at)`, quote, checkout, summary, refund guard, route helper |
| BE | `backend/app/services/renewal_service.py` | pass `at=expires_at` |
| BE | `backend/app/services/payment_method_service.py` | offer disclosure builder + `v3-intro` |
| BE | `backend/app/api/payments.py` | `GET /payments/intro-offer` |
| BE | `backend/app/errors.py` | `PAYMENT_NOT_REFUNDABLE` |
| BE | `backend/app/services/analytics_service.py` | 2 events |
| BE | `backend/tests/test_payments.py`, `test_early_adopters.py`, `test_subscription_renewal.py` | cases above |
| FE | `types/index.ts`, `lib/api.ts`, `stores/intro-offer-store.ts` (new) | data |
| FE | `components/promo/IntroOfferDialog.tsx` (new), mount in `ChatLayoutClient.tsx` | dialog |
| FE | `components/onboarding/OnboardingDialog.tsx`, `onboarding-store.ts`, `onboarding-content.ts`, `steps/*` | tour retirement, re-ask, trigger A |
| FE | `stores/auth-store.ts`, `stores/preferences-store.ts` | save promise, reask flag |
| FE | `components/chat/QuotaUpgradeDialog.tsx` | trigger B |
| FE | `app/pay/[plan]/page.tsx` | offer summary + schedule |
| FE | `components/Settings/PaymentHistoryDialog.tsx` | non-refundable rows |
| FE | `components/sidebar/SidebarFooter.tsx` | «تغيير المهنة» |
| FE | `components/tour/TourOverlay.tsx` | wait on offer dialog |
| FE | `components/Settings/AccountSettingsDialog.tsx` | cancel warning for intro members |
| BE | `backend/app/services/subscription_service.py` | `early_adopter.returnable` on `/payments/subscription` |
| FE | `lib/analytics/events.ts` | 2 events |
| FE | `content/legal/promo-terms-ar.md` | new section |

---

## 6. Order of operations

Phases 0–2 are built together in one tree (2026-10-21), so they ship as ONE
backend deploy. The Phase-1 backend selects `intro_offer_group`, so 183 must
be applied before that deploy as well as 182. Hence:

1. Apply **182**, then verify the live SQL boundary checks (§2.3).
2. Apply **183** (campaign `enabled=false`, so it is inert).
3. Push the **backend**. With the flag off every quote is empty, so for users
   the only change is the day-90 price fix. Must land before 2026-11-14.
4. Push the **frontend**. Still inert (the store says not eligible), apart from
   the tour retirement, «تغيير المهنة» and the one declined re-ask, which go
   live with this deploy.
5. **Smoke on prod with the flag ON for a test account only** — flip
   `enabled=true`, immediately buy with a fresh `legal` test account (real
   1.99, non-refundable by design), verify:
   `payment_transactions.intro_offer_group='legal'`, `amount_sar=1.99`, seat
   row exists with `promo_ends_at = paid_at + 90d`, `payment_methods` token
   active, consent hash with `v3-intro`, refund button absent + API 409,
   `effective_plan_price(u,'pro','current', expires_at)` → 49.90 and at
   `expires_at + 60d` → 94.90 (SQL only — no real renewal charge needed).
   Also: a `specialist` account sees 9.90; an `individual` sees nothing; a
   Basic-only past buyer sees the offer.
6. **Launch:** leave `enabled=true`. It stops by itself at `ends_at`.

**Off switch:** `UPDATE intro_offer_campaign SET enabled = false;` — stops new
quotes; existing offer holders keep months 2–3 via their seat.

---

## 7. Watch queries

```sql
-- uptake by profession
SELECT intro_offer_group, count(*) FILTER (WHERE status='paid') paid
FROM payment_transactions WHERE intro_offer_group IS NOT NULL GROUP BY 1;
-- conversion to month 2
SELECT count(*) FROM payment_transactions t
WHERE t.initiated_by='renewal' AND t.status='paid'
  AND EXISTS (SELECT 1 FROM payment_transactions o
              WHERE o.user_id=t.user_id AND o.intro_offer_group IS NOT NULL);
-- seats (never shown to users)
SELECT count(*) FILTER (WHERE released_at IS NULL) live,
       count(*) FILTER (WHERE over_capacity) over_cap FROM early_adopter_seats;
```

Related memory: `project_early_adopters_campaign` (update to 500 seats + the
period-boundary fix when Phase 0 ships), `project_onboarding_tour` (tour retired),
`project_onboarding_profession_step`.
