# Mobile — Stage 0: Before Building

**Status:** 2026-10-12 · core decisions made · Expo (+ `eas init`), Firebase,
RevenueCat, Play Console (personal — to convert) opened · **blocked on the
owner: Commercial Registration confirmation → D-U-N-S ⏱ → Apple enrolment**.
Build phases + environment → `03_build_plan_and_env.md`.

Legend: 👤 = owner must do it (identity, money, legal) · 🤖 = Claude can do it ·
✅ decided / done · ❓ decision needed · ⏱ = has a waiting period, start early

---

## 1. Decisions

### Decided (owner, 2026-10-11)
- ✅ Stack: React Native via **Expo**, code in `mobile/`, shared pure TS in
  `frontend/lib/contracts/` (2026-10-04).
- ✅ Accounts: **organization** (Apple + Google) → legal entity + **D-U-N-S** ⏱.
- ✅ Platforms: **iOS + Android together**.
- ✅ Bundle ID / package: **`com.rayhanai.app`** (set in `mobile/app.json`).
- ✅ Devices: **iPhone only** (`supportsTablet: false`).
- ✅ Push: **Expo Push Service** (Firebase project still needed for Android).
- ✅ Login: **email + password** and **email code (OTP)**. No Google, and
  **Sign in with Apple skipped for v1** (not required without Google — 4.8;
  hide-my-email would duplicate existing web accounts).
- ✅ Store listing: **Arabic only**.
- ✅ Purchases: **RevenueCat**; in-app prices = web prices; launch promotions
  → `02_in_app_purchases.md`.
- ✅ Updates: **EAS Update** (OTA) + backend **minimum-app-version** gate.

### Still open
- ✅ **Minimum OS:** iOS **16.4**, Android **7.0 (API 24)** — Expo SDK 57
  defaults; iOS 16.4 is also the floor of expo-secure-store/notifications.
  targetSdk 36 already meets Google's 2026 rule.
- ✅ **Store name / subtitle / category** (owner OK 2026-10-11): «ريحان - المساعد القانوني
  الذكي» (30 chars, same as the PWA), subtitle «الأنظمة السعودية بين يديك»,
  category Business (+ Productivity). Name availability checked in ASC.
- ✅ **Testing backend** (owner 2026-10-11): production + test accounts, no staging;
  RevenueCat sandbox events flagged and granted to test accounts only.

## 2. Accounts to open

### Apple 👤
- [ ] Prerequisites 👤: **Commercial Registration** (a freelance licence
      won't count as an organization — owner to confirm which exists), English
      legal name exactly as on the CR, company email on rayhanai.com, signer
      with legal authority.
- [ ] Apple ID with **2-factor auth** for the account holder (company email).
      2026-10-12: signup stopped at phone step — «Verification codes can't be
      sent to this phone number at this time». Likely rate-limit after retries
      (or the number is on too many Apple Accounts). Retry after 24 h, no VPN,
      try "Call me", or sign up on an iPhone / Apple Developer app, or use a
      different (company) number; last resort getsupport.apple.com.
- [ ] **D-U-N-S number** for the company ⏱ — ⏳ **REQUESTED 2026-10-20 01:47 UTC**
      (D&B iResearch "Add business — Mini Inquiry, Identity Data Only"; tracking
      ID 11080229, case 11138731, request key 2JS384R3V4; business name as
      submitted «rayhan tak», Makkah). Confirmation went to the company inbox,
      not the owner's personal Gmail. Expect the result ~2026-10-27 (+ ~2 days
      to reach Apple); D&B may email asking for the CR — answer fast.
      (free; legal name + address exactly as in the CR). Does NOT need the company Apple Account: request it via
      developer.apple.com/enroll/duns-lookup signed in with the owner's
      **personal** Apple Account (number belongs to the company, usable from
      any account; ~5 business days + ~2 days to reach Apple). Fallback:
      dnb.com → Saudi Arabia (never pay — the number is free). Same number
      is used for Google Play.
- [ ] **Apple Developer Program** as an organization — $99/year ⏱.
- [ ] App Store Connect → **Agreements, Tax, and Banking**: Paid Apps
      Agreement, bank account (IBAN + SWIFT), tax forms. Required before any
      in-app purchase can be tested or sold ⏱. No tax documents needed in
      advance: the **W-8BEN-E** is an online questionnaire in ASC (company
      type, ZATCA tax/zakat number «الرقم المميز» as foreign TIN, no US treaty
      → 30% US withholding on US-customer sales only ≈ 0). Apple/Google
      collect and remit KSA VAT on store sales — no VAT number needed to sell;
      ZATCA registration threshold (SAR 375k/yr, all channels) → accountant.
- [ ] **App Store Small Business Program** (15% instead of 30%).
- [ ] App Store Connect **API key** for EAS Submit / RevenueCat (🤖 once access exists).

### Google 👤
- [x] **Google Play Console** — $25 paid 2026-10-12, but opened as **Personal**
      by mistake. Convertible (Google help answer 16260648): once D-U-N-S
      arrives → About you → verify rayhanai.com → new **Organization** payments
      profile (personal one can't be reused) → confirm. Do NOT publish
      publicly before converting (seller = owner's name). Unverified whether
      conversion lifts the closed-testing requirement — check after.
- [ ] **Payments profile** (merchant account) for in-app purchases — must be
      an **Organization** profile (created during the account conversion).
- [ ] Service account JSON for EAS Submit + RevenueCat.
- [x] **Firebase project** `rayhan-a92c5` (Spark/free, Analytics off) — DONE
      2026-10-12. Android app `com.rayhanai.app` → `mobile/google-services.json`
      wired via `android.googleServicesFile` (not secret, stays tracked: EAS
      uploads git files only). Gradle snippets in the Firebase wizard are
      skipped — CNG generates them.
- [ ] **FCM V1 key → Expo**: key generated 2026-10-12. GCP org rayhanai.com
      enforces `iam.managed.disableServiceAccountKeyCreation` by default (legacy
      `iam.disableServiceAccountKeyCreation` was already off) → owner granted
      self **Organization Policy Administrator** (Org Admin alone can't edit
      policies) and overrode the managed one to **Off on the project only**
      (optional: set back to Inherit — existing key keeps working). Key stored
      at `%USERPROFILE%\.secrets\rayhan\` (owner-only ACL, outside OneDrive;
      no backup — regenerate from Firebase if lost). Expo website won't create
      the Android credential entry without a keystore → upload AFTER the first
      EAS Android build generates it (never hand-make a keystore).

### Tooling 👤 (Claude can drive once logged in)
- [x] **Expo account** — org `rayhan-ai`; 🤖 `eas init` DONE 2026-10-12 →
      project `@rayhan-ai/rayhan`, id in `mobile/app.json` (`owner` + `extra.eas`).
- [x] **RevenueCat** account + project — opened 2026-10-12; connect after
      store accounts exist (needs ASC In-App Purchase key + Play service
      account). Public SDK keys go in the app; the secret key never does.
- [x] **Sentry** account — choose the **EU (Frankfurt)** region at signup (permanent).
      Owner created it 2026-10-24; project + DSN + `SENTRY_AUTH_TOKEN` (EAS secret) next.
- No new AI-provider projects (Mistral / Alibaba / DeepSeek): the app never
  calls them — only the backend does, with its existing keys.

## 3. Development environment (Windows)

- ✅ Node 22.16 / npm 10.9 · Android SDK + emulator installed.
- [x] 🤖 `eas-cli` via `npx eas-cli@latest` (24.12.1), logged in as punpun99
      (owner of org `rayhan-ai`). `!`-prompt login fails (needs a TTY) → use
      `eas login --device --non-interactive`.
- iOS can't be compiled on Windows → **all iOS builds run on EAS cloud**;
  testing on a **physical iPhone**.
- [ ] 👤 Physical **iPhone** (+ TestFlight app) and an **Android phone** (or the emulator).
- [ ] 👤/🤖 Register the iPhone for development builds (`eas device:create`).
- [ ] 🤖 Optional: JDK for local Android builds.
- **Expo Go** is enough for Phase 1 (login, API, look & feel). From Phase 3
  (push) / Phase 4 (purchases) we need a **development build**.

## 4. Test environments

- ✅ EAS build profiles in `eas.json`: `development`, `preview`, `production`.
- [ ] 👤 **Sandbox testers** in App Store Connect (purchases without real money).
- [ ] 👤 **License testers** in Play Console.
- [ ] 👤 TestFlight internal group + Google Play internal testing track.
- [ ] 🤖 Dedicated **reviewer/demo account** on prod (credentials go in the
      submission notes).

## 5. Things the stores require before submission

- [ ] Privacy policy (`/privacy` exists) — extend to cover the app (push
      tokens, device info, purchase data, RevenueCat as a processor).
- [ ] Terms (`/terms` exists) — add in-app subscription terms.
- [ ] Support URL / contact (support email + WhatsApp exist).
- [ ] **In-app account deletion** (Apple) — exists on web; must be in the app.
- [ ] Apple **App Privacy** label + Google **Data safety** form.
- [ ] Age rating questionnaires.
- [ ] Export compliance: standard HTTPS only → `ITSAppUsesNonExemptEncryption = false`.
- [ ] AI disclaimer shown in the app as on web.

## 6. Brand assets (👤 approve, 🤖 can prepare drafts)

- [ ] App icon 1024×1024, **no transparency** (iOS); Android adaptive icon
      (foreground + background). Today: Expo template assets.
- [ ] Splash screen.
- [ ] Screenshots: iPhone 6.9" (+ 6.5" if required) and Android phone — **Arabic only**.
- [ ] Store texts (Arabic): description, keywords (iOS, 100 chars),
      promotional text, "what's new".

## 7. Repo & infrastructure prep

- [x] **Railway watch paths** — DONE 2026-10-11. luna-backend:
      `/backend/** /shared/** /agents/** /railway.json /.dockerignore`;
      luna-frontend: root `/frontend`, `/frontend/**`. `mobile/` commits deploy
      nothing; `frontend/lib/contracts/` commits rebuild the web frontend.
- [ ] 🤖 Commit `mobile/` skeleton + `frontend/lib/contracts/` (first Phase 1
      step; also required before any EAS build). Includes 2026-10-12 changes:
      `app.json` `owner` + `extra.eas.projectId` + `googleServicesFile`,
      `google-services.json`, `.gitignore` service-account-key patterns.
- [ ] 🤖 Root `CLAUDE.md`: add the mobile folder + rules (after owner review).
- [ ] 🤖 Native-client checks (Phase 1): no browser `Origin` accepted by the
      backend; Cloudflare doesn't challenge app API calls.
- [ ] ❓ Universal links (`apple-app-site-association`, `assetlinks.json`) —
      only if rayhanai.com links should open the app.

## 8. Order (because of the waiting periods)

1. 👤 Confirm CR → D-U-N-S ⏱ (via personal Apple Account) → Apple Developer
   enrolment + Play Console **conversion to Organization** (in parallel).
2. 👤 Agreements/Tax/Banking + Small Business Program + Play payments profile ⏱.
3. ✅ Expo, Firebase, RevenueCat accounts (2026-10-12). Sentry still open.
4. 🤖 Phase 1 runs **in parallel** with steps 1–2 (Expo Go, no store accounts needed).
5. 🤖 Next unblocked: commit `mobile/` → first **Android** dev build on EAS
   (generates the keystore → unlocks the FCM key upload). iOS dev build waits
   for Apple.
