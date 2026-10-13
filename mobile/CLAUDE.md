# Rayhan Mobile (ريحان) — Expo app

Native iOS/Android client for Rayhan (Luna Legal AI). Standalone Expo project inside the
monorepo; it talks to the same FastAPI backend as the web app. Currently a **skeleton** —
one placeholder screen, no features.

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

## Commands (run from `mobile/`)

```bash
npx expo install <pkg>   # ALWAYS instead of npm install <pkg> — picks SDK-compatible versions
npx expo start           # dev server
npx tsc --noEmit         # typecheck
npx expo-doctor          # dependency/config health
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
- Minimum OS: iOS 16.4 / Android 7.0 (API 24) — Expo SDK 57 defaults
- Crash reporting: Sentry, EU region, no PII
- Purchases: RevenueCat — see `../.claude/plans/mobile/02_in_app_purchases.md`

## TBD by owner

- App icon & splash (still the Expo template assets)

## EAS

- Project `@rayhan-ai/rayhan` (owner = Expo org `rayhan-ai`), id `c7100d6f-ab0a-44be-ba3e-a328e4388642` — in `app.json`.
- CLI login is interactive-only via `!`; use `eas login --device --non-interactive` (prints a URL + code, then `--resume <id> --match <number>`).
