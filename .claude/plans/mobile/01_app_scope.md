# Mobile — App Scope (v1)

**Status:** 2026-10-11 — core scope decided; remaining ❓ are decided before the
phase that needs them (phase numbers → `03_build_plan_and_env.md`).
Marks: ✅ decided · 📏 required by store rules (not a choice) · ❓ open.
Nothing marked ❓ is built until the owner decides it.

## Foundations
- ✅ React Native with Expo, inside `mobile/` in LUNA_AI.
- ✅ Shared types/contracts with the web via `frontend/lib/contracts/`.
- ✅ Same backend (FastAPI, `api.rayhanai.com`), same accounts, same data.
- ✅ iOS + Android together · iPhone only (no iPad layouts).

## What's in the app
- ✅ The app is the **chat**: conversations, sending messages, streamed
  answers, and the account screens («واجهة ريحان»).
- ✅ **Sources from answers (option B):** tapping a citation opens a **native
  reading screen** for that article/judgment (app login + same gating as web;
  content from the existing reference `/source` endpoint). Every citation keeps
  BOTH links, same as web: «فتح المصدر الرسمي» (official page → in-app browser)
  and «فتح ال… في ريحان» (`library_url` → reader; the rayhanai.com URL can be
  copied/shared). No slug ⇒ no Rayhan link (never a guessed URL); the reader
  still works.
- ✅ Conversation list: search, star, rename, delete — **all**. *(Phase 2)*
- ✅ Workspace items, copy actions (incl. «النسخ لناجز»), attachments from the
  phone — **all features exactly as on the web** (owner 2026-10-11). *(Phase 2)*
- ❓ Share **into** Rayhan from other apps (WhatsApp/Mail → PDF).
- ✅ Settings: **all** — detail level, وضع السرية, notifications, account,
  usage limits, payment history. *(Phase 5)*
- ❓ Onboarding / تعلّم ريحان lessons in the app.

## Not in v1
- ✅ Library browsing, blog, judgments/compliance hubs, calculators (option C
  = a later version; cited sources still open via the reader above).
- ❓ Cases (القضايا) — proposed out.
- ❓ Public/marketing pages — proposed out.

## Login
- ✅ Email + password and email code (OTP — today limited to an allowlist,
  `EMAIL_OTP_ALLOWED_EMAILS`; must be opened for the app).
- ✅ **Google + Apple sign-in: both, in the LAST stage** of the build (owner
  2026-10-12) — not in the first release work. Earlier note:
- ✅ No Google. **Sign in with Apple skipped for v1** (not required without
  Google — 4.8; hide-my-email would duplicate existing web accounts; revisit
  if many signups come from the app).
- 📏 Account deletion reachable inside the app (Apple).
- ✅ OTP **open to everyone, on the web too** (owner 2026-10-11). *(Phase 1)*
  Password login always available; if code emails can't be sent (daily cap,
  Resend down) the UI switches to password (owner 2026-10-12).
  Needs code, not config: replace the allowlist with "any existing account"
  (auth.py `_otp_allowed`), drop the web device flag (`rayhan.ff.email_otp`),
  and harden first — limiters fail open without Redis, no captcha.
- ✅ **Signup inside the app** (same signup steps as web: terms consent,
  marketing opt-in default OFF).
- ❓ Face ID / fingerprint to unlock.

## Notifications
- ✅ Provider: Expo Push Service.
- ✅ «إجابتك جاهزة» when a long answer finishes (payload never carries
  content — PDPL, same as web push). *(Phase 3)*
- ❓ Any other notification types.

## Payments
- ✅ RevenueCat · in-app prices = web prices · launch promotions on top of the
  regular price — full plan in `02_in_app_purchases.md`.
- 📏 Subscriptions sold in the app use Apple/Google in-app purchase; no
  buttons or links to web checkout inside the app.
- 📏 Web subscribers may use their plan in the app **only because** the same
  plans are also sold in the app (Apple 3.1.3(b)).
- 📏 The app must not say the web is cheaper.
- ❓ A user with both a web and an Apple subscription. *(Phase 4)*
- ❓ Pro second step; Basic in-app or not. *(Phase 4)*

## Look & feel
- ✅ Arabic-first, right-to-left.
- ✅ Latin digits only in the UI (same rule as web).
- ✅ Same design system as the web (colors, the web font (Noto Naskh Arabic)).
- ✅ Dark mode at launch.

## Quality bar
- ✅ Over-the-air updates (EAS Update) for JS changes; native changes go through
  the stores.
- ✅ Backend minimum-app-version gate («حدّث التطبيق»); backend ↔ app contracts
  stay backward-compatible (add fields, never rename/remove).
- ✅ Crash reporting — **Sentry, EU (Frankfurt) region** (owner 2026-10-11), no PII
  (`sendDefaultPii` off, chat text scrubbed), traces linked to Logfire;
  optional EAS Observe (free) for performance. *(Phase 1)*
- ❓ Offline behaviour (cached conversations, or just an offline screen).
- ❓ Analytics events in the app (reuse the web's analytics endpoint).
