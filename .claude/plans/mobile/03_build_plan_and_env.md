# Mobile — Build Plan & Environment

**Status:** 2026-10-12 · decisions locked (see `00_stage0_setup.md` §1), backend
watch paths set, Expo project `@rayhan-ai/rayhan` linked (`eas init`), Firebase
+ RevenueCat accounts open, Apple/Google org accounts pending D-U-N-S, nothing
feature-wise built. Purchases detail lives in
`02_in_app_purchases.md`; scope in `01_app_scope.md`.

## 1. Locked decisions

| Area | Decision |
|---|---|
| Stack | Expo SDK 57 + expo-router, TypeScript strict, in `mobile/` (own node_modules) |
| Shared code | `frontend/lib/contracts/` via `@contracts` (pure TS only) |
| Accounts | Organization (Apple + Google) → D-U-N-S |
| Platforms | iOS + Android together · iPhone only |
| App ID | `com.rayhanai.app` (both stores) |
| Login | Email + password, email code (OTP) — no Google / Apple sign-in |
| Push | Expo Push Service |
| Purchases | RevenueCat · same prices as web · launch ladder per `02_…` |
| Updates | EAS Update (OTA) for JS changes + backend minimum-app-version gate |
| Listing | Arabic only |
| Backend | Same FastAPI + Supabase + Railway project as web — **no new Railway project/service** (app is distributed by stores, OTA hosted by Expo; new server pieces = routes in luna-backend) · **no staging**: prod + test accounts |
| Crash reporting | Sentry, EU (Frankfurt), no PII |
| Min OS | iOS 16.4 · Android 7.0 (API 24) |

## 2. Environment we rely on

### Local (Windows)
- Node **22.16** / npm 10.9 ✅ · Android SDK + emulator present ✅ · Java/JDK ❌
  (only needed for local Android builds — cloud builds don't need it).
- `eas-cli` ✅ via `npx eas-cli@latest` (24.12.1), logged in as `punpun99`
  (owner of Expo org `rayhan-ai`). Login from Claude: `eas login --device
  --non-interactive` → owner approves URL → `--resume <id> --match <n>`.
  `eas credentials` is interactive-only → credential uploads go through the
  expo.dev website.
- No Xcode → **every iOS build runs on EAS cloud**; testing on a physical iPhone
  with a **development build** (Expo Go is not enough: RevenueCat/push are native).
- Dev loop: `cd mobile && npx expo start` → dev build on phone/emulator; the
  phone must reach the API (prod URL, or local backend via LAN IP / tunnel).

### Repo & deploy
- `mobile/` commits **do not** redeploy anything: luna-backend watch paths =
  `/backend/** /shared/** /agents/** /railway.json /.dockerignore` (set
  2026-10-11); luna-frontend = root `/frontend`, watch `/frontend/**`.
- ⚠ A change in `frontend/lib/contracts/` **does** rebuild the web frontend
  (expected — both apps consume it).
- Backend changes for the app (push tokens, RevenueCat webhook, min-version)
  deploy the normal way and must stay **backward-compatible** with old app builds.

### Cloud services
| Service | Used for | Owner action |
|---|---|---|
| Expo / EAS | builds (dev/preview/prod), EAS Update channels, EAS Submit, push credentials | ✅ org `rayhan-ai`, project `@rayhan-ai/rayhan` (id `c7100d6f-ab0a-44be-ba3e-a328e4388642`) |
| Apple Developer + App Store Connect | iOS app record, IAP products, sandbox testers, TestFlight, APNs key | enrol (org) — waits for D-U-N-S |
| Google Play Console | Android app record, subscriptions, internal testing, service account | ⚠ opened **personal** 2026-10-12 → convert to org after D-U-N-S |
| Firebase | FCM for Android push (Expo Push still needs it) | ✅ project `rayhan-a92c5`; FCM key upload to Expo after first Android build |
| RevenueCat | purchases, offers, entitlements, webhooks to backend | ✅ account + project; connect after store accounts |
| Supabase (`dwgghvxogtwyaxmbgjod`) | auth + DB (new tables: push tokens, store subscriptions source) | — |
| Railway luna-backend | API at `https://api.rayhanai.com/api/v1` | — |
| Cloudflare | must not challenge app API calls (no browser cookies, non-browser UA) | 🤖 verify rules |
| Logfire | backend traces incl. app requests | — |

### Configuration values
**App (`mobile/`, public — compiled into the app, never secrets):**
- `EXPO_PUBLIC_API_URL` · `EXPO_PUBLIC_SUPABASE_URL` · `EXPO_PUBLIC_SUPABASE_ANON_KEY`
- `EXPO_PUBLIC_REVENUECAT_IOS_KEY` · `EXPO_PUBLIC_REVENUECAT_ANDROID_KEY`
- EAS project id ✅ in `app.json` (`extra.eas.projectId`, `owner: rayhan-ai`)
- `google-services.json` ✅ in `mobile/` (Firebase client config — public, tracked)

**Backend (Railway, secret):**
- `REVENUECAT_WEBHOOK_AUTH` (shared secret RevenueCat sends) · `REVENUECAT_SECRET_KEY`
- `EXPO_ACCESS_TOKEN` (optional, enhanced push security)
- Minimum supported app version (DB row or env var — decide in Phase 1)

**Stored by EAS / stores (not in repo):** iOS signing certs + provisioning
profiles, APNs key, Android keystore, FCM v1 service-account JSON, App Store
Connect API key, Google Play service-account JSON.

### Claude tooling (set 2026-10-12)
- **Agents:** `@expo-mobile` (builds `mobile/` + contracts) → `@mobile-qa` (Android
  emulator via mobile-mcp, grades RTL/Arabic/digits/dark/flows) loop. Backend pieces →
  `@fastapi-backend` / `@sql-migration`. `@integration-lead` (now covers `@contracts` +
  backward-compat) and `@security-reviewer` (now has a mobile section) before merges.
  Later: `mobile-release` agent (EAS build/update/submit + store compliance, Phase 5–6),
  IAP agent at Phase 4 only.
- **Installed:** Expo plugin (`expo@claude-plugins-official`, local scope — EAS/router
  skills); `mobile-mcp` (`@mobilenext/mobile-mcp@1.0.10`, local scope,
  `ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk`, AVD `Medium_Phone_API_36.0`). Context7 has
  Expo docs (`/websites/expo_dev`).
- **Next to connect:** Expo remote MCP (`https://mcp.expo.dev/mcp`, OAuth; build/review
  actions confirm first) · Cloudflare MCP auth (Phase 1 probe) · Sentry MCP read-only
  (verify EU host first) · RevenueCat MCP read-only key (Phase 4).
- **Skipped:** App Store Connect MCPs (best one is macOS-only), Google Play MCP (no
  read-only mode) → use EAS Submit + the consoles; Firebase MCP; Appium/Maestro (for now).

## 3. Build phases

| # | Phase | Main contents | Blocked by |
|---|---|---|---|
| 0 | Setup | D-U-N-S, Apple/Google/Expo/Firebase/RevenueCat accounts, `eas init`, first empty dev build on the phones — **2026-10-12: Expo/`eas init`/Firebase/RevenueCat ✅; next = commit + Android dev build; Apple + Play org wait for D-U-N-S** | owner accounts |
| 1 | Foundation | API client + Arabic errors · auth (password + OTP), tokens in SecureStore · RTL, the web font (Noto Naskh Arabic), web colors, Latin digits · EAS Update channels + min-version gate («حدّث التطبيق») · first shared types moved to `@contracts` (messages, conversations, SSE events) · Cloudflare check | Expo account (dev build) — code can start now |
| 2 | Chat core | conversation list · chat screen · SSE streaming answers · markdown + citations · stop / reconnect · AI disclaimer | Phase 1 |
| 3 | Push | Expo push token registration · backend sends «إجابتك جاهزة» (no content in payload — PDPL) | Firebase + APNs key |
| 4 | Purchases | RevenueCat SDK + paywall · backend webhook → `user_subscriptions` + seats · promotion-2 offer flow · restore purchases · web ladder (189.90 step + `/promo-terms`) | Paid Apps Agreement, Play payments profile |
| 5 | Store readiness | in-app account deletion · settings scope (❓) · icon + splash · privacy labels / data safety · age rating · demo reviewer account · Arabic screenshots + texts | brand assets |
| 6 | Beta → release | TestFlight + Play internal track · fix · submit for review | all above |

Open scope items (❓ in `01_app_scope.md`) are decided before the phase that
needs them — never built speculatively.
