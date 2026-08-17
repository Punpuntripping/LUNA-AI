# المشتركون الأوائل — Early Adopters

**Status:** PLANNED · not built · nothing applied
**Owner decisions locked:** 2026-08-16 (see §1)
**Next migration number:** `138` (137 is the highest applied)

A launch campaign: the first **100 users to pay for `pro` or `max`** become
المشتركون الأوائل and hold a promotional price for **90 days**. While seats
remain, `basic` is discounted for everyone. When the 100th seat fills, `basic`
reverts and no new seats are issued — but everyone already inside keeps their
price for the rest of their 90 days.

| Plan | List | Promo | Who | Until |
|---|---|---|---|---|
| basic | 49.90 | **39.90** | anyone, while seats remain | campaign closes |
| pro | 89.90 | **49.90** | seat holders | 90 days from seat claim |
| max | 189.90 | **99.90** | seat holders | 90 days from seat claim |

---

## 1. The rules, as decided

1. **A seat is claimed** when a `pro`/`max` payment reaches `paid` **and is
   fulfilled**. Not at checkout — the ledger currently holds 8 `expired`/
   `initiated` rows against 5 paid ones, and abandoned quotes must not burn
   seats.
2. **The count starts at zero on launch day.** The three existing payers are not
   enrolled and are not repriced. (One of them renewed at 89.90 on 2026-08-16.)
3. **The promo is a 90-day window** anchored at the claiming payment's `paid_at`,
   not a count of charges. Any charge whose period begins inside the window is
   priced at the promo rate; the first one that begins after it is full price.
   Wall-clock — a gap in the subscription burns promo days rather than pausing
   them.
4. **The promise travels with the subscription.** Closing the campaign does not
   reprice anyone already holding a seat.
5. **A refund releases the seat** and voids the status. They may buy back in if
   seats remain.
6. **Cancelling forfeits the price permanently.** Cancel and let it stand → the
   seat is released and the promo is dead; coming back later is full price even
   if seats are open. **Undo restores the seat unconditionally.** This must be
   stated in the cancel dialog.
7. **An involuntary lapse does not release the seat** — a declined card in
   dunning is not a decision. The 90-day clock keeps running through it.
8. **Upgrade carries over.** pro → max mid-window pays max's promo price for the
   remaining days. No reset, no new window.
9. **`basic` has no enrolment and no per-user cap.** 39.90 for anybody, every
   purchase, while seats remain. Back to 49.90 the moment the 100th fills —
   including for people who bought during the campaign.
10. **The remaining count is never disclosed.** Not on the page, not in the API,
    not in an error message. The only scarcity signal is «المقاعد محدودة».

---

## 2. Why this is not a price edit

`plans.price_sar` is a single column and **three** things read it:

| Reader | Where |
|---|---|
| checkout | `payment_service.create_checkout` → `price = q2(plan["price_sar"])` (`payment_service.py:1275`) |
| **the renewal job, every 30 days, re-read at charge time** | `renewal_service.py:395` — "the catalog is the price" |
| the upgrade credit, for the **old** plan | `_upgrade_credit` (`payment_service.py:1230`) |

So `UPDATE plans SET price_sar = 49.90` breaks in both directions:

- while the campaign runs, payer #101 also pays 49.90 — the catalog cannot see
  who is asking;
- when it ends and the column goes back to 89.90, **every early adopter's next
  renewal jumps to 89.90 on a saved card**. The 3-month promise dies the day the
  campaign closes, as an automatic charge, with no warning.

The price therefore has to become a function of *(user, plan, now)* — one
definition, consumed by all three readers.

---

## 3. Data model (migration `138_early_adopters.sql`)

### 3.1 `plans.promo_price_sar`

```sql
ALTER TABLE public.plans ADD COLUMN promo_price_sar NUMERIC(10,2);
UPDATE public.plans SET promo_price_sar = 39.90 WHERE plan_id = 'basic';
UPDATE public.plans SET promo_price_sar = 49.90 WHERE plan_id = 'pro';
UPDATE public.plans SET promo_price_sar = 99.90 WHERE plan_id = 'max';
```

The catalog stays the price authority — same posture as `price_sar`, tunable by
a plain UPDATE, no code change. `NULL` = this plan has no promo.

### 3.2 `early_adopter_seats`

```sql
CREATE TABLE public.early_adopter_seats (
    seat_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(user_id) ON DELETE SET NULL,
    payment_id      uuid UNIQUE REFERENCES public.payment_transactions(payment_id),
    claimed_at      timestamptz NOT NULL DEFAULT now(),
    promo_ends_at   timestamptz NOT NULL,          -- claimed_at + 90 days
    released_at     timestamptz,
    release_reason  text CHECK (release_reason IN ('refund','cancelled')),
    over_capacity   boolean NOT NULL DEFAULT false
);

-- ONE live seat per user. The claim is the index, not a count.
CREATE UNIQUE INDEX early_adopter_seats_one_live
    ON public.early_adopter_seats (user_id) WHERE released_at IS NULL;
```

- **`payment_id UNIQUE` is the webhook/verify idempotency key** — the same
  mechanism `fulfilled_at` gives `grant_plan`. The double-run cannot double-claim.
- **No `seat_no` column.** Seats release and are re-issued; a number would be
  either wrong or a second thing to reconcile. Capacity is
  `count(*) WHERE released_at IS NULL`.
- **RLS: deny-all.** Service-role only, like `payment_methods` (132). Nothing
  client-side may read this table — it *is* the remaining count.
- `ON DELETE SET NULL` on `user_id`, matching 117's retention posture: a purged
  account leaves the seat record standing.

### 3.3 The capacity constant

```sql
CREATE TABLE public.early_adopter_campaign (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    seat_limit  integer NOT NULL DEFAULT 100,
    promo_days  integer NOT NULL DEFAULT 90,
    enabled     boolean NOT NULL DEFAULT false
);
INSERT INTO public.early_adopter_campaign DEFAULT VALUES;
```

A one-row singleton so the campaign can be **turned on by an UPDATE, not a
deploy** — the same discipline as `SUBSCRIPTION_AUTO_RENEWAL_ENABLED`. Ship the
code with `enabled = false` and it changes behaviour for zero users; flip it when
the marketing is ready. It also gives you a kill switch that does not need a
rollback.

### 3.4 Functions

```sql
-- Is the campaign taking new seats? Never exposed as a number.
early_adopter_open() RETURNS boolean

-- The ONE price definition. Consumed by checkout, renewal and the upgrade credit.
effective_plan_price(p_user_id uuid, p_plan_id text) RETURNS numeric

-- Claim. Called AFTER grant_plan returns, never inside it.
claim_early_adopter_seat(p_user_id uuid, p_payment_id uuid) RETURNS TABLE(...)

-- Release. reason IN ('refund','cancelled').
release_early_adopter_seat(p_user_id uuid, p_reason text) RETURNS boolean

-- Undo a cancellation-release. Restores the caller's own most recent seat.
restore_early_adopter_seat(p_user_id uuid) RETURNS boolean

-- Authed read for the user's own state. No count, ever.
early_adopter_status(p_user_id uuid) RETURNS TABLE(campaign_open boolean,
                                                   has_seat boolean,
                                                   promo_ends_at timestamptz)
```

`effective_plan_price` resolution order:

1. plan has no `promo_price_sar`, or campaign row `enabled = false` → `price_sar`
2. `basic` → `promo_price_sar` **iff** `early_adopter_open()`
3. `pro`/`max` → `promo_price_sar` **iff** the user holds a live seat with
   `promo_ends_at > now()` **OR** `early_adopter_open()` (this is the purchase
   that will claim the seat)
4. otherwise `price_sar`

All EXECUTE **revoked from `anon`/`authenticated`**, granted to `service_role`
only — the 118 lockdown posture. `early_adopter_open()` is surfaced to the public
through a backend endpoint, never through PostgREST.

### 3.5 The 100th-seat race

Two payments settling in the same second must not both become #100.
`claim_early_adopter_seat` takes `pg_advisory_xact_lock(hashtext('early_adopter_seats'))`
before counting. Claims happen ~100 times in the campaign's life, so serialising
them costs nothing.

**Deliberate over-capacity, and it is the right failure.** A quote priced at
49.90 while seats were open can settle after the campaign closed. The alternatives
are refusing a payment that already succeeded, or charging someone the full price
after quoting them the promo. Instead the seat is granted anyway and stamped
`over_capacity = true`. Bounded by the number of open quotes at closing time
(realistically 0–3), visible in one query, and it fails toward the customer.

---

## 4. Backend

| File | Change |
|---|---|
| `backend/app/services/payment_service.py` | `create_checkout` prices via `effective_plan_price` instead of `plan["price_sar"]`; `_upgrade_credit` prices the **old** plan the same way; `_revalidate_credited_charge` follows automatically (it re-runs `_upgrade_credit`); call `claim_early_adopter_seat` after `grant_plan` succeeds; call `release_early_adopter_seat(..., 'refund')` on the refund path |
| `backend/app/services/renewal_service.py` | `price = ps.q2(plan["price_sar"])` → `effective_plan_price(user_id, plan_id)`. **This is the line that keeps the 90-day promise.** |
| `backend/app/services/subscription_service.py` | `cancel_renewal` → `release_early_adopter_seat(..., 'cancelled')`; `reactivate_renewal` → `restore_early_adopter_seat`. Both best-effort **after** the flag write, following `clear_renewal_cancellation`'s posture: the flag is the cancellation and a seat-bookkeeping failure must never surface as a failed cancel. Add `early_adopter` to the state payload so the dialog can render the warning |
| `backend/app/api/payments.py` | new `GET /payments/early-adopter` (**public, unauthenticated**) → `{"open": bool, "promo": {"basic": "39.90", "pro": "49.90", "max": "99.90"}}`. Server-side cached ~30 s. **Returns no count and no seat total.** Authed `GET /payments/subscription` gains `early_adopter: {is_member, promo_ends_at}` |

### The two traps

**`_upgrade_credit` is the money bug.** It prices the old plan from the catalog.
An early adopter who paid 49.90 for pro and upgrades to max would be credited
**89.90** — more than they ever paid. Routing it through `effective_plan_price`
fixes it and self-corrects after day 90 (by then they are genuinely paying 89.90).
This is the same class as H-4 in `security_review_2026-08-07.md`, arriving
through a new door.

**The seat claim goes beside `grant_plan`, never inside it.** 113/119/120 all
established this and `clear_renewal_cancellation` documents it: the live
money-path RPC is not edited for a side concern. It also keeps us clear of
`trg_user_subscriptions_assignment` (`BEFORE UPDATE OF plan_id`) — a separate
table cannot accidentally re-stamp anyone's term.

---

## 5. The disclosure problem — needs your call

Disclosure v2 (deployed yesterday, `9d9ef1a`) deliberately keeps the **amount out
of the hashed consent string** and carries it in the /pay layout instead.

A promotional price **steps up**. Someone consents at a screen showing 49.90 and
is charged 89.90 on day 91, on a saved card. That is precisely the dispute the
consent artefact exists to settle, and today the artefact would not contain the
fact that a step-up was coming.

**Recommendation: bump `DISCLOSURE_VERSION` v2 → v3** for seat-holding pro/max
purchases only, with the step-up inside the hashed text —
«يبدأ الاشتراك بسعر المشتركين الأوائل لأول ٩٠ يوماً ثم يعود إلى السعر المعتاد».
Bumping rather than editing preserves v2 consents as evidence of what those users
saw, exactly as v1 was preserved. The *amount* can stay out; the *fact of the
step-up* should not.

This reverses part of a decision made one day ago, so it is flagged, not assumed.
The alternative — layout-only — is defensible if the /pay order summary states
both numbers, but it leaves nothing hashed.

**Also recommended:** an email ~7 days before the step-up charge. Currently
**blocked** — receipt email is still down on the 465/SSL issue, so this cannot
ship in the same wave. Note it as owed.

---

## 6. Frontend

`frontend/lib/pricing.ts` is hardcoded display copy and the DB is authoritative
for the charge — that split stays. Each plan gains a `promoPrice` and a
`promoBillingNote`; a campaign flag chooses which renders.

| File | Change |
|---|---|
| `frontend/lib/pricing.ts` | `promoPrice` per plan (Arabic-Indic: «٣٩٫٩٠» / «٤٩٫٩٠» / «٩٩٫٩٠»); `promoBillingNote` for pro/max stating the step-up; `EARLY_ADOPTER_LABEL` = «المشتركون الأوائل»; `SEATS_LIMITED_NOTE` = «المقاعد محدودة». `cheapestPricingPlan()` and `pricingPlansAbove()` must compare **effective** prices or the «ابتداءً من» line and the settings ladder will quote list prices during the campaign |
| `frontend/components/pricing/PlanPrice.tsx` | optional `listPrice` → renders the struck-through original beside the promo price. One component, so /pricing and the dialog cannot disagree |
| `frontend/app/pricing/page.tsx` | fetch campaign state; `export const revalidate = 60` (see §6.1); «المقاعد محدودة» badge on the cards |
| `frontend/components/landing/PricingSection.tsx` | same treatment — it shares `PlanPrice` |
| `frontend/components/chat/QuotaUpgradeDialog.tsx` | promo prices + «المقاعد محدودة». This is the surface you asked for explicitly |
| `frontend/components/Settings/AccountSettingsDialog.tsx` | **the cancellation warning** (§6.2) |
| `frontend/stores/early-adopter-store.ts` | new — one fetch per session, shared by dialog and settings |
| `frontend/types/index.ts` | `EarlyAdopterState` |

### 6.1 The stale-price window

`/pricing` is statically rendered today (no `dynamic`/`revalidate` export). A card
showing 39.90 while checkout charges 49.90 is the worst possible mismatch — it
happens at the moment of payment.

`revalidate = 60` bounds it to a minute, plus an explicit ISR purge when the
campaign closes. **Purge paths must be percent-encoded** or the 200 is a lie
(`project_isr_revalidate_percent_encoding`). `/pay` always re-quotes server-side,
so the authoritative number is never stale.

### 6.2 The cancellation warning

In the cancel flow, for a seat holder, before the confirm:

> أنت من **المشتركين الأوائل**. إذا اكتمل الإلغاء فسيعود سعر باقتك إلى السعر
> المعتاد، ولن يمكن استعادة سعر المشتركين الأوائل لاحقاً. يمكنك التراجع عن
> الإلغاء قبل انتهاء اشتراكك في {expires_at}.

Worth naming plainly: this is a retention lever pointed at someone in the act of
leaving. It is honest — the rule is real and they are told before they act — and
it will be the most consequential sentence on that screen. It should read as a
fact, not as a threat, and the undo deadline must be in it.

---

## 7. Copy elsewhere

- **`/terms` §5** — a promotional-pricing clause: who qualifies, the 90 days, the
  step-up, and that cancelling forfeits it. The forfeiture rule especially cannot
  live only in a dialog.
- **`product_docs.pricing`** — the router answers questions about ريحان from
  these rows and **no doc currently states a price** (deliberate). Decide whether
  the router should be able to say «هناك عرض للمشتركين الأوائل، والمقاعد محدودة»
  without quoting numbers. Recommended: yes, qualitatively, no numbers — that
  keeps the existing no-prices-in-docs rule intact.
- **Never anywhere:** a remaining count, a seat total, a closing date, or an
  error message that says the campaign is full. After close, a user simply sees
  the list price.

---

## 8. Tests

- `backend/tests/test_payments.py` — checkout prices at promo/list either side of
  the campaign; upgrade credit uses the paid price; refund releases the seat;
  double-claim via webhook+verify is idempotent on `payment_id`.
- `backend/tests/test_subscription_renewal.py` — **the load-bearing one**:
  renewal inside the window charges 49.90, renewal after day 90 charges 89.90,
  and closing the campaign does not reprice a live seat holder.
- New: `backend/tests/test_early_adopters.py` — capacity, the advisory-lock race,
  cancel→release→undo→restore, over-capacity stamping.
- ⚠ `.gitignore` line 19 blankets `backend/tests/*`. **Force-add** any new test
  file or it will never be committed — this already bit `test_subscription_renewal.py`.
- `FakeSupabase` tests cannot catch column drift (that is how the dropped `status`
  column reached prod). Verify the new columns against live Supabase before deploy.

---

## 9. Deploy order

**Migration 138 first, then push.** Railway is GitHub-linked to master, so *the
push is the deploy*, and the new backend names columns and functions that must
already exist.

The gap here is **safe in the forward direction**, unlike 129: with 138 applied
and the old backend still live, everything reads `price_sar` and the campaign is
simply not running yet. Nothing is over- or under-charged.

Ship with `early_adopter_campaign.enabled = false`. The deploy then changes
behaviour for **zero users**, and the campaign starts with a one-row UPDATE once
the pricing page copy is approved — a flip you can also reverse instantly.

Sequence:

1. Apply `138_early_adopters.sql` (verify: RLS on `early_adopter_seats`, 0 client
   grants, EXECUTE revoked from anon/authenticated, `enabled = false`).
2. Push → backend + frontend deploy.
3. Verify `GET /payments/early-adopter` → `{"open": false, ...}`, `/pricing`
   still shows list prices.
4. Buy `pro` on a spare account, confirm full price still charged.
5. `UPDATE early_adopter_campaign SET enabled = true;` → purge `/pricing` and
   `/` (percent-encoded).
6. Re-verify the same purchase now quotes 49.90 and writes an
   `early_adopter_seats` row.

**Closing the campaign is automatic** — seat 100 fills, `early_adopter_open()`
goes false. Only the ISR purge is manual; add it to the runbook, because the
public page will otherwise show 39.90 for up to 60 s after close.

---

## 10. Accepted exposure

- **Over-capacity** by the number of open quotes at closing (0–3 realistically),
  each worth ≤ 90 SAR of discount. Bought deliberately in exchange for never
  repricing a completed payment. Query: `WHERE over_capacity`.
- **Refund-and-rebuy churn.** A refund frees the seat and the user may rebuy —
  a 24 h loop that resets the 90-day clock. Bounded by the refund fee (provider
  fee + 1.15 + 0.50 per cycle, ~3.40 out of pocket each time) and visible in the
  ledger. Not worth machinery until observed.
- **`stamp_payment_prior_snapshot` still reads `user_subscriptions.plan_id` raw,
  with no expiry check** (unfixed since 131 — see `project_free_monthly_window_upgrade_ladder`).
  Nothing in this plan depends on `prior_plan_id`, but anything added later that
  reasons about it must remember it is the raw plan, not the effective one.
- **Grandfathered subscribers** — the three current payers stay at list price by
  decision. If any of them asks, the fix is a manual seat insert, not a rule change.
