# Mobile — Phase 4: In-app purchases (RevenueCat)

**Status:** PLANNED 2026-10-27.
- **4a** (profession step) can be built now.
- **4b** (store purchases) is blocked on D-U-N-S, which blocks the Apple org account + Paid Apps Agreement and
  the Play org conversion + payments profile (`00_stage0_setup.md` §2).

**Supersedes** `02_in_app_purchases.md` §1–§2 (prices, ladder, the old 189.90 "promotion 2"). §7 there (store
facts checked 2026-10-11) and §8 there (cross-provider rules) still apply.
**Prices and rules come from** `../pricing_campaigns_v2.md`: price tiers + seat batches, the same on web and
app. That plan ships first. Phases: `03` §3.
Marks: ✅ decided · ❓ owner decision · 🔎 verify before relying on it.

## 1. Goal + exit criteria
A user buys Pro, Max or Basic inside the app with Apple / Google billing, at the **same price tiers and
batches as the web**. The backend grants the plan, and the purchase shows on the web.

1. **Same ladder** (`pricing_campaigns_v2.md` §1):

   | | Pro | Max |
   |---|---|---|
   | Tier 1 (once per plan) | 19.90, profession only, 2 months → then 1 month | 149.90 × 4 months |
   | Tier 2 | 59.90 × 2 months | 189.90 × 3 → then × 1 |
   | Regular | 94.90 | 289.90 |

   Basic is 39.90 for 7 days (raised from 29.90 on 2026-10-27), with no renewal.
2. **The backend decides who gets which price.** Not the store, not the app. The app shows exactly what
   `/payments/app-offers` (§3) returns, which comes from the web's `promo_quote`. That covers profession,
   tiers already used, active Basic (Tier 1 only after Basic ends) and lapsed subscribers.
3. Every purchase lands in `payment_transactions` + `user_subscriptions` (`provider` apple / google) and
   claims a seat in the same batch counts as the web.
4. The paywall has restore purchases, the subscription terms text, `/terms` + `/privacy` links, and a link to
   the system «إدارة الاشتراك» screen.
5. **No link or button to web checkout anywhere in the app.** That includes links inside AI answers and
   announcements (§4 P6).
6. Cross-provider rules from `02` §8:
   - app → web is blocked («يُدار من App Store / Google Play»);
   - web → app: the store charges full price and the backend refunds the unused web value through Moyasar,
     **at what the user actually paid** (`pricing_campaigns_v2.md` §1.4-8).

## 2. Store catalog

### 2.0 The store rules that shape everything
- **Apple:** a user's first discounted purchase in the group can use the product's **intro offer**: one per
  Apple ID per group, ever ✅.
  - Every later discounted purchase needs a **promotional offer**: a lapsed subscriber coming back at a tier
    they never used, or a Pro → Max switch.
  - Apple: "all customers with an existing or expired subscription in the app" are eligible for promotional
    offers, and "You decide the criteria" ✅.
  - Intro and promotional eligibility are independent ✅.
- **Cross-platform once-per-user.** Apple can't know that someone already used Tier 1 **on the web**, so it
  would offer the intro again.
  - Fix: StoreKit's **intro-offer eligibility override**, a backend-signed JWS. WWDC25: "These new purchase
    options both require a compact JWS string and are back deployed to iOS 15."
  - The backend signs "not eligible" whenever `promo_quote` says this user's tier is used or closed.
  - 🔎 Check that react-native-purchases / RevenueCat exposes it. If it doesn't: use a no-intro product for
    those users, or accept the small leak.
- **Google:** **developer-determined** offers for every batch. Google checks nothing; the backend decides,
  and the app passes the offer token that matches the backend's batch. No "new customer acquisition" offers,
  because those would ignore web history.
- **Basic** is outside the subscription group (non-renewing on Apple, one-time on Google), so buying it uses
  up nothing.

### 2.1 Apple — one subscription group «ريحان»
| Product | Price | Intro offer (pay as you go) | Promotional offers (same terms) |
|---|---|---|---|
| `rayhan_pro_monthly` | 94.90 | active Pro **Tier 2** batch (59.90 × 2) | Pro Tier 2 batches, for lapsed users |
| `rayhan_pro_monthly_offer` ❓O1 | 94.90 | active Pro **Tier 1** batch (19.90 × 2, later × 1) | Pro Tier 1 batches, for lapsed users who never used Tier 1 |
| `rayhan_max_monthly` | 289.90 | active Max batch (Tier 1 149.90 × 4, then Tier 2 189.90 × 3 / × 1) | Max Tier 1 + Tier 2 batches, for switchers and lapsed users |
| `rayhan_basic_7d` | 39.90 | — | — |

- Intro offers can run **1–12 months** on a monthly subscription, so 1, 2, 3 and 4 all work ✅.
- **Pro → Max switch:** Apple: "For upgrades or crossgrades to a different subscription with the same
  duration, the promotional offer triggers a billing event and goes into effect immediately." ✅
- **Promotional offers:**
  - RevenueCat fetches the signed offer: `getPromotionalOffer` → `purchaseDiscountedPackage`, with the
    In-App Purchase Key uploaded.
  - 🔎 Confirm RevenueCat does the signing and supports JWS.
  - 🔎 Confirm promotional offers allow 2/3/4-month pay as you go; only pay-up-front durations are
    documented.
  - Limit: up to 10 active offers per subscription.
- **Batch rollover is manual on Apple.** Intro offers can't be edited: delete and recreate with the next
  batch's terms, and swap the matching promotional offer.
  - The backend alert `promo.batch_near_full` (90%) gives the warning.
  - Until the swap, purchases still get the old batch and are seated `over_capacity`. That's accepted, as on
    the web.
- 🔎 **SAR price points:** Apple offers 800 fixed points per currency. Check 19.90 / 39.90 / 59.90 / 94.90 /
  149.90 / 189.90 / 289.90 with `GET /v1/subscriptions/{id}/pricePoints?filter[territory]=SAU` once the
  account exists. If a point is missing, use the nearest one and **move the web to match**, so prices stay
  equal everywhere.
- 🔎 **Sandbox-test the two same-price Pro products:** which intro does a user see, and does the switch show
  the right sheet?

### 2.2 Google Play
- Subscription **`pro`**, base plan `monthly` at 94.90 (no offer = regular), with developer-determined
  offers per batch:
  - `pro-t1-a` (19.90 × 2) and `pro-t1-b` (19.90 × 1), tag `tier1`;
  - `pro-t2-a` (59.90 × 2), tag `tier2`.
- Subscription **`max`**, base plan `monthly` at 289.90, with offers `max-t1-a` (149.90 × 4), `max-t2-a`
  (189.90 × 3) and `max-t2-b` (189.90 × 1).
- One-time product **`basic_7d`** at 39.90.
- Google allows 1–52 recurrences per offer phase and up to 50 active offers ✅.
- **Rollover:** deactivate the filled batch's offer.
- **Pro → Max:** a replacement purchase with the Max batch's offer token, mode `CHARGE_PRORATED_PRICE`.
  🔎 Test end to end on the internal track; the docs imply it works but never say so.
- 🔎 SAR price granularity on Play.

### 2.3 RevenueCat
- **App user ID** = our `users.user_id` (UUID), set at sign-in and logged out at sign-out. **Never the
  email.**
- **Entitlements:** `pro`, `max`, `basic`.
- **Offerings:** `plain` (`rayhan_pro_monthly` + Max + Basic) and `offer` (`rayhan_pro_monthly_offer` + Max
  + Basic). On Android the app selects the subscription option by offer id / tag.
  - The app picks the offering by identifier from `/payments/app-offers`. Targeting rules aren't used, so
    the backend stays the single source.
- **Webhook** to the backend (§3).
- **Test Store** ✅ exists ("works immediately without platform setup"). It's used for 4b UI and webhook
  work before the store accounts exist.
  - It never exercises real Apple / Google offer logic.
  - Its API key never goes into a store build.
- **Pricing:** free up to $2,500 of monthly tracked revenue, then 1%.

## 3. Backend (`@fastapi-backend` + `@sql-migration`, after `pricing_campaigns_v2` ships)
- **B1 Migration**
  - `user_subscriptions` provider/source gains `apple` | `google`; store rows carry `store_expires_at`
    from the store.
  - `payment_transactions` gains the store transaction id, UNIQUE per store, for idempotency.
  - `promo_batches` gains `apple_intro_ref` / `apple_promo_ref` / `google_offer_id`, so a store purchase
    maps to its batch.
- **B2 `POST /api/v1/payments/revenuecat/webhook`**
  - Auth by a shared `Authorization` secret; constant-time compare; returns 200 quickly.
  - **Events → actions:**
    - `INITIAL_PURCHASE` / `RENEWAL` / `PRODUCT_CHANGE` / `NON_RENEWING_PURCHASE` → payment row
      (`price_in_purchased_currency`, `currency`, `store`, `period_type` INTRO / PROMOTIONAL / NORMAL,
      offer id) → `grant_plan` → seat claim in the mapped batch.
    - `CANCELLATION` → mark cancelled; the seat is lost per `pricing_campaigns_v2.md` §1.4-6. A refund
      (`price` < 0 / cancel reason) releases the seat, exactly like a web refund.
    - `EXPIRATION` → expire (and a Basic expiry triggers the Tier 1 offer, §1.3-2 there).
    - `BILLING_ISSUE` → flag.
  - Re-fetch the subscriber from the RevenueCat REST API before granting; don't trust the body alone.
  - Never log tokens or receipts.
- **B3 `GET /api/v1/payments/app-offers`** (authed) returns:
  `{pro: {offering: "offer" | "plain", purchase: "intro" | "promotional" | "regular", offer_ref, intro_jws?}
  | null, max: {...same}, managed_by: "moyasar" | "apple" | "google" | null}`.
  - It's built from `promo_quote`, plus whether this store account already used its intro.
  - **It never returns counts, batches or tier numbers.**
- **B4 The renewal engine skips store subscriptions** (provider ≠ moyasar). 🚨 Without this, Moyasar would
  charge a card for a subscription Apple already bills.
- **B5 Web → app refund:** when a store purchase lands for a user with a running web plan, refund the unused
  web value through Moyasar (the remaining ratio × the cash actually paid) and end the web renewal.
- **B6 Web side:**
  - `/pay` refuses checkout while a store subscription is active (`SUBSCRIPTION_MANAGED_BY_STORE`, Arabic
    error);
  - settings shows «اشتراكك يُدار من App Store / Google Play».
- **B7 Tests:** webhook idempotency, the event → state table, the batch mapping, the
  intro / promotional / regular choice per user, store refund → seat release, renewal skip, web checkout
  blocked.

## 4. App (`@expo-mobile`)
- **P1 (4a, now) Profession step.** Port the web step:
  - `OnboardingDialog` logic: open when `profession_group` is null, one re-ask for `declined`, any exit
    saves;
  - `StepProfession` grid and its copy (`onboarding-content.ts`:47-92): «وش أقرب وصف لك؟», the 4 groups
    with their hints and chips, «أفضل عدم الإجابة», «حفظ»;
  - `PATCH /api/v1/auth/profession` and `PATCH /preferences {profession_reask_done}`;
  - «تغيير المهنة» in settings.

  It is JS-only, so it can ship as an EAS Update. **No offer popup until 4b** (nothing to sell yet). It
  starts collecting the first answer now, so app signups qualify later.
- **P2 SDK:** `react-native-purchases` plus its config plugin, which means a new dev build.
  - Public SDK keys (`appl_…` / `goog_…`) go in EAS env; the Test Store key is for the dev profile only.
  - `Purchases.logIn(user_id)` at sign-in and `logOut` at sign-out (`session-store.ts`).
- **P3 Paywall screen.**
  - Shows what `/payments/app-offers` returns: plan cards in the web layout, regular price struck-through
    next to the offer price, «المقاعد محدودة» + `PROMO_TERMS_NOTE`.
  - Apple 3.1.2 content: name, length, price per period, offer terms, the auto-renew line, `/terms`,
    `/privacy`, «استعادة المشتريات».
  - Prices are always read from the store product / offer, never typed in the app.
- **P4 Entry points** ❓O3:
  - the quota notice gets «ترقية الباقة» (today `NoticeBanner` deliberately has no CTA);
  - the parallel-limit notice offers Max;
  - the Basic-ended offer;
  - settings gets a «الاشتراك» row: plan, renewal date, «إدارة الاشتراك» → system subscriptions screen.
    For a web subscriber it shows «اشتراكك مُدار من موقع ريحان», with no link ❓O5.
- **P5 Purchases by kind:**
  - **intro:** a plain purchase, with the eligibility JWS when needed;
  - **promotional:** `getPromotionalOffer` + `purchaseDiscountedPackage` (iOS), or the developer-determined
    offer token (Android);
  - **switch:** a promotional purchase of Max (iOS), or a replacement purchase with the Max batch offer
    (Android).
- **P6 Close the link-out holes** (found 2026-10-27):
  - `src/components/markdown/links.ts`:18-21 opens any link in an answer, and the `pricing` doc tells the
    router to send users to rayhanai.com/pricing;
  - `src/announcements/logic.ts`:104-110 opens any in-site link.

  Fix: intercept `/pricing` and `/pay*` and open the paywall instead (plain text before 4b ships).
  `/promo-terms` stays allowed, because it's terms, not checkout.
- **P7 Basic:** a non-renewing purchase; the expiry comes from the backend.
- **P8 Contracts** (`frontend/lib/contracts/payments.ts`, additive): `AppOffers`, `SubscriptionState`,
  `ManagedBy`.
- **P9 Privacy:** RevenueCat is a processor (US), receiving the user UUID, purchase data and device info.
  Add it to the privacy policy, the Play data-safety form and Apple privacy labels (Phase 5).

## 5. Owner tasks
1. D-U-N-S → **Apple org enrolment** → Agreements, Tax and Banking (**Paid Apps**) → Small Business Program
   (15%).
2. **Play Console → convert to Organization** → payments profile → license testers.
3. App Store Connect **In-App Purchase Key** + an **ASC API key**, then a Play service-account JSON, both
   into RevenueCat.
4. Create the products and offers in §2 (I can script them with the ASC API key; Play is via the Console),
   or approve me doing it.
5. Create the RevenueCat webhook secret → Railway `luna-backend` `REVENUECAT_WEBHOOK_SECRET`
   (`--skip-deploys`).

## 6. Decisions ❓
- **O1** Add the new permanent product ID `rayhan_pro_monthly_offer`. Product IDs can never be reused or
  renamed. The 2026-10-11 set had only `rayhan_pro_monthly` / `rayhan_max_monthly` / `rayhan_basic_7d`.
- **O2** Build **4a now**: the profession step in the app, shipped by EAS Update. Recommended: it starts
  collecting the first answer before purchases exist.
- **O3** Paywall entry points as in P4: quota notice, parallel-limit notice, Basic-ended offer, settings
  «الاشتراك».
- **O4** Batch rollover on the stores is manual, done the same day as the 90% alert, and a small overflow is
  accepted. The alternative is the ASC / Play APIs, scripted later.
- **O5** For a web subscriber, the app shows the plan and «مُدار من موقع ريحان» with **no link**. 🔎 Check
  3.1.3(b) wording at review.
- **O6** Build 4b's code against the RevenueCat Test Store **before** the store accounts exist, or wait for
  the accounts. If built early, real store testing still needs the accounts before release.

## 7. Test plan
- **Backend:** B7 pytest, `@integration-lead` §6 (contracts, backward compatibility), `@security-reviewer`
  §8 (webhook auth, no receipts in logs, IAP rules).
- **Test Store** (before accounts): paywall, purchase → webhook → grant → web shows the plan, restore,
  cancel, expiry.
- **Apple sandbox / Play license testers** (after accounts):
  - each batch's intro shows to the right user and is refused for one who used it on the web (eligibility
    JWS);
  - a lapsed user gets the promotional offer;
  - the Pro → Max switch;
  - refund → seat release;
  - web subscriber → store purchase → Moyasar refund;
  - store subscriber → web `/pay` blocked;
  - no web link anywhere in the app.
- `@mobile-qa` on emulator and device: RTL, Arabic copy, Latin digits, dark mode, font scale 1.3.

## 8. Order of execution
1. `pricing_campaigns_v2` ships on the web.
2. **4a** profession step (now, EAS Update).
3. Owner tasks 1–3 (as soon as D-U-N-S lands).
4. B1–B7 + P2–P9 (against the Test Store first if O6 = early).
5. Store products (§2) → sandbox QA (§7) → Phase 5 store readiness → Phase 6 release.
