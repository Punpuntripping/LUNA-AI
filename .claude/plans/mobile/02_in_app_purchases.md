# Mobile — In-App Purchases & Launch Pricing

**Status:** PLANNING 2026-10-11 · nothing built, no store products created
**Depends on:** Apple Developer + Play Console accounts, Paid Apps Agreement /
payments profile (see `00_stage0_setup.md` §2), RevenueCat account (✅ opened
2026-10-12; Apple org account pending D-U-N-S, Play Console opened personal →
must be converted to Organization before an org payments profile exists).

Marks: ✅ owner decided · 📏 store rule · ❓ open · 🔎 must be verified against
current Apple / Google docs before building (stated from knowledge, not yet checked).

---

## 1. Decisions (owner, 2026-10-11)

- ✅ Subscriptions are sold **inside the app** through Apple / Google IAP.
- ✅ In-app prices are the **same as the web** (no +15% markup).
- ✅ The **regular price is shown from day one** — Max = **289.90**. A cheaper
  "base" price that is raised later is rejected.
- ✅ Launch pricing is a **promotion on top of the regular price**, never a
  lower regular price.
- ✅ Max launch ladder:

  | Step | Price | Length | Who |
  |---|---|---|---|
  | Promotion 1 | **99.90** | 3 months | new subscribers while seats are open |
  | Promotion 2 | **189.90** | 3 months | existing promo-1 subscribers **and** new subscribers |
  | Regular | **289.90** | — | everyone after that |

  Goal: the features built during these ~6 months justify the regular price.
- ✅ The web was always sold as a time-limited price (`/promo-terms` §2.2:
  promo price for **90 days** from the seat-claiming payment, then the regular
  price). The app follows the same idea.
- ✅ **Pro:** 49.90 for the first 3 months, then regular 94.90 — **no second step**.
- ✅ **Basic** (29.90, 7 days, one-time, no renewal): **sold in the app too**
  (store product type being verified — likely non-renewing).

## 2. Store mapping

### Products
- One **subscription group** «ريحان» with the products `pro_monthly` (94.90) and
  `max_monthly` (289.90) on Apple; one subscription per plan with a monthly
  base plan on Google. Product IDs are permanent — final names ❓.
- 🔎 Apple has fixed price points per currency. Check that 94.90 / 289.90 /
  99.90 / 189.90 / 49.90 SAR exist; if not, use the closest point and decide
  whether the web moves to match (prices must stay the same everywhere).
- Store prices in KSA **include 15% VAT**; Apple/Google keep 15% (Small
  Business Program / Google's 15% tier) → e.g. 99.90 nets ≈ 73.8 SAR.

### Promotion 1 — 99.90 × 3 months (new subscribers)
- **Apple:** introductory offer, *pay as you go*, 3 monthly periods at 99.90,
  with start/end dates in App Store Connect.
  - 📏 One introductory offer per Apple ID **per subscription group, ever**.
  - Apple cannot gate an intro offer per user → the **100-seat cap** is enforced
    by switching the offer off (manually or via the App Store Connect API 🔎)
    when the backend's seat count fills. Small overflow accepted (the web
    already expects 0–3 over capacity).
- **Google:** offer on the base plan with a 3-period discounted phase,
  **developer-determined eligibility** → the backend can gate it exactly. 🔎

### Promotion 2 — 189.90 × 3 months
- **New subscribers:** end the 99.90 intro offer, create a 189.90 intro
  offer (3 periods). People who already used an intro offer in the group are
  not eligible → treat them like existing subscribers.
- **Existing subscribers (Apple):** **promotional offer** at 189.90 × 3
  periods. The backend signs it, so only eligible users (seat holders) get it.
  - ⚠ The user must **accept it inside the app** (Apple purchase sheet).
    Accepted while active → starts at the next renewal (month 4). 🔎
  - ⚠ If not accepted before the month-3 renewal, Apple charges **289.90**.
    Mitigation (required):
    - in-app banner «عرض خاص لك: 189.90 لمدة 3 أشهر» from the start of month 3;
    - push + email reminder a few days before renewal (telling them about the
      in-app offer is allowed).
- **Existing subscribers (Google):** an existing subscription can't take a
  new offer on the same base plan; likely path = replace the subscription with
  the same plan + a 189.90 offer (also needs user confirmation). 🔎
- **Web:** applied automatically (we own billing) → needs a web change, §4.

### Extending a promotion
- **For new subscribers:** end the current offer, create a longer one.
- **Promotion 1 for people already in it:** not automatic. Apple fixes their
  terms at purchase; the only way is a promotional offer that they accept.
- Nothing forces the move to the regular price on a fixed date; we choose when
  promotion 2 ends.

### Raising the regular price later (if ever)
- 📏 Large increases for existing subscribers need their **consent** in-app,
  otherwise the subscription ends at renewal. Threshold rules + KSA handling 🔎.
  Not part of the current ladder (regular price is already 289.90).

## 3. Backend

- **Purchase verification + entitlement:** App Store Server Notifications V2
  and Google Real-time Developer Notifications → grant/renew/expire in
  `user_subscriptions` (same SSoT as Moyasar; source column = `apple` /
  `google` / `moyasar`). Never trust the client's receipt alone.
- **Seats:** an Apple/Google purchase at promo price claims an
  `early_adopter_seats` row, the same as web. The cap counts all three sources.
- **Promotional-offer signing endpoint** (Apple): backend decides eligibility
  (seat holder, in promo-1 window) and returns the signature.
- **Campaign state endpoint** already exists (`/payments/early-adopter`);
  the app reads it to decide which offer UI to show. It still never exposes a
  seat count.
- **Cross-platform:** a web subscriber logs into the app → app shows their
  plan, no purchase UI for the same plan. An Apple subscriber on the web →
  web shows «تدار من App Store» with no Moyasar checkout for that plan.
  ❓ What happens if someone has both (double payment) — refund policy.
- **Refunds:** Apple/Google handle refunds; their notifications must release
  the seat like a web refund does (`/promo-terms` §3).

## 4. Web changes needed for the ladder

- Add the **189.90 step** for Max after the 90 days, auto-applied to seat
  holders and offered to new subscribers during promotion 2.
- Update `/promo-terms` §2.2 to describe both steps.
- ✅ Align wording to **«أول 3 أشهر»** everywhere (owner 2026-10-11). Web
  `/promo-terms` §2.2 + backend window must change from 90 days to 3 billing
  months together (wording and logic must match).
- 📏 The app must not say the web is cheaper. Same prices make this moot.

## 5. App (mobile)

- IAP library ✅ **RevenueCat** (decided 2026-10-11). Alternative was: (handles receipts, notifications, offers,
  cross-platform entitlements; costs a % above a revenue threshold) vs
  `expo-iap` / `react-native-iap` + our own backend handling.
- Paywall screen: regular price shown, promo price + «المقاعد محدودة» +
  `PROMO_TERMS_NOTE` when the campaign is open; never a seat count.
- 📏 «استعادة المشتريات» (restore purchases) button.
- 📏 Subscription terms + links to `/terms` and `/privacy` on the paywall.
- 📏 No link or button to web checkout inside the app.
- Promotion-2 banner + accept flow (Apple promotional offer).
- Link to the system subscription-management screen (cancel / change plan).

## 6. Open / to verify before building

- 🔎 All 🔎 items above against current Apple / Google docs.
- ✅ Product IDs (owner OK 2026-10-11): Apple `rayhan_pro_monthly`, `rayhan_max_monthly`
  (auto-renew, one group), `rayhan_basic_7d` (non-renewing); Google
  subscriptions `pro` / `max` with base plan `monthly`, one-time product
  `basic_7d`.

## 7. Verified 2026-10-11 (official docs)

- Apple intro offer: pay-as-you-go up to 12 months; **one per person per
  subscription group**; start/end dates; can't be edited (delete + recreate);
  effect of ending it on people already in it is NOT documented → test in sandbox.
- Apple promotional offer: existing/lapsed subscribers only; starts at the
  **next billing event**; one active at a time; **RevenueCat signs it** (upload
  an In-App Purchase Key) → no signing server needed.
- Apple upgrade: immediate + Apple refunds the unused part itself;
  **no developer control over proration** → our web credit formula can't apply.
- Google: offers support **2 phases** (99.90×3 then 189.90×3 could be one
  offer for new subscribers); upgrades use fixed replacement modes, no custom
  amount; developer-determined offers can be gated by the backend.
- Commission: Apple Small Business Program 15% from day one; Google 15% for
  KSA (10%+5% structure only reaches "rest of world" 2027-09-30).
- Price increase consent threshold KSA: 20 SAR (monthly) — moot, regular price
  is fixed from day one.
- **SAR price points NOT confirmed** — check `pricePoints?filter[territory]=SAU`
  once the account exists; .99 endings are common.
- Basic as a non-renewing product works; expiry tracked by RevenueCat/backend.

## 8. Same plan, upgrades, two providers (from the code, 2026-10-11)

- Web today: re-buying the same plan **stacks time** (grant_plan); upgrades get
  a cash credit = remaining days × daily rate of what they paid
  (`payment_service._upgrade_credit`); downgrades blocked while active; one
  row per user (`user_subscriptions.user_id` UNIQUE). No provider column on
  `user_subscriptions` → add `provider` (moyasar / apple / google).
- Stores don't allow buying the same subscription twice; upgrades within the
  store use the store's proration.
- Cross-provider rules (owner 2026-10-11):

  | From → To | How |
  |---|---|
  | Web → web | today's checkout credit (unchanged) |
  | Web → app | store charges full price; backend refunds unused web value via Moyasar |
  | App → app | Apple / Google proration |
  | App → web | **blocked** — web shows «اشتراكك يُدار من App Store — يمكنك الترقية من التطبيق», no Moyasar checkout until the store subscription has ended |

  Why App → web is blocked: only Apple/Google can refund or cancel a store
  subscription; a web purchase would leave the store one renewing (double billing).
  - Plan paid in the app → web shows «تُدار من App Store / Google Play», no
    Moyasar checkout at all while it is active.
  - Plan paid on the web → app hides the same/lower plan.
  - Web Pro → app Max upgrade: Apple charges full Max; backend refunds the
    unused web Pro value via Moyasar (same formula as today's credit) when the
    Apple purchase webhook lands. ✅ owner 2026-10-11. Same-provider web
    upgrades keep today's checkout discount.
