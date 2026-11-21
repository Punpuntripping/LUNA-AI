# Pro Plus tier · Max price cut · Scheduled downgrade

**Status: PLAN, 2026-11-20. Nothing built or applied.** Owner direction 2026-11-20. Upcoming features will need
more points than Pro allows, and Max is too big a jump from Pro. The owner also wants users to be able to
downgrade.
**Read first:** `pricing_campaigns_v2.md` (price tiers, batches, seat rules: this plan adds to it and does not
change its rules), `subscription_auto_renewal.md` + `renewal_retry_grace.md` (the renewal engine that the
downgrade rides on), `subscription_cancellation.md` (cancel = `renewal_cancelled_at`), `mobile/07_phase4_purchases.md`
+ `mobile/08_phase4b_build_spec.md` (store products and offers).
Marks: ✅ owner decided · ❓ my recommendation, owner to confirm · 🔎 verify before relying on it.

---

## 0. The new ladder

| | Basic | Pro | **Pro Plus (new)** | Max |
|---|---|---|---|---|
| `plan_id` | `basic` | `pro` | **`pro_plus`** ✅ | `max` |
| `name_ar` | الأساسية | الاحترافية | **الاحترافية بلس** ✅ | القصوى |
| List price (SAR, VAT incl.) | 39.90 / 7 days | 94.90 | **149.90** ✅ | **219.90** ✅ (was 289.90; owner 2026-11-20, after weighing 239.90 / 229.90) |
| Promo | — | T1 19.90×2 / T2 59.90×2 | **99.90 × 2 months** ✅ | **169.90 × 2 months** ✅ (replaces 149.90×4 and 189.90×3/×1) |
| Session (5h) points | 15 | 20 | **50** ✅ | 100 |
| Weekly points (5 × session) | 75 | 100 | **250** ✅ | 500 |
| OCR pages / 30d | 50 | 150 | **500** ✅ | 1000 |
| `max_parallel_runs` | 1 | 1 | **1** ✅ | 5 |
| `library_unlocks_period` | 100 | 200 | **500** ✅ | 1000 |
| `deep_search_min_points` / `_per_window` | 1.01 / — | — / — | **— / —** | — / — |
| SAR per point at full use | — | 0.22 | **0.14** | **0.103** |

Name notes: write «بلس» as a word. A "+" symbol jumps to the wrong side in RTL text. Avoid «المتقدمة»:
in common Arabic ladders متقدم ranks *below* احترافي. Fallback name: «المكثّفة».

### 0.1 Economics (worst case: every user spends the full allowance; 1 pt = 0.0375 SAR of model cost)
Moyasar fee (fitted exactly to 62 live payments): mada (1% + 1 SAR) × 1.15 · Visa/MC (2.75% + 1 SAR) × 1.15.
The App Store numbers assume Apple's Small Business Program at 15%. Without it, the first year is 30%. 🔎 Enrol before launch.

| Monthly profit (SAR, % of shelf) | Pro 94.90 | Pro Plus 149.90 | Pro Plus promo 99.90 | Max 219.90 | Max promo 169.90 |
|---|---|---|---|---|---|
| Web, mada | 64.21 (68%) | 87.30 (58%) | 44.39 (44%) | 107.18 (49%) | 64.28 (38%) |
| App Store | 54.07 (57%) | 70.62 (47%) | 33.66 (34%) | 82.18 (37%) | 45.22 (27%) |

Not included: OCR, infrastructure, refunds. Usage on 2026-11-20 is far below the WEEKLY caps (Pro median ≈ 23/100, p90 ≈ 69), but the **5h session cap binds**: in the last 30 days 10 of 20 active paying Pro users hit 20 pts in at least one session (14 of 63 sessions, 22%); 13 of 20 reached 80%; session median 13.4, p90 24.7, max 62.8. All-time median was 8.4, so usage is rising. Pro Plus's 50 covers p90 twice over. **Confirmed by `unsent_messages`** (real blocks): 11 Pro users (10 paying) were blocked by the 5h session cap in the last 30 days, 45 messages; 4 of them on 2 separate days. Weekly blocks: 2 users. OCR: 3. Trial plans (`marketing_lawyer*`): 43 users session-blocked since 11-02.

---

## 1. Decisions

| # | Question | Recommendation |
|---|---|---|
| D1 | Name + slug | ✅ owner 2026-11-20: «الاحترافية بلس» / `pro_plus`, en "Pro Plus" |
| D2 | OCR / parallel runs / library unlocks | ✅ owner 2026-11-20: OCR **500**, parallel runs **1**, library unlocks **500** |
| D3 | Pro Plus promo batch | ✅ owner 2026-11-20: batch `pro_plus_t1_a`, tier 1, 99.90 × 2 months, **500 seats**, `profession_only=false`, no tier 2, once per user. |
| D4 | ⚠ Max tier-1 (149.90 × 4) costs the same as Pro Plus's list price, with double the points. Only **2 of its 100 seats** are taken, so it would sit on the pricing page for months. | ✅ owner 2026-11-20: Max has ONE offer, 169.90 × 2 months (D10). Disable `max_t1_a` at launch. The 2 held seats keep their terms. |
| D5 | Who gets the Pro Plus promo | ✅ (accepted 2026-11-20, "let's build") Same as Max rule 4: anyone who never used it, including past subscribers. **Pro → Pro Plus** switchers get it too (mirror of rule 6): their Pro seat is released `'upgraded'` and credit = cash paid (§1.4-8). Pro Plus → Max uses the existing Max quote. On Max now → Pro Plus is a downgrade and is never quoted. |
| D6 | Downgrade timing | ✅ (accepted 2026-11-20, "let's build") **At the next renewal only.** The current term runs out untouched. No immediate switch, no partial refund. Apple and Google behave the same way, so web and app match. |
| D7 | Downgrade price | ✅ owner 2026-11-20: the price shown when the downgrade is scheduled is a **promise**: stored on `user_subscriptions` (`renewal_plan_price_sar` / `_batch_id` / `_promo_months`). At renewal the lower of the promise and the renewal-day fresh-purchase quote is charged. The promise holds even if the offer batch is full or disabled, or the target plan is no longer for sale (seat claimed `over_capacity`; plan granted regardless of `price_sar`). |
| D8 | Downgrade to Basic / Free | ✅ (accepted 2026-11-20, "let's build") Not offered as a "change". Basic is a one-time 7-day plan. The dialog points to «إلغاء التجديد» (cancel renewal) instead. |
| D9 | Existing Max subscribers (4 active) | ✅ (accepted 2026-11-20, "let's build") They renew at 219.90 automatically (a cut, so no consent is needed). Optional one-line email/notice. |
| D10 | The Max offer | ✅ owner 2026-11-20: **169.90 × 2 months**. Implement as a NEW batch `max_t2_c` (tier 2, batch_order 3, 169.90, 2 months, seats ✅ 500) and disable `max_t2_a` + `max_t2_b` (0 seats held in either on 11-20). Do not reprice a/b in place: the store offer code = batch id (catalog.json naming rule), and 🔎 an Apple promotional offer's duration can't be edited after creation. Tier 2 stays once per user. |
| D11 | Pro 59.90 × 2 (`pro_t2_a`) | ✅ owner 2026-11-20: **unchanged**, once per person (pricing v2 rules). An earlier note here saying "open to everyone, every time" was a misreading and is withdrawn. |
| D12 | What counts as an offer "used" | ✅ owner 2026-11-20: **upgrade** inside the offer's **first charging period** (30 days from the seat claim) → NOT used; upgrade after it → used. **Refund** → the same first-period rule, which is exactly today's O1 predicate (refund of the claiming payment → not used; refund of a later renewal → used): no change. **Cancel** and **downgrade** → used. Malak's legacy seat therefore stays used; she was compensated with a free month instead. |

---

## 2. Phase A — Max price cut 289.90 → 219.90 (independent, ship first)

Price CUT order = **DB first, then frontend** (memory `project-pricing-page`).
1. **Migration** `NNN_max_price_219_90.sql`: `UPDATE plans SET price_sar = 219.90 WHERE plan_id='max'`.
   🔎 Do NOT touch `plans.promo_price_sar` (99.90, frozen for legacy seats, pricing v2 §1.4-4).
   Self-test: every Max batch price < 219.90 (149.90 / 189.90 ✓). Number = next free migration number:
   🔎 check master AND open worktrees (my-files, thread-planner) before picking.
2. Renewals: `effective_plan_price(user,'max','current')` falls through to list price for seatless users.
   🔎 Verify that on a dev account after the apply.
3. **Frontend**: `frontend/lib/pricing.ts` max `price: "219.90"`. Update the comment block (:10-17, :225-231
   rank argument "149.90 / 189.90 < 289.90").
4. **Copy**: grep `289.90` / `289٫90` across `frontend/content`, `legal/`, landing, OG, `product_docs` (prod
   showed none on 11-20). 🔎 `/promo-terms` lists Max prices.
5. **Tests** that pin 289.90: `backend/tests/test_payments.py` (:70-82 catalog fixture, :541, :579, :1305, :1490),
   `test_early_adopters.py` (:914, :1119), `test_parallel_runs.py:37`. Docstrings in `payment_service.py`
   (:239, :499, :1734, :2058) and `renewal_service.py:678`.
6. **Stores (when Phase 4b is live)**: lower `rayhan_max_monthly` / `max:monthly` to 219.90. A price decrease
   applies to existing subscribers with no consent. `scripts/store/catalog.json` price_snapshot.
   🔎 Apple SAU price point for 219.90 exists (`asc_setup.py` resolves `customerPrice` → point id).

---

## 3. Phase B — Pro Plus plan

### 3.1 Database (`@sql-migration`), one migration `NNN_pro_plus_plan.sql`
1. `INSERT INTO plans` row `pro_plus` with §0 values, `billing_cycle='recurring_30d'`, `duration_days=30`,
   `promo_price_sar=NULL` (legacy column, never used for new plans), and **`price_sar = NULL` at first**.
   NULL price = not purchasable (the checkout and store offer paths skip a NULL price), so the plan can ship dark.
2. `INSERT INTO promo_batches` `('pro_plus_t1_a','pro_plus',1,1,99.90,2,500,false)` with **`enabled=false`**.
3. Redefine every live function that hard-codes plan ids. Found in prod on 2026-11-20 by grepping `prosrc` for `'pro'`/`'max'`.
   Take each body from `pg_get_functiondef` LIVE, not from the 192 file:
   | Function | Change |
   |---|---|
   | `promo_quote` | `p_plan_id NOT IN ('pro','max')` → add `pro_plus`. Downgrade guard: `v_eff='max' AND p_plan_id IN ('pro','pro_plus')`, `v_eff='pro_plus' AND p_plan_id='pro'`. Seat-block CASE: allow a pro seat to take a pro_plus quote (D5). `v_t1_ok := true` for pro_plus. |
   | `promo_public_quote` | Add a pro_plus block: `promo_active_batch('pro_plus', 1)`. |
   | `promo_checkout_price` | 🔎 Check the `p_plan_id='pro' AND tier=1` branch; pro_plus needs no special case. Confirm the switch release `'upgraded'` covers pro→pro_plus. |
   | `promo_tier_used` | No legacy mapping for pro_plus (no legacy seats). Verify only. |
   | `intro_offer_quote` | `t.plan_id IN ('pro','max')` → add `pro_plus` (a past Pro Plus payer is a "past subscriber"). |
   | `redeem_plan_code` | `v_cur IN ('basic','pro','max','dev')` → add `pro_plus`. |
4. 🔎 Also grep live views and policies, not just functions (`pg_views.definition`, `pg_policies`) for `'max'`.
5. Self-tests inside the migration (pricing v2 §2.4 pattern): a user on pro is quoted pro_plus at 99.90;
   a user on max is quoted nothing for pro_plus; a user who used `pro_plus_t1_a` gets the list price; a batch with
   `enabled=false` gives the list price.

### 3.1b Promo rule change, D12 (same migration as 3.1)
1. `promo_tier_used`: additionally ignore a seat with `release_reason = 'upgraded' AND released_at < claimed_at + interval '30 days'`
   (upgraded inside the first charging period). The O1 refund predicate stays as it is. Legacy and batch seats alike.
   `'cancelled'`, `'downgraded'`, `'upgraded'` after day 30, and a refund of a later renewal still count.
   Impact on 2026-11-20: none yet (no `'upgraded'` releases exist).
2. Self-tests: upgrade on day 10 → not used; upgrade on day 35 → used; refund of the claiming payment → not used; refund of a later
   renewal → used; cancel → used; downgrade → used.
3. The Python fakes (`backend/tests/test_early_adopters.py` and the store-side copies) change in the same commit (pricing v2 §2.3).
   `/promo-terms`: upgrading within the first month of an offer does not use it up; cancelling, downgrading or upgrading later does.

### 3.2 Backend (`@fastapi-backend`). 🚨 owner OK required before touching backend (memory `no-unapproved-changes`)
| File | Change |
|---|---|
| `backend/app/services/payment_service.py:294` | `PLAN_RANK = {"basic":1,"pro":2,"pro_plus":3,"max":4}` |
| `payment_service.py:916` | `PROMO_PLAN_IDS` + `pro_plus` |
| `payment_service.py:1151`, `:1285`, `:1318` | Verify that the intro-offer paths stay Pro-only |
| `backend/app/services/payment_method_service.py:90` | `RENEWABLE_PLAN_IDS` + `pro_plus` (renewal selection reads it) |
| `backend/app/services/subscription_service.py:138` | `EARLY_ADOPTER_PLAN_IDS` + `pro_plus` |
| `backend/app/services/renewal_service.py:196` | Docstring; the filter itself comes from `RENEWABLE_PLAN_IDS` |
| `backend/app/services/store_purchase_service.py:133-137, 2100` | `APPLE_REGULAR_PRODUCT`, `GOOGLE_REGULAR_PRODUCT`, `SUBSCRIPTION_PLAN_IDS`, `OFFER_PLAN_IDS` + `pro_plus` (Phase D) |
| `backend/app/api/payments.py:345` | Public promo payload example/shape: `{"pro","pro_plus","max"}` |
| Tests | `test_payments.py` catalog fixture + rank tests; `test_early_adopters.py` fakes (pricing v2 §2.3: the Python fakes copy the SQL rules and change in the same commit). New cases: pro→pro_plus credit, pro_plus→max credit, max→pro_plus blocked at checkout. |

### 3.3 Web frontend (`@nextjs-frontend`)
- `frontend/lib/pricing.ts:84` `id: "basic" | "pro" | "pro_plus" | "max"`. Add a card between pro and max: `nameAr`,
  `price: "149.90"`, cadence, bullets. Who it's for: «لمن يستخدم الأدوات المتقدمة بانتظام» ❓. `upgradeOptions`
  ranks by list price, so it slots in automatically. Update its comment.
- Promo surfaces read `promo_public_quote`. The pro_plus quote must flow through `resolvePlanPricing` and
  the card note «سعر العرض لأول شهرين، ثم يعود …».
- ❓ Mark Pro as the «الأكثر طلباً» card (`pricing.ts:113`) so four cards still have a default.
- `frontend/components/Settings/AccountSettingsDialog.tsx`: plan label/switch for `pro_plus`.
- Usage dialog / quota-wall upsell: the next plan up from Pro is now Pro Plus. 🔎 grep `"max"` in
  `frontend/components` for the hard-coded "upgrade to Max" CTAs.
- Mobile web width: four cards stack; check at 375px.
- `npx tsc --noEmit`, `npm run lint`, `npm run build`.

### 3.4 Copy / legal (owner approves wording)
- `/promo-terms`: add the Pro Plus offer (99.90 for the first 2 months, then 149.90; once per user; the seat is
  lost on cancel). Never show seat counts (pricing v2 §1.4-10).
- `/terms` plan list (§5) if it enumerates plans. `product_docs` `pricing` row (the router answers price
  questions from it). Landing/pricing SEO copy. 🔎 grep «القصوى» and «الاحترافية» to find plan enumerations.

### 3.5 Launch switch (after Phases A+B are deployed)
1. `UPDATE plans SET price_sar = 149.90 WHERE plan_id = 'pro_plus'`.
2. `UPDATE promo_batches SET enabled = true WHERE batch_id = 'pro_plus_t1_a'`.
3. D4 + D10: `UPDATE promo_batches SET enabled = false WHERE batch_id IN ('max_t1_a','max_t2_a','max_t2_b')`; `INSERT` `max_t2_c` (169.90 × 2) enabled. Ship the INSERT disabled in the Phase B migration; enable it here.
4. Deploy the frontend with the card. Order: DB first (purchasable but unseen is harmless), then the card.
5. Verify: `promo_public_quote()` returns pro_plus 99.90×2; a dev checkout on pro_plus = 99.90; a dev
   Pro user upgrading → 99.90 minus credit.
Rollback: `price_sar = NULL` (stops new sales; running terms are untouched) + re-enable `max_t1_a`, `max_t2_a`, `max_t2_b` and disable `max_t2_c`.

---

## 4. Phase C — Scheduled downgrade (web / Moyasar)

Today: `create_checkout` blocks buying a lower plan while a higher one runs (`payment_service.py:2149`,
`PAYMENT_DOWNGRADE_BLOCKED`). Cancellation only stops renewal. The 2026-08-09 cancellation plan already named
the design for this: a `renewal_plan_id` that the renewal engine reads.

### 4.1 Behaviour
- In «إعدادات الحساب» → subscription section: «تغيير الباقة». It lists only renewable plans **lower** than the
  current one (max → pro_plus / pro; pro_plus → pro). Confirm sheet: «ستبقى على باقة X حتى <date>، ثم تنتقل
  إلى باقة Y بسعر Z ريال شهرياً». Undo is allowed until the renewal runs.
- At renewal: the engine charges **Y's fresh-purchase price** (D7: offer if eligible, else list), grants Y, claims the offer seat if any, and clears the pending change.
- Visible only for `source='payment'`, the same rule as cancel. Store-billed users see «إدارة الاشتراك من المتجر» (Phase D).
- Interactions:
  - Cancel renewal → clears the pending downgrade (cancel wins).
  - Buying an upgrade or the same plan → clears it.
  - During the 24h renewal grace → the ladder retries the **new** plan's price.
  - Holding a promo seat on the current plan → the seat ends at the switch (released `'downgraded'`). That tier counts as used.

### 4.2 Database
- `ALTER TABLE user_subscriptions ADD COLUMN renewal_plan_id text REFERENCES plans(plan_id)`, plus
  `renewal_plan_set_at timestamptz`. RLS: unchanged (self-row SELECT). Writes go through the service role only.
- 🔎 If `user_subscriptions_live` should expose it, rebuild the view from `pg_get_viewdef` LIVE. The 120 lesson: rebuilding
  from an old migration's shape silently dropped 4 operator columns.
- `early_adopter_seats.release_reason`: add `'downgraded'` to its CHECK (it counts as tier used, like `'upgraded'`).

### 4.3 Backend
- `subscription_service.py`: `schedule_downgrade(user, plan_id)` (validate: renewable, rank lower, active
  paid term, not store-managed, not cancelled) and `cancel_scheduled_downgrade(user)`. Arabic errors.
- Routes on the payments router, next to the cancel routes: `POST /payments/subscription/downgrade {plan_id}`,
  `DELETE /payments/subscription/downgrade`. The subscription GET returns `pending_plan_id` + its price + date.
- `renewal_service.py`: `_select_due_*` also reads `renewal_plan_id`. Charge `COALESCE(renewal_plan_id, plan_id)`
  at that plan's fresh-purchase price (D7: `promo_quote` as if no plan; claim the seat on success). `plan_changed_mid_charge` (:676) must compare against the **original** plan_id,
  not the target. On paid: grant the target, clear the column, release the seat `'downgraded'`.
- Receipt + dunning email: show the new plan name.
- Cancel / upgrade / re-purchase fulfilment clear `renewal_plan_id` (Python, never inside `grant_plan`, the same
  rule as `renewal_cancelled_at`).
- `DOWNGRADE_BLOCKED_AR` → «لديك باقة أعلى فعّالة حالياً. يمكنك التحويل إلى باقة أقل من إعدادات الحساب، ويبدأ التحويل عند التجديد.»
- Tests: schedule/undo; renewal charges the lower price and grants the lower plan; cancel clears it; upgrade clears it;
  grace retry uses the new price; seat release; store-managed rejected.

### 4.4 Frontend
`AccountSettingsDialog.tsx`: «تغيير الباقة» button, plan picker, confirm sheet, pending banner with «تراجع».
TanStack mutation + invalidate the subscription query.

### 4.5 Legal
`/terms` §5: a plan change takes effect at the next renewal at the new plan's price; the current term is
not refunded or shortened. Owner approves the wording. 🔎 Decide whether `LEGAL_VERSION` needs a bump
(the Aug precedent did not bump for clarifications).

---

## 5. Phase D — App stores (rides on the unpushed Phase 4b IAP branch)

- **Apple** (`scripts/store/catalog.json` + `asc_setup.py`): new auto-renewable `rayhan_pro_plus_monthly`,
  `plan_id: pro_plus`, `price_tiers: [1]`. Re-rank the group levels: **max 1 · pro_plus 2 · pro 3** (both
  pro products). Apple then treats Max→Pro Plus and Pro Plus→Pro as downgrades (they take effect at renewal,
  natively) and the reverse as upgrades (immediate, prorated by Apple). Promotional offer code = batch id
  `pro_plus_t1_a`. 🔎 SAU price points for 149.90 and 99.90 exist.
  🔎 Intro-offer eligibility is **per subscription group**. A user who took any intro in the group (e.g. Pro
  19.90) is not eligible for an Apple intro on Pro Plus, so serve 99.90 as a signed **promotional** offer.
- **Google** (`play_setup.py`): subscription `pro_plus`, base plan `monthly` 149.90, offer `pro-plus-t1-a`
  (99.90 × 2), tag `tier1`. In-app downgrades use replacement mode **DEFERRED**.
- **RevenueCat**: entitlement `pro_plus`; add it to the offering.
- **Seed**: migration 194's `store_offer_batches` seed + `python scripts/store/check_catalog.py` (catalog ↔
  spec ↔ migration must match).
- **Backend**: `store_purchase_service.py` constants (§3.2). The "never downgrade a running higher plan" guard
  (:1652) defers a lower store grant until the higher term ends, which is exactly the at-renewal downgrade.
  🔎 Add a test: Max → Pro Plus store renewal at the period boundary grants Pro Plus.
- **Mobile** (`@expo-mobile`): `mobile/src/purchases/catalog.ts` `PlanId`, `PLAN_ORDER`, `PLAN_COPY`;
  `subscription-model.ts`; the paywall tests. Downgrade UI = «إدارة الاشتراك» → the store's own manage-subscription
  sheet (`showManageSubscriptions` on iOS, the Play subscription deep link on Android).
- Max price cut in both stores (Phase A step 6). Max offer: new Apple promotional offer + Google offer `max-t2-c` (169.90 × 2); retire `max_t1_a` / `max_t2_a` / `max_t2_b` store offers; update the 194 `store_offer_batches` seed.

---

## 6. Rollout order

1. **Phase A** (Max 219.90): migration → frontend/copy → stores when live. Small and independent.
2. **Phase B** dark: migration (pro_plus row with NULL price, batch disabled, functions) → backend deploy →
   self-tests, then a manual `pro_plus` grant on a dev account to check the limits (checkout stays closed while
   the price is NULL).
3. **Launch switch** §3.5 (price + batch on, Max offers swapped to `max_t2_c` 169.90 × 2) → frontend card deploy.
4. **Phase C** (downgrade): migration → backend → frontend → `/terms` copy, same day.
5. **Phase D** with the Phase 4b store go-live (`mobile/09_phase4_go_live_runbook.md`).

🚨 Every master push redeploys and kills in-flight deep searches (memory `redeploy-kills-searches`): batch the
backend pushes and deploy in a quiet window.

## 7. Open
- D1–D9 above.
- Should the upcoming points-hungry features be **gated** to Pro Plus and up, or open to Pro with the cap doing the gating?
  This decides whether Pro Plus has a feature bullet or only a "more points" bullet.
- 🔎 Next free migration numbers (master shows 213 as the highest file; memory mentions 219b/220 applied:
  reconcile before numbering).

---

## 8. Build contract (2026-11-20). Worktree `C:\Programming\LUNA_AI_pro_plus`, branch `feat/pro-plus-tier` off origin/master 642cb6a

Scope now: Phases A, B, C (web + backend + DB). Phase D (stores/mobile) waits for the IAP branch (`store_purchase_service.py` is not on master).
Nothing is applied to prod and nothing is deployed until the owner says so. Migrations are dry-run against prod with ROLLBACK only.

**Migrations** (221-223 are claimed by the my-files worktree):
- `224_max_price_219_90.sql`: Phase A.
- `225_pro_plus_plan.sql`: Phase B + D12 + the Max offer swap. Ships DARK: `pro_plus.price_sar` NULL; `pro_plus_t1_a` and `max_t2_c` inserted `enabled=false`; `max_t1_a`/`max_t2_a`/`max_t2_b` untouched (the launch switch flips them).
- `226_scheduled_downgrade.sql`: Phase C.

**SQL interface the backend codes against:**
1. `promo_quote(p_user_id uuid, p_plan_id text, p_as_new boolean DEFAULT false)`: same return columns. `p_as_new = true` = quote as a fresh buyer after the current term ends (skips the "on this plan now" / "lower than current plan" guard and the live-seat block). Plan rank in SQL = `plans.price_sar` list price (pro < pro_plus < max).
2. `promo_checkout_price(...)` gains the same trailing `p_as_new boolean DEFAULT false`, passed through to `promo_quote`.
3. `claim_early_adopter_seat`: when the claim supersedes a live seat on a HIGHER-priced plan, it releases it `'downgraded'`, not `'upgraded'`.
4. `early_adopter_seats.release_reason` CHECK + `release_early_adopter_seat` accept `'downgraded'`.
5. `promo_tier_used`: also ignores `'upgraded'` seats released < 30 days after `claimed_at` (D12). Refund rule unchanged.
6. `user_subscriptions.renewal_plan_id text NULL REFERENCES plans(plan_id)`, `user_subscriptions.renewal_plan_set_at timestamptz NULL`. The `user_subscriptions_live` view is NOT rebuilt; the backend reads the table.
7. Every DROP+CREATE keeps the live ACL (GRANT/REVOKE) exactly as before.

**HTTP interface the web codes against** (`/api/v1/payments`, all errors Arabic):
- `GET /subscription` gains:
  `pending_plan_id` (str|null), `pending_plan_name_ar` (str|null), `pending_price_sar` (str|null: the price the next renewal will charge, quoted now as a fresh buyer, so it may change), `pending_promo_months` (int|null), `downgrade_options`: `[{plan_id, name_ar, price_sar, promo_price_sar|null, promo_months|null}]` (empty unless: source payment, term running, not store-managed, renewal not cancelled; only renewable plans ranked below the current one).
- `POST /subscription/downgrade` `{plan_id}` → the GET shape. 409 `SUBSCRIPTION_DOWNGRADE_INVALID` (not lower / not renewable / not allowed), 409 `SUBSCRIPTION_NOT_CANCELLABLE` (no running paid term), 409 `SUBSCRIPTION_ALREADY_CANCELLED` (undo the cancel first).
- `DELETE /subscription/downgrade` → the GET shape (no-op 200 if none pending).
- Cancel, reactivate, any upgrade/re-purchase clear `renewal_plan_id`.
- Public promo payload (`GET /early-adopter`): `promo` and `months` maps gain a `pro_plus` key.

---

## 9. Launch and rollback runbook (from the 2026-11-20 independent review)

**Launch, in this order, at a quiet hour** (every master push redeploys and kills in-flight deep searches):
1. Apply 224 → 225 → 226 to prod (each records itself in schema_migrations). Verify: max 219.90, pro_plus row with NULL price, both new batches disabled, the new columns/CHECK/index exist.
2. Push + deploy the BACKEND commit only. Verify `/health`, then GET `/payments/subscription` on a dev account (it reads the five new columns: 42703 = 226 missing).
3. Launch switch (SQL at the bottom of 225): pro_plus price 149.90, enable `pro_plus_t1_a` + `max_t2_c`, disable `max_t1_a` / `max_t2_a` / `max_t2_b`. Verify `promo_public_quote()`.
4. Push + deploy the FRONTEND commit (Pro Plus card, Max 219.90, downgrade UI, legal pages). It must not go out before steps 1 and 3: before 224 the Max card would say 219.90 while checkout charges 289.90; before step 3 the Pro Plus card's checkout returns 400.
5. Update `product_docs` pricing row (router answers price questions from it).
6. Smoke on a dev account: Pro Plus checkout at 99.90; schedule + undo a downgrade; quota-wall upsell lists Pro Plus.

**Rollback rules:**
- Backend rollback to pre-226 code: that code ignores `renewal_plan_id`, so every user with a scheduled downgrade would be charged their CURRENT (higher) plan. Before rolling back, either clear pending downgrades (and tell those users) or keep the new backend.
- Stopping Pro Plus sales after the first sale: NEVER set `pro_plus.price_sar` to NULL (running Pro Plus terms would then fail to price and silently lapse). Disable `pro_plus_t1_a` and remove the card from the website instead. Nulling the price is only safe while BOTH `select count(*) from payment_transactions where plan_id='pro_plus'` and `select count(*) from user_subscriptions where plan_id='pro_plus' or renewal_plan_id='pro_plus'` are 0.
- Frontend rollback alone is safe (old cards; the API still accepts pro_plus checkouts).

**Review #7 decided (owner 2026-11-21, option b):** a downgrade (`p_as_new`) never gets Pro tier 1 (19.90); it falls to 59.90 × 2 if unused, else list. The cancel-and-rebuy path (upgrade before day 30, lapse, buy tier 1 again) remains by design under D12.
