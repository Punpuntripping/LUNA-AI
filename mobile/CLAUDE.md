# Rayhan Mobile (ريحان) — Expo app

Native iOS/Android client for Rayhan (Luna Legal AI). Standalone Expo project inside the
monorepo; it talks to the same FastAPI backend as the web app. **Phase 1 (foundation)**:
auth + signup, theme/RTL/fonts, API client, version gate — no chat yet (Phase 2).

## Stack

| Layer | Choice |
|---|---|
| Framework | Expo (SDK 57, see `package.json`), React Native, TypeScript (strict) |
| Navigation | expo-router — routes live in `app/` (every file is a screen, `_layout.tsx` = navigator). Non-route code goes OUTSIDE `app/` |
| Builds | EAS Build (`eas.json`: development / preview / production). Owner is on **Windows** — iOS builds go through EAS cloud, no Xcode, no local `ios/` |
| Native dirs | `ios/` and `android/` are generated (CNG) and gitignored — configure via `app.json` + config plugins, never by hand |
| Backend | Existing FastAPI at `https://api.rayhanai.com/api/v1` — streaming is **SSE** (not WebSocket) |

Expo changes every SDK: before touching an Expo/RN API, check the versioned docs
(`https://docs.expo.dev/versions/v57.0.0/`, index at `https://docs.expo.dev/llms.txt`) — do not trust memory.

## Folder layout

```
app/                     expo-router routes ONLY (every file is a screen; default exports)
  _layout.tsx            fonts/splash, QueryClient, Theme, Stack.Protected guards (update → unavailable → (app) | (auth))
  update-required.tsx    blocking «حدّث التطبيق» (min version / any 426)
  session-unavailable.tsx saved session but server unreachable — retry, session kept
  (auth)/                login · otp · signup · verify-email · forgot-password · reset-password
  (app)/                 index (home placeholder) · settings · account-restore (deletion grace)
src/                     everything else — imported as `@/…`
  config/env.ts          EXPO_PUBLIC_* read once
  api/                   transport (headers, timeout, error parsing) · client (token + 401 → refresh → retry) · errors (Arabic) · auth · app-config
  auth/                  token-store (SecureStore RT, in-memory AT) · refresh (single-flight rotation) · session-store (zustand)
  theme/                 tokens.generated.ts (DO NOT EDIT — `node scripts/gen-tokens.mjs`) · theme · ThemeProvider · typography · fonts
  components/            AppText (the only Text) · Button · TextField · Checkbox · CodeInput · Screen · ErrorBanner · LinkText …
  hooks/ stores/ lib/    use-cooldown, use-app-config-gate · update-gate, theme-preference · format (dates/numbers), semver, jwt, validation
scripts/gen-tokens.mjs   web globals.css → src/theme/tokens.generated.ts
```

- Text: always `AppText` (Noto Naskh Arabic per weight; Android ignores `fontWeight` on custom fonts).
- Layout: logical props only (`start`/`end`, `marginStart`, `paddingEnd`) — ESLint fails on `left`/`right`.
- Numbers/dates: `src/lib/format.ts` only — ESLint fails on Arabic-Indic literals, bare `"ar"` locales and argument-less `toLocale*()`.
- Errors: `errorMessage(err)` from `src/api/errors.ts` — always Arabic.

## Commands (run from `mobile/`)

```bash
npx expo install <pkg>   # ALWAYS instead of npm install <pkg> — picks SDK-compatible versions
npx expo start           # dev server
npx tsc --noEmit         # typecheck
npx expo-doctor          # dependency/config health
npm run lint             # expo lint + Latin-digit / RTL rules
node scripts/gen-tokens.mjs  # re-sync colours/type from the web
npx eas-cli@latest build --profile development --platform ios
```

## Hard rules

1. **Never edit `../frontend`** except `../frontend/lib/contracts/` (follow its README).
2. Shared code comes **only** via the `@contracts` alias (`tsconfig.json` paths + `metro.config.js` watchFolders). Never import anything else from `../frontend`.
3. **Own `node_modules`, never hoisted.** No npm workspaces, no root `package.json`. Metro resolves bare imports from `mobile/node_modules` only and blocks `../frontend/node_modules`.
4. **Arabic-first, RTL.** All UI copy in Arabic; layouts must be correct in RTL.
5. **Latin digits only** in the UI (0-9), same policy as web — never Arabic-Indic numerals.
6. **All user-facing errors in Arabic.**
7. **Tokens:** never in AsyncStorage/plain storage — use `expo-secure-store` (rule for when auth is built).
8. **Payments:** digital purchases inside the app MUST go through Apple / Google in-app purchase (store rules). Never link to, or embed, the web checkout (Moyasar) inside the app.
9. Do not touch Railway, root `.gitignore`, root `CLAUDE.md`, or CI from work in this folder.

## Plans

Plans live in `../.claude/plans/mobile/` — start with `03_build_plan_and_env.md`.

## Decided (2026-10-11)

- Bundle ID / package: `com.rayhanai.app` (iOS + Android, launching together)
- iPhone only (`ios.supportsTablet: false`)
- Push: Expo Push Service
- Login: email + password, email code (OTP) — no Google / Apple sign-in
- Store listing: Arabic only
- Minimum OS: iOS 16.4 / Android 7.0 (API 24) — Expo SDK 57 defaults (decided, not TBD)
- Font: Noto Naskh Arabic (same as the web), colours generated from `frontend/app/globals.css`
- Auth: backend `/auth/*` only — no supabase-js in the app
- Crash reporting: Sentry, EU region, no PII
- Purchases: RevenueCat — see `../.claude/plans/mobile/02_in_app_purchases.md`

## TBD by owner

- App icon & splash (still the Expo template assets)

## EAS

- Project `@rayhan-ai/rayhan` (owner = Expo org `rayhan-ai`), id `c7100d6f-ab0a-44be-ba3e-a328e4388642` — in `app.json`.
- CLI login is interactive-only via `!`; use `eas login --device --non-interactive` (prints a URL + code, then `--resume <id> --match <number>`).
