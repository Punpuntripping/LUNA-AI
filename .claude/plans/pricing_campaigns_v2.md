# Pricing campaigns v2 — price tiers + seat batches (web now, app in Phase 4)

**Status:** PLANNED 2026-10-27 · nothing built · nothing applied.
**Owner decisions:** 2026-10-26/27 (reflect session). All rules confirmed 2026-10-27; A4/A5 stand as defaults (§7).
**Migrations:** `191_basic_price_39_90.sql` (§8, written 2026-10-27) · `192_promo_price_tiers.sql` (190 is the latest applied, 2026-10-26).
**Read first:** `early_adopters.md`, `profession_intro_offer.md` (this plan supersedes its prices, its
eligibility and its rule 11), `subscription_auto_renewal.md`. App side: `mobile/07_phase4_purchases.md`.
Marks: ✅ decided · ❓ assumed, confirm · 🔎 verify before relying on it.

**Vocabulary** (owner's words): a **price tier** is a price level (Tier 1, Tier 2, regular). A **batch** is a
group of seats sold at one tier's price for a set number of months. A tier can have several batches, which
are sold one after another.

---

## 1. The rules

### 1.1 Price tiers (✅ owner 2026-10-27)
| | Pro | Max | Per user |
|---|---|---|---|
| **Tier 1** | **19.90**, only for the first profession answer «قانوني» / «مختص» / «رائد أعمال» | **149.90**, everyone | **once** per plan |
| **Tier 2** | **59.90**, everyone | **189.90**, everyone | **once** per plan (✅ Q1) |
| **Regular** | 94.90 | 289.90 | — |

Basic: **39.90** flat for 7 days (raised from 29.90, owner 2026-10-27, §8), never in a tier.

### 1.2 Seat batches (✅ owner 2026-10-26/27)
| Batch | Plan | Tier | Price | Months | Seats |
|---|---|---|---|---|---|
| `pro_t1_a` | Pro | 1 | 19.90 | 2 | 500 |
| `pro_t1_b` | Pro | 1 | 19.90 | 1 | 500 |
| `pro_t2_a` | Pro | 2 | 59.90 | 2 | 500 |
| `max_t1_a` | Max | 1 | 149.90 | 4 | 100 |
| `max_t2_a` | Max | 2 | 189.90 | 3 | 400 |
| `max_t2_b` | Max | 2 | 189.90 | 1 | 1,000 |

After a batch's months: the regular price.

### 1.3 Who pays what — the quote, per user and plan
1. **On Pro or Max now** → no new seat; renewals are priced by the seat they hold (§1.4-3). The Pro → Max
   switch is rule 6.
2. **On Basic now** → Tier 1 is **not** offered while Basic runs. When the Basic term ends, the user is shown
   the offer: the existing post-answer / quota-wall popup, triggered by the expiry (✅ A10). Tier 2 and
   regular upgrades from Basic keep today's credit.
3. **Pro:**
   - Tier 1 if the first profession answer is in the 3 groups (✅ A6), the user never used Pro Tier 1, and a
     Tier 1 batch is open;
   - otherwise Tier 2 if the user never used Pro Tier 2 and a Tier 2 batch is open;
   - otherwise regular.

   An offer user whose Tier 1 batches are full falls to Tier 2 (✅ A2).
4. **Max:** Tier 1 if the user never used Max Tier 1 and a batch is open; otherwise Tier 2 if never used
   and a batch is open; otherwise regular.
5. **Past subscribers qualify** (✅ owner 2026-10-27, A8): anyone whose Pro **or Max** subscription stopped,
   including by cancelling, and anyone who has bought Basic before, is quoted by rules 3–4 like everyone
   else. The old "never paid for Pro/Max" and "on the free plan" checks (M183:269-313) are replaced by
   "never used this tier".
6. **Pro → Max switch** (✅ A3, legacy holders included): the switcher gets the Max quote of rule 4 for its
   months from the switch payment. Their Pro seat is released with a new reason `'upgraded'`, which counts
   as having used that Pro tier but is not forfeiture.
7. **"Used a tier"** = ever held a seat at that tier for that plan, live or released — except a seat whose
   purchase was refunded (§1.4-7).
   - Legacy seats map: a legacy Pro seat (49.90, the old plain price) counts as Pro **Tier 2** used; a legacy Max seat (99.90)
     counts as Max **Tier 1** used (✅ Q3).

### 1.4 Seat behaviour
1. **Batches fill in order** within a tier: the active batch is the lowest-lettered enabled batch with free
   seats.
2. **Batches are data.** Price, months, seats and on/off live in a table row, and a change is one `UPDATE`
   with no deploy. The owner changed these numbers 6 times on 2026-10-26/27.
3. **A seat freezes its terms.** Each seat stores its batch, plan, tier, price and window
   (`promo_ends_at = paid_at + 30 × months` days). Editing a batch never re-prices a held seat.
4. **Legacy seats keep their terms.** 10 live seats on 2026-10-26: 6 Pro at 49.90 and 4 Max at 99.90, each
   with 90 days. They stay priced exactly as today, from `plans.promo_price_sar`, which is frozen for them:
   **never edit it again** (the migration comments it and self-tests it).
5. **The quote is the promise.** Checkout stores the batch it quoted on the payment row. The seat is claimed
   in that batch even if the batch filled while the payment settled; that seat is stamped `over_capacity`
   (today's §3.5).
6. **Cancelling loses the seat** (✅ A9): the remaining discounted months are gone, and that tier counts as
   used. A tier the user **never** used stays open to them later (✅ Q2). For example, someone who cancelled a
   59.90 seat can come back as a lawyer at 19.90. This replaces today's permanent forfeiture of all promo
   prices (`/promo-terms` §3.1).
7. **Refunds:** the first payment of a Tier 1 Pro seat is non-refundable (as today); later payments keep the
   24h self-serve refund. A refund releases the seat back to its batch. ✅ owner 2026-10-27: a **refunded
   purchase does NOT count as using its tier** (as if never bought). A refund of a later renewal ends the seat
   but the tier stays used, so it can't be reopened that way.
8. **Upgrade credit = what the user actually paid** (✅, reverses `profession_intro_offer.md` rule 11). The
   credit is the remaining-days ratio × the cash amount of the payment funding the running term, with
   today's one-period cap and 1.00 floor. It applies to every plan.
9. **End dates:** Pro Tier 1 keeps `intro_offer_campaign.ends_at` (2026-12-31 23:59 Riyadh), extended if the
   app launches later. Tier 2 and Max Tier 1 have no end date: they run until seats run out or the switch is
   turned off (❓ A4).
10. **Never shown:** a seat count, a batch, a tier number or an end date. «المقاعد محدودة» stays the only
    scarcity signal.
11. **Stores count in the same batches** (Phase 4). A store purchase becomes a `payment_transactions` row
    (`provider` apple/google) and claims a seat the same way. No store work happens in this plan; nothing
    here may block it.

## 2. Database — migration 192 (`@sql-migration`)

### 2.1 Schema
- **`promo_batches`**: `batch_id text PK` (`pro_t1_a`, …), `plan_id text → plans`, `price_tier smallint
  CHECK in (1,2)`, `batch_order int`, `price_sar numeric(10,2)`, `months int CHECK 1..12`,
  `seat_limit int`, `profession_only bool`, `enabled bool default true`,
  `UNIQUE (plan_id, price_tier, batch_order)`.
  - RLS on, no policies, service_role only (like `early_adopter_campaign`, M138:366-370).
  - Seeded with the §1.2 rows.
- **`early_adopter_seats`** gains `batch_id text NULL → promo_batches`, `plan_id text NULL`,
  `price_tier smallint NULL`, `price_sar numeric NULL`.
  - NULL `batch_id` marks a legacy seat. There is no backfill; §1.3-7 maps legacy seats inside the
    functions.
  - Widen the `release_reason` CHECK (M138:227-228) with `'upgraded'`.
  - The one-live-seat index (M138:344-346) stays. A switch releases the Pro seat before the Max insert, in
    the same transaction.
- **`payment_transactions`** gains `promo_batch_id text NULL`, the batch quoted at checkout.
  - ⚠ `_TXN_COLUMNS` (PS:699-703) breaks checkout and `/history` if the backend ships before the column
    exists. **Migration first, always** (M182:68-74).
- **`early_adopter_campaign`** gains `ladder_v2 boolean NOT NULL DEFAULT false`. It gates only new quotes
  and new claims (§2.2).

### 2.2 Functions
New names avoid overload trouble; existing signatures stay as they are.
- **`promo_tier_used(p_user_id, p_plan_id, p_tier)`**, new: §1.3-7, including the legacy mapping.
- **`promo_active_batch(p_plan_id, p_tier)`**, new: the active batch row or nothing. Live seats are counted
  per `batch_id` under the claim's advisory lock.
- **`promo_quote(p_user_id, p_plan_id)`**, new: returns `(batch_id, price_tier, price_sar, months)` or
  nothing, implementing §1.3. The active-Basic rule reads `user_subscriptions_live`, a `security_invoker`
  view (M129a) — read it, never rebuild it.
- **`effective_plan_price(uuid, text, text, timestamptz)`**: same signature, body-only change (CREATE OR
  REPLACE is safe; M183's single-overload guard stays green).
  - Branch (b), **independent of the flag**, prices a live seat in its window:
    - a batch seat applies only when `seat.plan_id = p_plan_id` and returns `seat.price_sar`;
    - a legacy seat returns `plans.promo_price_sar` (today's rule).

    Batch seats exist only after the flip. After a rollback, held seats keep their price. Gating this
    branch on the flag would charge a 19.90 seat 49.90 in month 2.
  - Branch (c) (`'purchase'`): with `ladder_v2` it returns `promo_quote(...).price_sar`; without the flag,
    today's body.
  - The forfeiture branch (a) changes per §1.4-6 / Q2.
- **`claim_early_adopter_seat(p_payment_id)`**: same signature and RETURNS TABLE.
  - When the payment carries a `promo_batch_id`:
    1. release a live seat of the other plan (`'upgraded'`);
    2. insert the batch seat (`batch_id`, `plan_id`, `price_tier`, `price_sar`, `promo_ends_at = paid_at +
       30 × months`), with `over_capacity` set when the batch's live count ≥ `seat_limit`.
  - The column is set only while `ladder_v2` is on. The claim honours it even if the flag was turned off
    before capture.
  - Idempotent on `payment_id`. The amount gate compares against the batch price, not
    `plans.promo_price_sar`.
- **`early_adopter_open()`**: zero arguments, kept (PS:886 calls it with `{}`). With `ladder_v2` it means
  "some batch is open".
- **`intro_offer_quote`**: with `ladder_v2`, it becomes a thin wrapper over `promo_quote(user, 'pro')` that
  reports eligible when the quote is Pro Tier 1. `intro_offer_prices` stays in place, unused.
- **`early_adopter_status`**: adds the seat's `price_sar` / `plan_id` / `price_tier`. Additive only.

### 2.3 Rules that exist in several copies — change them together
- The forfeiture predicate is in M183:371-377, 519-526 and 671-675, and `intro_offer_quote` (308-312) has
  its own copy. All of them move to §1.4-6.
- `'upgraded'` must never forfeit.
- The Python test fakes copy these rules (TP:258-273, 400-441, 475-565; TSR:365-372, 471-495) and change in
  the same commit. The fakes already diverge from the real SQL on `enabled`; fix that while there.

### 2.4 Self-tests (inside 192, like M182/M183)
- Exactly one overload of each function; grants are service_role only; the seed rows equal §1.2 (this
  checks the seed only — later edits are UPDATEs).
- With the flag on, in a rolled-back txn:
  - a legacy Pro seat prices 49.90 and a legacy Max seat 99.90;
  - a batch seat prices its own price only for its own plan;
  - fill `max_t1_a` → the quote is `max_t2_a`;
  - a user who used Pro Tier 1 is quoted Tier 2;
  - an individual is never quoted Pro Tier 1;
  - active Basic is never quoted Pro Tier 1;
  - a switch releases Pro as `'upgraded'` without forfeiting.

## 3. Backend (`@fastapi-backend`)
- **Checkout** (PS:1695-1703): the price comes from `promo_quote`, and `promo_batch_id` is stamped on the
  row.
  - Offer terms (PS:943, 978-1026): new fields `offer_sar` / `offer_months`.
  - The old keys stay truthful for old clients: `first_month_sar = then_sar = offer_sar`,
    `then_months = offer_months − 1`, `list_sar`.
  - Replace the `first < then ≤ list` sale check (PS:1002) with `offer_sar < list_sar`.
- **`_upgrade_credit`** (PS:1571-1660) + `_revalidate_credited_charge` (PS:2100-2124): base the credit on
  the cash `amount_sar` of the payment funding the running term. The cap and floor are unchanged. The Max
  quote during a switch comes from `promo_quote`.
- **Basic-expiry offer:** when a Basic term ends, an offer-eligible user sees the intro-offer dialog on next
  visit. A flag on the intro-offer state (`reason: "basic_ended"`) is read by the existing
  `IntroOfferDialog` trigger.
- **`renewal_service`**: no call change. It prices at `p_at = expires_at` (RS:427-435), and the seat carries
  its price.
- **Consent disclosure** (PM:236-239), new version `v4-tiers`, built from the months instead of the
  hardcoded «الشهرين الثاني والثالث»:
  - 1 month: «يُخصم {p} ر.س اليوم، ثم {list} ر.س كل 30 يوماً.»
  - 2 months: «… ثم {p} ر.س للشهر الثاني، ثم {list} …».
  - n months: «… لكلٍّ من الأشهر {n−1} التالية …».
  - Wording 🔎 owner. Payments already checked out keep their v3 hash.
- **Public `GET /payments/early-adopter`**: `{open, promo:{pro, max}}` plus `months:{pro, max}`.
  - These carry the **general** quote (Pro Tier 2 for the public page, since Tier 1 is personal). A closed
    plan drops out of `promo`.
  - No counts, no batches, no tier numbers. Update TEA:793 to pin the three keys.
- **`GET /payments/intro-offer`**: adds `offer_sar` / `offer_months` (and `reason`). Update TP:2409.
- Moyasar description «(عرض الشهر الأول)» (PS:1737) becomes «(سعر العرض)».
- **Alert:** a Logfire warning `promo.batch_near_full` at 90% of any batch (store offers get swapped by hand
  in Phase 4).

## 4. Frontend + copy (`@nextjs-frontend`) — every location found 2026-10-27
- **`lib/pricing.ts`** (:147-170): promo price and months come from `/payments/early-adopter`, not from
  constants, because a batch rollover must change the page without a deploy.
  - «لأول 90 يوماً» becomes «لأول شهرين» / «لأول {n} أشهر».
  - Plan-order rule (:230): Basic 39.90 < Pro Tier 2 59.90 < Pro 94.90, and Max 149.90 / 189.90 < 289.90 ✓.
    Pro Tier 1 (19.90) is below Basic, but it is a personal offer and never shown on the public page.
  - 🔎 `/pricing` and the landing `PricingSection` render server-side. Check revalidate ([[isr-bake-trap]]).
- **Offer UI**:
  - `components/promo/intro-offer-content.ts` (:22-70): «للشهر الأول» becomes «لأول شهرين» or «للشهر الأول»
    from `offer_months`; the schedule rows are rebuilt.
  - `IntroOfferSummary`, `IntroOfferDialog` (plus the Basic-ended trigger), `intro-offer-store` validation
    (:37-59), `types/index.ts` (:991-1014).
- **`app/pay/[plan]/page.tsx`** (:331-447): offer rows and schedule. Show the quoted price, not
  `plan.price`, when a tier applies (today it shows list, :340-357).
- **`QuotaUpgradeDialog`** (:278-343) ribbon and «ابتداءً من».
- **`AccountSettingsDialog`** (:1003-1027): the cancel warning becomes «إذا ألغيت تفقد مقعدك وما تبقّى من
  أشهر العرض» per §1.4-6. The return line follows the user's next quote.
- **`PaymentHistoryDialog`** (:138): «عرض الشهر الأول» becomes «سعر العرض».
- **`/promo-terms`** (`content/legal/promo-terms-ar.md`), rewritten around the two tiers:
  - Tier 1 once per plan, Tier 1 Pro only for the 3 professions;
  - Tier 2;
  - batches of seats, each price for its months;
  - cancelling loses the seat;
  - the Basic rule;
  - the first Tier 1 Pro payment non-refundable;
  - also fix :31 (old basic promo), :35, :41, :57, :61, :73-91, and update «آخر تحديث».
- **`product_docs.pricing`**: the M183 section (:705-717) says «على الشهر الأول». The new text says «سعر
  ترويجي لأول شهر أو أكثر حسب العرض»; still no digits (M126). Read the live row, then UPDATE in 192 with an
  md5 guard.
- `api/payments.py` docstrings (:326, :366-369).
- Not touched: the pitch deck, and `/terms` §5 (no amounts; points to /promo-terms).

## 5. Tests
Update or replace (from the 2026-10-27 map):
- **TP:** 799, 1519, 1533 (credit basis), 2180-2410 (offer), catalog TP:73-82 (stale: pro 89.90).
- **TEA:** 301-412 (capacity → per batch), 471-515 (forfeiture → §1.4-6), 600-637 (90-day renewal pricing),
  677-718 (credit), 793-808 (payload), 948-969.
- **TSR:** 2100-2219 (`p_at`, months per batch, v3 → v4 disclosure), catalog TSR:94-103.

New:
- §1.3 quote matrix: profession × used tiers × active Basic × lapsed Pro/Max × batches open/full;
- batch rollover;
- quote-is-the-promise;
- switch releases Pro as `'upgraded'`;
- credit at the cash actually paid;
- legacy seats unchanged;
- the Basic-ended offer trigger;
- no counts in the public payload.

Run the CI durable suites in a clean worktree ([[ci-durable-suites]]). `test_payments` stays out of CI until
the four `claim_x_first_purchase` tests are fixed.

## 6. Rollout
1. Read the live `product_docs.pricing` row and the 10 live seats, so the migration's guards match prod.
2. **Apply 192** with the flag off. Self-tests pass; behaviour is unchanged.
3. **Push** backend + frontend together (both auto-deploy).
   - Until step 5, the copy shows the new terms while the DB charges the old ones.
   - The gaps (old plain Pro: 3 months; old Max: 99.90; old offer: 1.99/9.90) mostly favour the customer.
     Exception: a past subscriber reading the new copy is still refused the offer until the flip, so keep
     the window short.
4. **Pre-flip check:** list open credited quotes (`payment_transactions` with status `initiated` and a non-zero
   `upgrade_credit_sar`, created in the last 24h). After the flip they are re-checked on the cash basis and may
   be held for review, so let them settle or expire first.
5. **Flip** `UPDATE early_adopter_campaign SET ladder_v2 = true;`.
   - A trigger stamps `ladder_v2_at`. From then on, an unstamped payment created after the flip gets no legacy
     seat, while checkouts opened before it keep their quote.
   - Purge `/pricing`, `/` and `/about_us` (they revalidate every 60s anyway).
   - Right after the flip, apply **191** (Basic 39.90; a price raise, so the copy deployed in step 3 goes first).
     Not before the flip: with the flag off, upgrade credit is priced at the catalog, so a 29.90 Basic buyer
     would be credited on 39.90 (review finding 7).
6. **Verify** with SQL `promo_quote` for:
   - an eligible lawyer;
   - an individual;
   - a lapsed Pro lawyer;
   - an active-Basic lawyer;
   - a legacy Pro holder and a legacy Max holder.

   Then check `/payments/early-adopter`, `/pricing`, `/pay/pro` and `/pay/max` on the owner's test account.
   No real charge is needed.
7. **Rollback** = flip the flag off. New buyers get today's terms; held batch seats keep their own price to
   the end of their window.

Day-90 renewals of legacy seats start ≈ 2026-11-15 and are unaffected.

**Commit note:** `backend/tests/promo_ladder_model.py` (the shared SQL model the three payment suites import) is
caught by the `backend/tests/*` line in `.gitignore`, so it needs `git add -f`.

## 7. Open
- **Decided 2026-10-27 (round 2):** Q1 Tier 2 is also once per user · Q2 a cancellation loses only the seat
  and its tier, never-used tiers stay open · Q3 legacy Pro (49.90) = Pro Tier 2 used, legacy Max (99.90) =
  Max Tier 1 used.
- ❓ **A4** End dates: only Pro Tier 1 has one (2026-12-31, extended for the app launch). Tier 2 and Max run
  until seats run out or the switch is turned off.
- ✅ **A5** A refund frees its seat back to its batch, so a full batch can briefly reopen; a refunded purchase
  doesn't use its tier (owner 2026-10-27).
- **Price typo:** the owner wrote 19.99 once more on 2026-10-27. The plan keeps **19.90**, as decided ("1-
  19.9").
- **Decided 2026-10-27:** A1 Pro Tier 2 is 2 months, at **59.90** (changed from 49.90 later the same day) · A2 offer users fall back to Tier 2 ·
  A3 legacy switchers take the active Max quote · A6 Tier 1 Pro needs the profession · A7 Tier 1 is once
  per user · A8 lapsed Max users qualify too · A9 cancelling loses the seat · A10 active Basic is offered
  Tier 1 only after Basic ends.
- **Later, not in this plan:** the 4 legacy Max holders' "claim 149.90 for 3 more months" message.

## 8. Basic price raise 29.90 → 39.90 (✅ owner 2026-10-27, independent of the tiers)
- **Why it's separate:** Basic is one-time and outside every tier, so this is a plain catalog change. It can
  ship right after the flip (§6 step 5), not before it. Written as `191_basic_price_39_90.sql`; the copy is in
  `pricing.ts`.
- **Order:** this is a **price raise**, so the copy goes first and the DB second ([[pricing-page]] rule).
  1. Deploy `frontend/lib/pricing.ts`:133 (`price: "39.90"`), plus any landing or `/pay/basic` copy that
     hard-codes 29.90.
  2. Then `UPDATE plans SET price_sar = 39.90 WHERE plan_id = 'basic';` (the in-process plan cache takes up
     to 5 min).
- **Who is affected:** nobody already holding Basic, since it never renews. 5 Basic purchases at 29.90 were
  made between 2026-10-11 and 10-21.
- **Credit:** a Basic → Pro/Max upgrade credits the cash actually paid (§1.4-8), so 29.90 buyers are credited
  29.90-based and 39.90 buyers 39.90-based.
- **Tests:** the catalogs in TP:73-82 and TSR:94-103 (both stale anyway).
- 🔎 Apple / Google need a SAR price point at 39.90 (Phase 4).

