# Mobile — Phase 1: Foundation

**Status:** PLANNED 2026-10-11 · nothing built · code can start now in Expo Go (no store
accounts needed); EAS Update + native Sentry wait for the Expo account (`eas init`).
**2026-10-12:** Expo account + `eas init` DONE (`@rayhan-ai/rayhan`) → the
"after `eas init`" steps (U4, S4) are unblocked once `mobile/` is committed.
Marks: ✅ decided (see `00`–`03`) · ❓ owner decision · 🔎 not verified, check before relying on it.

---

## 1. Goal + exit criteria

Phase 1 delivers the base the chat (Phase 2) is built on. It includes no chat screens.
When it's done, the owner can do all of this on an Android emulator and on an iPhone
(Expo Go at first, then a dev build):

1. Open the app. It is Arabic, right-to-left, uses the web font and colours, and follows the system light/dark setting. A manual switch (فاتح / داكن / تلقائي) also works.
2. **Sign up** with the same steps as the web: full name, email, password (8+ characters), the
   terms + privacy checkbox (with links), and the marketing opt-in, which is **OFF** by default. A 6-digit
   code arrives by email. Typing it in logs the user in.
3. **Log in** with email + password, and with an **email code** (needs OTP Phase B, see §3).
4. Kill the app and reopen it: the user is still logged in (the refresh token is kept in SecureStore).
5. The home placeholder shows «مرحباً {call_name}» from `/auth/me`. Logging out works. An account
   pending deletion lands on a blocking «استعادة الحساب» screen.
6. Every error is in Arabic: wrong password, wrong code, airplane mode, server 503, rate limit.
7. If the backend's minimum version is raised above the installed build, the app shows the «حدّث التطبيق» screen.
8. A test JS error shows up in Sentry (EU). It has no email, no token and no chat text.
9. *(After `eas init`)* A preview build picks up a JS-only change via EAS Update.

## 2. Dependencies

- **Email OTP Phase B**: `.claude/plans/email_otp_login.md` (Phase B). It opens OTP to every
  existing account (`backend/app/api/auth.py:453` `_otp_allowed` → any account; GoTrue's
  `should_create_user: False` already blocks signups), drops the web device flag, adds hardening,
  and switches the Supabase **Magic Link** template to `{{ .Token }}` (still pending per memory).
  Exit criterion 3 (code login) waits on it. Nothing else does.
- **Expo account + `eas init`** (👤 `00` §2): needed for EAS Update channels, dev builds and Sentry native symbols.
- **Sentry account, EU region** (👤): needed for the DSN.
- ❓ **Font:** the plans say «IBM Plex Sans Arabic», but the web actually ships **Noto Naskh Arabic**
  400/500/600/700 (`frontend/app/layout.tsx:2,9-14`, `globals.css:107`). The goal is "same design
  system as the web", so this plan uses **Noto Naskh Arabic** unless the owner says otherwise.

## 3. Work items

### R — Repo
- **R1** Commit the `mobile/` skeleton and `frontend/lib/contracts/` (`00` §7). Diff every file first.
  Note: `frontend/types/index.ts` and `hooks/use-messages.ts` carry unrelated uncommitted
  edits (`run_active`). Commit those separately, **before** the contracts move (C1).
- **R2** Update `mobile/CLAUDE.md`. Its "TBD: Minimum OS" is stale: iOS 16.4 / API 24 are decided.
  Add the folder layout below. The root `CLAUDE.md` waits for owner review (`00` §7).
- **R3** Folder layout. Routes go in `app/`; everything else goes in `src/` (mobile/CLAUDE.md rule).
  Change the tsconfig alias from `"@/*": ["./*"]` to `"@/*": ["./src/*"]`.
  ```
  mobile/app/_layout.tsx          providers: Sentry.wrap, QueryClient, Theme, fonts/splash, version gate
  mobile/app/update-required.tsx  «حدّث التطبيق» (store link from /app/config)
  mobile/app/(auth)/_layout.tsx   redirects to (app) when a session exists
  mobile/app/(auth)/login.tsx · otp.tsx · signup.tsx · verify-email.tsx
  mobile/app/(app)/_layout.tsx    guard → (auth); deletion-pending → blocking screen
  mobile/app/(app)/index.tsx      placeholder home (call_name, logout)
  mobile/app/(app)/settings.tsx   theme switch, version/update id, logout
  mobile/app/(app)/account-restore.tsx
  mobile/src/config/env.ts        EXPO_PUBLIC_* read once, typed
  mobile/src/api/{client,errors,headers,auth,app-config}.ts
  mobile/src/auth/{token-store,session-store,refresh}.ts
  mobile/src/theme/{tokens.generated,theme,ThemeProvider,typography,fonts}.ts(x)
  mobile/src/components/{AppText,Button,TextField,Checkbox,Screen,ErrorBanner}.tsx
  mobile/src/observability/sentry.ts
  mobile/scripts/gen-tokens.mjs
  mobile/eslint.config.js
  ```
- **R4** Install packages, always with `npx expo install`: `expo-secure-store`, `expo-localization`,
  `expo-application`, `expo-updates`, `@sentry/react-native`, `@tanstack/react-query`, `zustand`,
  `@expo-google-fonts/noto-naskh-arabic`. Do **not** install `@supabase/supabase-js` (see A0).
  `npx expo-doctor` must report clean.

### P — API client (`mobile/src/api/`)
- **P1** `client.ts`: `apiFetch<T>(path, init)` against `EXPO_PUBLIC_API_URL` =
  `https://api.rayhanai.com` plus `/api/v1`. Never use the `*.up.railway.app` host: the origin lock
  (`backend/app/middleware/origin_lock.py`) will 403 it once `EDGE_SECRET` is armed. The client
  mirrors the web's `apiFetch` (`frontend/lib/api.ts:206-264`):
  - Bearer token from memory.
  - One 401 → refresh → retry.
  - Parse both error shapes: nested `{error:{code,message,status}}` (`backend/app/errors.py:126-140`)
    and the flat `detail`.
  - 15 s timeout through `AbortController`.
- **P2** `headers.ts` sends these on every request:
  - `X-App-Platform: ios|android`
  - `X-App-Version` (`expo-application` `nativeApplicationVersion`)
  - `X-App-Build` (`nativeBuildVersion`)
  - `X-App-Update-Id` (`expo-updates` `updateId`, or `embedded`)
  - `X-Request-ID` (a uuid, also set as a Sentry tag; the backend echoes it, `main.py:496-506`)

  CORS doesn't apply to native fetch (no `Origin`), so `allow_headers` (`main.py:557-564`) needs no change.
- **P3** `errors.ts`. The web has **no** client-side code→Arabic catalogue: the backend sends the
  Arabic text in `error.message`, and the web shows it (`api.ts:254-262`). The app does the same, with fallbacks:
  - Show `message` when it is a non-empty string.
  - **422** is FastAPI's default list-shaped `detail` (there is no `RequestValidationError` handler in
    `main.py`), so it maps to «البيانات المدخلة غير صحيحة».
  - Fallbacks by status: 401 «انتهت الجلسة، سجّل الدخول من جديد», 403, 404, 429 «تم تجاوز الحد المسموح من
    الطلبات», 5xx «الخدمة غير متاحة مؤقتاً، حاول مجدداً» (same as `MSG_SERVICE_UNAVAILABLE`).
  - Network/timeout: «تعذّر الاتصال بالخادم — تحقق من اتصالك».
  - **An HTML body or a `cf-mitigated` header** (Cloudflare challenge): «تعذّر الوصول إلى الخادم»,
    reported to Sentry with the tag `edge_challenge`.
  - **426** `APP_UPDATE_REQUIRED` (B2) → route to `/update-required`.
- **P4** `ErrorCode` string union (copied from `backend/app/errors.py:14-110`) goes into
  `@contracts/errors.ts`, so both apps branch on typed codes. This adds a type only; nothing changes at runtime.
- ✔ **Acceptance:** unit-level checks with a mocked `fetch` (or a small `__tests__` file run by `npx tsc` plus a
  manual run). Wrong password shows «بيانات الدخول غير صحيحة». Airplane mode shows the network message.

### A — Auth & signup
- **A0 Decision: backend `/auth/*` plus `expo-secure-store`, no supabase-js.**
  - Login, OTP, refresh, logout and me are already backend routes (`auth.py:214, 572, 640, 745, 814, 878`).
  - The web only needs supabase-js for two things: the cookie session and **signup**. Signup runs client-side
    through `supabase.auth.signUp()` with PKCE (`frontend/stores/auth-store.ts:213-299`; note at `auth.py:735-738`).
  - PKCE ties the confirmation link to the browser that started it, and a native app is not that browser.
  - So the app signs up through new backend routes (B3) and confirms the email with a **6-digit code**,
    not a link. One auth path, and no second token owner fighting SecureStore.
- **A1** `token-store.ts`:
  - The refresh token lives in SecureStore (`rayhan.auth.rt`, `keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`).
  - The access token stays **in memory only** (same as the web, rule 4).
  - Reinstall trap: the iOS keychain survives an uninstall. Wipe it on first launch when an install
    marker is missing. The marker lives in a plain file via `expo-file-system` 🔎.
- **A2** `refresh.ts` handles refresh-token rotation:
  - One refresh at a time (a single shared promise, like `api.ts:134-189`).
  - `POST /auth/refresh {refresh_token}` → **write the new refresh token to SecureStore before** using the
    new access token.
  - `401 AUTH_EXPIRED` → wipe tokens, go to login.
  - **`503` → keep the session** and show a retry. The backend deliberately separates outage from
    expiry (`auth.py:769-777`).
  - Refresh ahead of time: 5 min before `exp`, and when `AppState` returns to `active`.
- **A3** `session-store.ts` (zustand):
  - Cold start = stored refresh token → refresh → `GET /auth/me` → route.
  - `login()`, `loginWithOtp()` and `signup()` all end in one shared `establishSession(LoginResponse)`
    (same pattern as the web, `auth-store.ts:200-211`).
  - Logout = `POST /auth/logout` (always 200, `auth.py:814-875`), then wipe tokens.
- **A4 Screens:**
  - **login:** email + password, plus a «الدخول برمز عبر البريد» link.
  - **otp:** `/otp/request` → code field with `textContentType="oneTimeCode"` / `autoComplete="sms-otp"`.
    The backend strips spaces (`auth.py:417-430`). 60 s resend cooldown, matching `OTP_REQUEST_COOLDOWN_S`.
  - **signup:** same fields and validation as the web (`LoginForm.tsx:34-47, 215-225, 505-583`):
    - Name is required.
    - Email format is checked.
    - Password is 8+ characters.
    - The terms checkbox must be ticked before submit. «الشروط» and «الخصوصية» open
      `https://rayhanai.com/terms|privacy` in `expo-web-browser`.
    - Marketing defaults to `false`.
    - Sends `terms_version = LEGAL_VERSION` from `@contracts` (moved, C1).
  - **verify-email:** code entry plus «إعادة الإرسال».
  - **account-restore:** `deletion_pending` → `POST /auth/restore-account` or logout.
  - Profession / onboarding is **❓ out of Phase 1** (`01` lists onboarding ❓). `profession_group: null` is ignored for now.
- ✔ **Acceptance:** exit criteria 2–6 on both platforms. Fresh signup → the `users` row has `terms_version`,
  `marketing_opt_in=false`, and `marketing_consent_src='signup_checkbox'` (trigger from 167). Check with SQL.

### T — Theme, RTL, fonts, dark mode, digits
- **T1 Token script** `mobile/scripts/gen-tokens.mjs` (Node, no dependencies):
  - Parses `frontend/app/globals.css`: the `:root` light block (`:15-163`), the `.dark` block (`:275-344`),
    and the phone type override (`:171-180`).
  - Writes `src/theme/tokens.generated.ts` (`light`, `dark`, `radius`, `space`, `type`).
  - Almost every colour is already hex or rgba, which RN accepts as-is. Only **4 values are OKLCH**
    (`--destructive*`, `:65-66, :326-327`). The script converts them with a built-in OKLCH→sRGB function
    (about 30 lines) and fails loudly on any other unsupported CSS colour.
  - `box-shadow` strings go through as RN `boxShadow` (new architecture) 🔎.
  - The generated file header says "do not edit"; re-run the script after `/theme` changes on the web.
- **T2 Type scale:**
  - Use the **phone** tiers (px = rem×16): xs 13/18 · sm 15/23 · base 17/26 · lg 19/29 · xl 21/30 · read 17 @ 1.7.
  - Heading tokens: h1 32, h2 22, h3 18.
  - `maxFontSizeMultiplier` ≈ 1.3 on chrome.
  - Android ignores `fontWeight` on custom fonts, so map each weight to its own family
    (`NotoNaskhArabic_400Regular` … `_700Bold`) inside `AppText`.
- **T3 Fonts:** for Expo Go, `useFonts()` loads them and holds the splash screen. At the first dev build, switch
  to the `expo-font` config plugin so the fonts are embedded.
- **T4 RTL:**
  - `expo-localization` plugin `{ supportsRTL: true, forcesRTL: true }`.
  - At startup, if `!I18nManager.isRTL`, show a dev-only red banner.
  - Layouts use logical props only (`marginStart`/`paddingEnd`/`start`/`end`).
  - Plain `left`/`right` are banned by review checklist.
  - 🔎 The Expo docs say the static config applies in Expo Go, but Expo Go *resets runtime RTL*.
    Check on the emulator on day one. If Expo Go stays LTR, RTL sign-off moves to the first dev build.
- **T5 Dark mode:**
  - `app.json` already has `userInterfaceStyle: automatic`.
  - The theme follows `useColorScheme()`, plus a manual setting (فاتح/داكن/تلقائي) like the web's
    next-themes cycle (`frontend/components/ui/theme-toggle.tsx`, `providers.tsx:38-41`).
  - The manual setting is applied with `Appearance.setColorScheme()`, so native controls follow it too, and
    persisted (non-secret, so plain storage is fine; SecureStore is acceptable to avoid adding AsyncStorage).
  - `expo-system-ui` sets the root background so dark mode doesn't flash white.
- **T6 Latin digits:**
  - Port the web rule (`frontend/.eslintrc.json:4-31`: Arabic-Indic literals, template strings, JSX text,
    and bare `"ar"` locales passed to `toLocale*` / `Intl`) into `mobile/eslint.config.js` (flat config on
    top of `eslint-config-expo`, added by `npx expo lint`).
  - **Add one rule the phone needs:** ban `toLocaleString()` / `toLocaleDateString()` **with no arguments**. On a
    device set to Arabic they produce ٠-٩.
  - Move `frontend/lib/format/numerals.ts` (pure, no imports; `AR_NUM_LOCALE`, `AR_DATE_LOCALE`,
    `toLatinDigits`) into contracts (C1).
  - 🔎 Check that Hermes Intl honours `-u-nu-latn` on both platforms with a quick test screen.
- ✔ **Acceptance:** exit criterion 1. Screenshots of login + home in light and dark on both platforms.
  `npm run lint` is clean.

### C — Contracts move (`frontend/lib/contracts/`)
- **C1** Create these pure files, copied verbatim from `frontend/types/index.ts`, plus an `index.ts` barrel.
  - **`agents.ts`:** `AgentFamily`, `TaskType`, `WorkspaceItemKind`, `WorkspaceCreator` (`:702-714`).
  - **`messages.ts`:**
    - `MessageMetadataKind`, `MessageMetadata`, `TemplateScope`, `TemplateUsed`, `NextStepKind`, `NextStep`
    - `Message`, `Attachment`, `MessageListResponse` (including the `run_active` field from the uncommitted edit), `SendMessagePayload`
    - Line refs: `:192-305, :1747-1750`
    - Depends on `agents.ts`.
  - **`conversations.ts`:** `ConversationSummary`, `ConversationDetail`, `ConversationListResponse`,
    `CreateConversationRequest` (`:133-185`). No dependencies.
  - **`sse.ts`:**
    - Types: `SSEMessageStart`, `SSEToken`, `SSEDuplicate`, `SSEQuotaExceeded`, `SSEParallelLimit`, `SSEDone`
      (`:499-596, :676-697`); `SSEAgentRunStarted`, `DeepSearchStage`, `SSEAgentProgressData`, `SSEAgentProgress`,
      `SSEStatus`, `SSEAgentRunFinished`, `SSEAgentQuestion`, `SSEAgentResumed`, `SSEWorkspaceItem{Created,Updated,Locked,Unlocked}`,
      `SSEReferencedExistingItem`, `SSENextSteps`, `SSETemplatesUsed` (`:1605-1745`).
    - Depends on `agents.ts` and `messages.ts`.
    - **New (additive):** `SSEError {detail}`, `SSEEventName` union, and `SSEEventMap` (name → payload). The names
      are the 21 cases in `frontend/hooks/use-chat.ts:500-934`. The backend also emits **`agent_selected`**
      (orchestrator), which the web ignores. Include it as an optional name 🔎 (check its payload).
  - **`auth.ts`:** `User`, `AuthTokens`, `AuthResponse` (`:5-70`), plus new `OtpRequestResponse`, `SignupRequest`,
    `SignupResponse`, `AppConfig` (B1/B3).
  - **`legal.ts`:** `LEGAL_VERSION` and `LEGAL_ROUTES` (from `frontend/lib/legal.ts:10-28`).
  - **`numerals.ts`:** from `frontend/lib/format/numerals.ts`.
  - **`errors.ts`:** the `ErrorCode` union (P4).
  - **Stay on web (not pure):**
    - `WorkspaceItem` (it imports kinds from contracts)
    - the zod parsers `lib/next-steps.ts` / `lib/templates-used.ts`
    - `onboarding-content.ts` (uses lucide icons)
    - the SSE read loop (Phase 2 may extract a pure frame parser).
- **C2** Web keeps its imports unchanged:
  - `frontend/types/index.ts` removes the moved definitions and adds `export type { … } from "@/lib/contracts"`.
    Use `export type` because of `isolatedModules`.
  - `frontend/lib/legal.ts` and `frontend/lib/format/numerals.ts` become one-line re-exports.
  - No consumer file changes. There are 23 importers of numerals and many of `@/types`.
- **C3** Not a breaking change, so `CONTRACTS_VERSION` stays 0 (README rule 7). Update the README's "Nothing has been moved here yet".
- ✔ **Acceptance:**
  - `cd frontend && npx tsc --noEmit && npm run lint && npm run build` all green.
  - `cd mobile && npx tsc --noEmit` green.
  - grep confirms no `@/`, `react` or zod import under `frontend/lib/contracts/`.
  - Note: the commit rebuilds the web frontend (watch path `/frontend/**`). That is expected.

### U — OTA + minimum version
- **U1 Backend `GET /api/v1/app/config`** (B1). It is public, needs no auth, and sends `Cache-Control: public, max-age=300`.
  It returns `{min_supported_version:{ios,android}, latest_version:{ios,android}, store_urls:{ios,android}}`.
  The update message is a fixed Arabic string in the app.
  - **Recommendation: environment variables, not a DB row.**
    - A forced update is always tied to a backend deploy anyway.
    - Env vars avoid a migration, RLS, and a public anon read path.
    - Changing a Railway variable redeploys in minutes.
  - Fields go in `shared/config.py` as `APP_MIN_VERSION_IOS`, `APP_MIN_VERSION_ANDROID`, `APP_LATEST_VERSION_IOS`,
    `APP_LATEST_VERSION_ANDROID`, `APP_STORE_URL_IOS`, `APP_STORE_URL_ANDROID`. Defaults are `"1.0.0"` / empty.
  - Settings is `case_sensitive=True`, so field name == env name (validation-alias trap).
  - The path is outside `/api/v1/auth/`, so it falls under the default 60/min/IP limit, not 10.
- **U2 Server-side enforcement** (B2): a small dependency or middleware. Only when `X-App-Platform` is `ios`/`android`
  **and** `X-App-Version` < the minimum, answer **426** `APP_UPDATE_REQUIRED` with Arabic text.
  - Exempt: `/app/config`, `/health`, `/auth/logout`.
  - Requests without the header (the web) are never touched, so this is backward-compatible.
  - Comparison is semver on the **native** version. OTA updates don't change it, which is correct: the gate exists for
    native-incompatible changes.
- **U3 App side:** fetch `/app/config` on launch and on every return to foreground. Compare against
  `nativeApplicationVersion`. If below the minimum → `/update-required` (store button, no dismiss). If below latest → a
  dismissible banner. Any 426 from P3 routes to the same screen.
- **U4 EAS Update** (after `eas init`):
  - `npx expo install expo-updates`, then `eas update:configure`.
  - `runtimeVersion: { policy: "fingerprint" }`. The SDK 57 docs present it as the automatic option for CNG and
    custom-native projects. Fallback is `appVersion` if fingerprints drift between local and EAS 🔎.
  - Channels: `"channel"` in each `eas.json` build profile, named `development`, `preview`, `production` 🔎 (key
    name per EAS docs).
  - Default check-on-load: the update applies on the next launch.
  - Release flow: `eas update --channel preview` first, then `production`.
  - `eas.json` already has `appVersionSource: remote` + `autoIncrement`. The build number auto-increments. The
    marketing version (`1.0.0`) is what the gate compares.
- ✔ **Acceptance:**
  - pytest (§6).
  - Set `APP_MIN_VERSION_ANDROID=9.0.0` on prod for 5 minutes → the emulator shows «حدّث التطبيق» → revert.
  - Exit criterion 9 once a preview build exists.

### S — Sentry (`@sentry/react-native`, EU)
- **S1 Setup:**
  - `npx expo install @sentry/react-native`.
  - Config plugin `["@sentry/react-native/expo", {organization, project, url: "https://de.sentry.io/"}]` 🔎 (EU host).
  - `metro.config.js` switches to `getSentryExpoConfig(__dirname)`. Keep the existing `watchFolders` / `blockList` /
    `nodeModulesPaths` changes on top of it.
  - `EXPO_PUBLIC_SENTRY_DSN` (public). `SENTRY_AUTH_TOKEN` is an **EAS secret**, never committed.
- **S2 Init** (`src/observability/sentry.ts`):
  - `sendDefaultPii: false`.
  - No Session Replay, `attachScreenshot: false`, `attachViewHierarchy: false`.
  - `environment` comes from the update channel; `release`/`dist` come from version/build.
  - Tags: `update_id`, `request_id`.
  - `beforeSend` / `beforeBreadcrumb` drop request/response bodies and query strings for `/messages`, `/auth/*` and
    `/workspace`; strip `Authorization`; redact any email; drop console breadcrumbs that carry message text.
  - `tracePropagationTargets: [/^https:\/\/api\.rayhanai\.com\/api\/v1\//]`, `tracesSampleRate` 0.1.
- **S3 Link to Logfire:**
  - Guaranteed link: `X-Request-ID` (P2) as a Sentry tag, matching the backend `request_id`.
  - Joining Sentry traces to Logfire through W3C `traceparent` 🔎 (it depends on the SDK propagating `traceparent`
    and not only `sentry-trace`).
  - Backend: record the `X-App-*` headers as span attributes in `capture_request_context`
    (`backend/app/middleware/request_context.py`).
- **S4 Builds:** Expo Go captures JS errors only. Native crashes and symbolication need the dev/preview build. Source
  maps upload automatically during EAS Build. **For EAS Update**, run `npx sentry-expo-upload-sourcemaps dist` after each
  `eas update` 🔎.
- ✔ **Acceptance:** exit criterion 8. Inspect the event JSON: no `user.email`, no IP (`sendDefaultPii` off),
  no request body.

### N — Native-client backend/edge checks
- **N1 Cloudflare probes** (do these first, day 1, with curl).
  - **Facts:**
    - `api.rayhanai.com` is **orange** (memory, live-probed 2026-08-13).
    - **Browser Integrity Check is ON** (`cloudflare_navigation_hardening.md:295`).
    - WAF rule 1 skips BIC **only** when an `authorization` header is present (`:99`).
    - Default script UAs are blocked (memory: Python-urllib → 403).
  - **Which calls are exposed:** the app's **unauthenticated** calls — `/auth/login`, `/otp/*`, `/auth/refresh` (its
    token is in the body), `/auth/signup*` and `/app/config` — all go through BIC.
  - **Probes:** against each of those endpoints, send bad credentials with the UAs `okhttp/4.12.0`,
    `Rayhan/1 CFNetwork/1568.100.1 Darwin/24.0.0` and an empty UA. **Expected:** JSON 401/422/200, with no
    `cf-mitigated` header and no HTML.
  - **If challenged:** propose (owner OK + 🤖 via CF API) a WAF skip of `bic` for
    `http.host eq "api.rayhanai.com"` (a JSON API, where a challenge can never be solved). Do **not** skip on the spoofable
    `X-App-Platform` header.
  - Repeat the probe from the emulator and the iPhone using the real app.
  - ✅ **Result 2026-10-13 (curl, no Authorization):** UAs `okhttp/4.12.0`, `Rayhan/1 CFNetwork…`,
    `Expo/… CFNetwork…` and an EMPTY UA → `/auth/login` 401 JSON, `/auth/refresh` 401 JSON,
    `/auth/otp/request` 200/429 JSON, `/health` 200 — no `cf-mitigated`, no HTML. No WAF change
    needed. Re-probe `/app/config` + `/auth/signup*` after the B-deploy, and from real devices.
- **N2 Origin/CSRF:** no `Origin` or `Referer` check exists in the backend. CORS only answers browsers. Origin lock = a
  Cloudflare-injected secret, which is fine because the app goes through `api.rayhanai.com`. No cookies, no CSRF. Nothing
  to change.
- **N3 Rate limits:** `/api/v1/auth/*` = **10/min per IP**, including authenticated `/auth/me`
  (`backend/app/middleware/rate_limit.py:57-58, 429-431, 465`).
  - KSA mobile carriers use CGNAT, so many users share one IP. A cold start costs 2 auth calls (refresh + me).
  - Proposal B6, backward-compatible: requests with a verifiable Bearer token on `/auth/me` and similar read routes are
    keyed by user, not IP. Or exclude `GET /auth/me` from the auth bucket. ✅ owner OK 2026-10-12 (key by user).
- **N4 `/auth/refresh` has never carried real traffic.**
  - The web refreshes through supabase-js (`api.ts:151-189`), and grep finds no caller of the backend `/auth/refresh`.
    The app will be its first consumer.
  - It runs on the shared `supabase_auth` singleton (`auth.py:746-757`). The 1h-logout bug came from that client
    (memory: fixed via `auto_refresh_token=False`, `shared/db/client.py:137-138`).
  - B8: move `/refresh` to a throwaway client like the OTP routes do (`auth.py:565-570`). Add a concurrency test.

## 4. Backend changes

All of these are additive and backward-compatible. The web is unaffected.

| # | Change | Files |
|---|---|---|
| B1 | `GET /api/v1/app/config` + 6 settings fields | new `backend/app/api/app_config.py`; `backend/app/main.py` (include_router, like `:632-639`); `shared/config.py` |
| B2 | 426 `APP_UPDATE_REQUIRED` (only when `X-App-Platform` is present) | `backend/app/errors.py` (new enum member); new dependency/middleware in `backend/app/middleware/app_version.py`; `main.py` |
| B3 | Native signup: `POST /auth/signup` · `/auth/signup/verify` · `/auth/signup/resend` | `backend/app/api/auth.py` (bodies inline like `OtpRequestBody` `:412-434`) |
| B6 | ❓ Per-user rate-limit key for authed `/auth/me` | `backend/app/middleware/rate_limit.py` |
| B7 | `X-App-*` headers → audit/Logfire attributes | `backend/app/middleware/request_context.py` |
| B8 | `/auth/refresh` on a per-request throwaway client | `backend/app/api/auth.py:745-811` |
| (dep) | OTP Phase B | per `.claude/plans/email_otp_login.md` |

**B3 details:**
- **signup:** `sign_up({email, password, options:{data:{full_name_ar, terms_version, marketing_opt_in, signup_source:"ios_app"|"android_app"}}})`
  on a **throwaway anon client** (same trap as OTP, `auth.py:394-398`).
  - The `handle_new_user` trigger already reads `full_name_ar`, `terms_version` and `marketing_opt_in`
    (`shared/db/migrations/167_marketing_consent_proof.sql`). `signup_source` just stays in metadata, so no migration is needed.
  - Duplicate detection (empty `identities`) → 409 «البريد الإلكتروني مسجل مسبقاً», matching the web's `auth-store.ts:282-287`.
  - GoTrue rate limit → 429.
  - If confirmation is off and a session comes back, return `LoginResponse` directly.
- **verify:** `verify_otp({email, token, type:"email"})` 🔎 (`"signup"` vs `"email"` per current GoTrue) → `_build_login_response`.
- **resend:** GoTrue resend `type:"signup"`.
- Per-email cooldown and fail limits reuse the OTP helpers (`auth.py:457-518`), on top of the 10/min/IP limit.
- There is no captcha, because Turnstile can't run natively. App Attest / Play Integrity is a later 🔎.

**Supabase side (one-time, 👤 or 🤖 via Management API):** the **Confirm signup** email template gets `{{ .Token }}`
(Arabic, RTL) **in addition to** the existing link.
- The template is shared with the web. Web users simply also see a code. The web link flow is untouched.
- If an app user taps the link instead, GoTrue still confirms the account and lands them on the web's
  `/login?notice=verify_elsewhere` (`frontend/app/auth/callback/route.ts`). They then log in back in the app.
  That is acceptable for v1.

## 5. Not in Phase 1

- Chat, SSE, and the conversation list (Phase 2).
- Push (Phase 3) and purchases (Phase 4).
- Profession/onboarding ❓, in-app account deletion (Phase 5), Face ID ❓.
- Analytics. Noted for later:
  - Events go to `POST /api/v1/public/events` (`backend/app/api/analytics.py:263`).
  - Names must be registered in `EVENT_NAMES` (`backend/app/services/analytics_service.py:124`).
  - `classify_client` (`:277`) needs a `rayhan_app` bucket keyed off the UA / `X-App-Platform`.

## 6. Test plan

- **Web:** `cd frontend && npx tsc --noEmit && npm run lint && npm run build` after C1/C2.
- **Mobile:** `cd mobile && npx tsc --noEmit && npm run lint && npx expo-doctor`.
- **Backend pytest:**
  - new `backend/tests/test_app_config.py`: shape, defaults, cache header, no auth needed.
  - new `test_app_version_gate.py`: header absent → passes; below the minimum → 426 with Arabic text; exempt paths pass.
  - new `test_signup.py`: metadata keys passed, throwaway client used, duplicate → 409, verify → `LoginResponse` shape
    equal to `/login`'s, limits.
  - extend `test_email_otp.py` for Phase B.
  - new refresh concurrency test.
  - Run: `python -m pytest backend/tests -q`.
- **Edge:** the N1 curl matrix, before and after deploy.
- **Device:** Expo Go on the Android emulator, then the iPhone. Walk exit criteria 1–8, then 9 on a preview build.
  RTL check uses the reversed order of the login fields. Dark mode check toggles the system setting while the app is open.
- **SQL:** after a test signup, check the `users` row for consent fields (A acceptance). Delete the test account
  afterwards with the `nuke-account` skill.

## 7. Risks / unknowns

- 🔎 **RTL in Expo Go.** It may not apply. RTL sign-off may need the first dev build, which needs the Expo account.
- 🔎 **Cloudflare BIC** on unauthenticated app calls (N1). Worst case: login is blocked for some phones.
- **CGNAT × 10/min/IP** on `/auth/*` (N3).
- **Refresh-token rotation.** The app could crash between receiving and persisting the new token. Supabase reuse
  detection then revokes the whole family → forced logout. The mitigation is persist-first (A2). Reuse interval
  default ≈10 s 🔎.
- **Signup abuse** without a captcha. The bounds are the per-email and per-IP limits plus the GoTrue/Resend SMTP limits.
- ❓ **Font:** Noto Naskh Arabic (actual web) vs IBM Plex Sans Arabic (plans).
- 🔎 **Digits:** Hermes `Intl` with `-u-nu-latn`. Also, no-argument `toLocaleString` on Arabic devices produces ٠-٩ (T6 lint).
- 🔎 **Runtime version:** fingerprint differences between EAS cloud and local; `eas.json` `channel` key.
- 🔎 **Sentry:** EU URL in the plugin; source maps for EAS Update; whether `traceparent` is propagated.
- **Stale keychain** after reinstall (A1).
- **Payload drift:** `agent_selected` SSE event is untyped. In Phase 2, check every `SSEEventName` against the backend emit sites.

## 8. Order of execution

1. **R1** commits (diff first) → **N1** edge probes with curl (can run without any code) → **R2/R3/R4** scaffold + packages.
2. **C1–C3** contracts move. Both apps' `tsc` green. Ship the web build.
3. **T1–T6** tokens script, theme, fonts, RTL check on the emulator (decides the Expo Go vs dev build question early), lint rule.
4. **P1–P4** API client + errors.
5. **B1, B2, B3, B7, B8** (+ B6 if approved) with pytest → deploy luna-backend → re-run N1 against the new routes.
   Supabase Confirm-signup template.
6. **A1–A4** auth + signup screens → exit criteria 2–6. Code login once OTP Phase B ships.
7. **U3** gate in the app → exit criterion 7.
8. **S1–S3** Sentry (JS in Expo Go) → exit criterion 8.
9. After `eas init`: **U4** EAS Update channels + first preview build → **S4** native Sentry → exit criterion 9 and the RTL/font sign-off on the build.


## Status 2026-10-13 — DEPLOYED + live-tested (commits 4820d24 a4424d2 3338755 4ac6dd3 af134a1 9929d23)
- ✅ Backend B1/B2/B3/B6/B7/B8 + password/forgot|reset live; prod smoke: 426 gate, 422s, web untouched.
- ✅ Exit criteria tested on the Android emulator (Expo Go) against prod: 1 (Arabic/RTL via
  `direction`, font, colours, dark), 2 (signup → users row: terms_version, marketing false,
  `signup_checkbox`, `android_app`), 4 (persisted session), 5, 6, password reset → login.
- ✅ Owner decision: password set/change = **email code ONLY** on web + app (no links, no
  current-password form); reset/change signs out all OTHER sessions. Web `/forgot-password`
  + settings rebuilt; `/reset-password` + old links → `/forgot-password?link=expired`.
  Owner tested web OK.
- ✅ Supabase: Email OTP length **6**, expiry 600 s; templates live: Confirm signup (link +
  code), Magic Link (code), Reset Password (**code only**), Password changed notification ON.
  Lesson: a link in the email gets opened (user or mail scanner) and BURNS the code.
- Main test account: `enterprise@rayhanai.com` (owner pastes codes).
- ⏳ Left: exit 3 (code login for all → OTP Phase B), 7 (min-version on device), 8 Sentry
  (no account), 9 EAS Update + first dev build (RTL/font sign-off on a real build), iOS.

## Addendum 2026-10-13 — «نسيت كلمة المرور» added to Phase 1 (owner)
- Why it is essential: most users are **Google-only with no password** (33/51 in Aug) and the
  app has no Google sign-in in v1 → their way in = reset (creates a first password) or OTP
  code login (Phase B).
- Backend: `POST /auth/password/forgot {email}` → `{sent:true}` (5xx → 503, never swallowed);
  `POST /auth/password/reset {email, code, new_password}` → verify_otp `recovery` + set
  password in one call → LoginResponse (no session if the password update fails).
- App: login link «نسيت كلمة المرور؟» → `forgot-password` → `reset-password` (code + new
  password + confirm) → logged in.
- Supabase **Reset Password** template → `supabase/email_templates/reset_password_ar.html`
  (link for web + `{{ .Token }}` for the app). Owner pastes it with the Confirm-signup one.

## Addendum 2026-10-12 — code entry UX
- Code inputs (login OTP, signup verify) use `textContentType="oneTimeCode"` (iOS
  AutoFill from Mail/Messages, iOS 17+) and `autoComplete="one-time-code"`
  (Android), numeric keyboard, paste of a 6-digit string fills all boxes.
  Emails can't carry a working «نسخ الكود» button (no JS in mail clients); the
  template makes the code one-tap selectable instead
  (`supabase/email_templates/magic_link_otp_ar.html`).
