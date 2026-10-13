# «الدخول برمز عبر البريد» — Email OTP Login

**Status (verified 2026-10-11):**
- **Phase A: BUILT and DEPLOYED, dev-gated.** Commit `07339d1` (2026-09-26) is on
  `origin/master`. Prod `POST https://api.rayhanai.com/api/v1/auth/otp/request` answers
  `200 {"sent":true}` (probed 2026-10-11 with a non-listed address; the trace
  shows the Redis limiter spans and no GoTrue call, so the allowlist gate is
  live). Railway `luna-backend` has `EMAIL_OTP_ALLOWED_EMAILS` set to **one**
  tester address.
- **Phase A is NOT live-validated.** Logfire (`rihan`, 2026-09-28 → 10-11) has
  **zero** OTP requests apart from the probe. Verification steps 2–4 below
  (real code email, installed iPhone app) have never been run. The Supabase
  checks (Magic Link template has `{{ .Token }}`, 600 s expiry, SMTP rate
  limit) are **unverified**: no tool here reads GoTrue auth config.
- **Phase B (this revision): PLANNED, nothing built.** Owner decision
  2026-10-11: **email code login opens to everyone, on the web and in the
  new mobile app** (`mobile/01_app_scope.md` § Login, Phase 1).

## Why email codes

- **Installed iOS app (PWA, `pwa_step1.md`):** Google OAuth may finish in
  Safari. Safari has its own storage, and the PKCE verifier cookie behind
  `/auth/callback` lives in the context that started the flow. So the app ends
  up logged out. A 6-digit code is typed INTO the app: no redirect and no
  second context.
- **Native app (Expo, `mobile/`):** there is no Google or Apple sign-in in v1
  (`mobile/01_app_scope.md` § Login). Password and email code are the only ways
  in, so the code must work for every account.
- **Rejected:** logging in in the browser and handing the session to the app.
  It is complex, and a pairing link can be used to phish a session: an
  attacker sends a victim a link bound to the attacker's nonce, the victim
  logs in, and the attacker gets the session.

---

## Phase A — what exists today (reference)

| Piece | Where |
|---|---|
| Config gate `EMAIL_OTP_ALLOWED_EMAILS` + `email_otp_allowlist` property (empty means off, fail-closed; `Settings` is `lru_cache`d, so a change needs a redeploy) | `shared/config.py:200-219` |
| Shared login tail `_build_login_response(..., has_password)` used by `/login` and `/otp/verify` | `backend/app/api/auth.py:280-371` |
| Constants: 1 request/60 s and 5/h per email, 5 failed verifies per 15 min; Arabic messages; Redis keys use the sha256 email hash, never the raw address | `auth.py:397-409`, `_email_key` `:441` |
| Gate `_otp_allowed(email)` (allowlist membership) | `auth.py:453-454` |
| Per-email limiters. **All fail OPEN** when Redis is missing or errors | `auth.py:457-518` (`:464`, `:479`, `:490`, `:496`) |
| `POST /otp/request`: limiter, then gate (unlisted gets the same 200 and GoTrue is never called), then `sign_in_with_otp(should_create_user=False)` on an isolated anon client. A GoTrue 4xx (unknown user, GoTrue's own limit) gets the same 200; GoTrue 5xx or timeout gets 503 | `auth.py:572-637`, `should_create_user` `:607` |
| `POST /otp/verify`: lock check, gate re-check (defence in depth), `verify_otp(type="email")`; wrong code gets 401 «الرمز غير صحيح أو منتهي الصلاحية»; returns `LoginResponse`; `has_password` comes from the RPC (migration 141), never hard-coded | `auth.py:640-~730` |
| Per-IP limit: 10 requests/60 s per IP per path on `/api/v1/auth/*`. **Fails open** without Redis | `backend/app/middleware/rate_limit.py:56-58, 413-422, 429-431` |
| Tests (24, incl. `test_request_fails_open_without_redis` `:314`) | `backend/tests/test_email_otp.py` |
| Analytics names `otp_requested` / `otp_verified` / `otp_failed` | `backend/app/services/analytics_service.py:105-111`, `frontend/lib/analytics/events.ts:98-104` |
| UI: device flag `rayhan.ff.email_otp`, set by `/login?otp=1` and cleared by `?otp=0` | `frontend/lib/feature-flags.ts` (used only by OTP) |
| UI: `EmailOtpLogin` (one-time-code input, 6 digits accepted up to 10, auto-submit, 60 s resend) and the flag-gated link in `LoginForm` | `frontend/components/auth/EmailOtpLogin.tsx`, `LoginForm.tsx:81-92, 358-366, 593-605` |
| API + store: `authApi.otpRequest/otpVerify`, `loginWithOtp` → `establishSession()` | `frontend/lib/api.ts:525-535`, `frontend/stores/auth-store.ts:129, 206` |

Abuse posture today: there is **no captcha** on OTP. Turnstile exists only on
anonymous «اسأل ريحان» (`backend/app/services/ask_service.py:137-139, 227-257`,
`backend/app/api/public_ask.py:181-182`, `frontend/components/library/blocks/TurnstileGate.tsx`).
Even there it is **inert**: `TURNSTILE_SECRET_KEY` is **not set** on
`luna-backend`, so `verify_turnstile` returns True. Codes are sent by Supabase
GoTrue through Resend SMTP (`send.rayhanai.com`).

**Monitoring gap found 2026-10-11:** the route's stdlib `logger.info(...)`
lines (`auth.otp.requested`, `auth.otp.not_allowed`, …) do **not** reach
Logfire. The probe's trace holds only the HTTP span and the Redis spans. Today
the only durable OTP signals are `audit_logs` rows (`_audit_otp`, best-effort)
and client `analytics_events`.

---

## Phase B — general release (web + app)

### B1. Gate: from an allowlist to "any existing account" behind a 3-state mode

Replace `EMAIL_OTP_ALLOWED_EMAILS`-as-gate with **`EMAIL_OTP_MODE`**
(`shared/config.py`):

| Value | Behaviour | When |
|---|---|---|
| `allowlist` **(default when unset)** | Exactly Phase A: listed emails only; unlisted get the silent `{"sent":true}` | Today, and while hardening ships |
| `open` | Any email. GoTrue decides whether an account exists (`should_create_user=False`) | General release |
| `off` | `/otp/request` and `/otp/verify` both return **503** «الدخول بالرمز غير متاح حالياً — استخدم كلمة المرور» for every caller | Kill switch / rollback |

**Recommendation: use the 3-state mode instead of a plain `EMAIL_OTP_ENABLED`
boolean.** After release, the login page shows the option to everyone. A
silent kill switch (200, no email) would leave real users waiting for codes
that never come. `off` is honest, and it reveals nothing about any account
because the answer is identical for everyone. Keeping `allowlist` as the
unset default keeps the code fail-closed and means the backend can deploy
before the switch is flipped. Garbage values are treated as `allowlist`.
Keep `EMAIL_OTP_ALLOWED_EMAILS` because it is still used in `allowlist` mode.

`_otp_allowed(email)` → `_otp_gate(email) -> Literal["send","silent","off"]`.

**"Existing, non-deleted account":**
- **No account / hard-deleted account** (auth user purged): GoTrue rejects
  `sign_in_with_otp` with `should_create_user=False`. The route returns the
  same `{"sent":true}` (`auth.py:613-627`) and no email is sent. No extra DB
  read is needed.
- **Account in its deletion grace period** (`users.deletion_requested_at`
  set): **allowed**, as with password login. `_build_login_response` returns
  `deletion_pending`, and the frontend shows the deletion-pending screen, where
  the user can cancel. Blocking it would strand app-only users who want to
  cancel the deletion.
- **Banned users:** GoTrue refuses them; no change needed.

**Signup stays password-based.** `should_create_user=False` stays,
unconditionally, in every mode. Email-code login never creates an account.
New users sign up through the existing signup flow (terms consent, marketing
opt-in default OFF), on the web and in the app. Add a test that asserts the
flag is `False` in `open` mode.

**User-enumeration stance (unchanged, now load-bearing):**
- `/otp/request` always returns `200 {"sent":true}` for well-formed input:
  existing account or not, GoTrue 4xx or not, and when our own caps say no
  (see B3 for why caps are silent too). The only differing answers are the
  per-email cooldown 429, which is keyed on the email the caller typed and so
  reveals nothing about the account; 503 when GoTrue is down or the mode is
  `off`, which is the same for everyone; and 422 for a malformed email.
- **Timing side channel (new in `open` mode):** an unknown email returns as
  soon as GoTrue rejects it, while a real send waits for SMTP. To close it,
  **do the GoTrue call in a background task**
  (`asyncio.create_task`, held in a module-level set so it isn't GC'd).
  Limiter, captcha and gate decide synchronously, then the route returns
  `{"sent":true}` at once. Cost: a GoTrue outage becomes a silent non-send
  instead of 503. That is acceptable because the UI already says «إن لم يصلك
  الرمز…» and offers resend, and the outage still shows up in monitoring (B3.5).
- `/otp/verify`: a wrong code, an unknown email and an unsendable email all
  return the same 401.

### B2. Frontend (web)

- Delete the device flag: remove `frontend/lib/feature-flags.ts` (its only
  users are the two OTP components) and the `applyEmailOtpFlagFromUrl` /
  `isEmailOtpEnabled` / `otpEnabled` state in `LoginForm.tsx:81-92`. The link
  «الدخول برمز عبر البريد» renders for **everyone** in login mode
  (`LoginForm.tsx:593-605`). Register mode is unchanged: no code signup.
  `?otp=1` becomes a no-op. Optionally keep `?otp=1` as a deep link that opens
  the OTP card directly. It is harmless and useful for support.
- `EmailOtpLogin.tsx`: mount Turnstile (reuse `TurnstileGate` /
  `resolveTurnstileToken` from `components/library/blocks/TurnstileGate.tsx`;
  move them to `components/security/` if the reuse is awkward) and send
  `turnstile_token` with `otpRequest`. Handle the new 503 «غير متاح حالياً»
  with a «الدخول بكلمة المرور» button. Handle a captcha failure (400
  `CAPTCHA_FAILED`, Arabic) by re-running the widget.
- `frontend/lib/api.ts:529`: `otpRequest(email, turnstileToken?)`.
- Analytics: add a `reason: "captcha" | "unavailable"` value to `otp_failed`.
  No new event names are needed.

### B3. Hardening — must ship BEFORE `EMAIL_OTP_MODE=open`

**1. Limiters fail closed for OTP (degraded strict mode without Redis).**
OTP is a security boundary, not a damper. Without Redis today anyone can
send unlimited codes to any inbox and guess codes without lockout (only
GoTrue's own limits remain).
- Reuse `_ProcessLocalWindow` from `backend/app/middleware/route_limits.py:155-206`.
  It is bounded at 20k identities, uses LRU eviction and is per-process. Add
  an OTP-local instance for each limiter. When Redis is `None` or raises, use
  it with **stricter** numbers: cooldown 1/120 s and 3/h per email; verify
  3 fails/15 min.
- The global daily cap (below) falls back to a per-process cap of
  `OTP_DAILY_SEND_MAX // 4` (with ≤ 2 replicas that is roughly half the
  normal cap). It never falls back to "unlimited".
- Rewrite `test_request_fails_open_without_redis` (`test_email_otp.py:314`)
  as `..._degrades_strict_without_redis`.

**2. Caps, all checked before GoTrue is called, all keyed on hashed
identities:**

| Cap | Value (default, env-overridable) | Over-limit answer |
|---|---|---|
| Per-email cooldown | 1 / 60 s (exists) | 429 «انتظر قليلاً…» (exists) |
| Per-email hourly | 5 / h (exists) | 429 (exists) |
| **Per-email daily** (new; stops inbox bombing) | `OTP_EMAIL_DAILY_MAX=10` / 24 h | silent `{"sent":true}`, no send |
| **Per-IP request** (new, OTP-specific, separate from the 10/60 s middleware) | `OTP_IP_HOURLY_MAX=30` / h | 429 «محاولات كثيرة من هذه الشبكة — حاول لاحقاً» |
| **Per-IP verify fails** (new; stops spraying guesses across many emails) | 30 / h | 429 (same message as the per-email lock) |
| **Global daily sends** (new; cost and reputation guard) | `OTP_DAILY_SEND_MAX=500` (see Open questions) | silent `{"sent":true}`, no send, plus an alert |

- The per-email daily cap and the global cap answer **silently**, because a
  429 there would reveal "this address got codes today". Per-IP 429s are fine
  because they are keyed on the caller, not on any account.
- **Count only real sends** for the global cap: increment after the gate and
  captcha pass, just before the GoTrue call. Key format:
  `otp:sent:day:{UTC date}`.
- **IP identity:** use `client_ip_for_rate_limit` (`rate_limit.py:371`)
  only. Never use raw headers (the `audit-request-ctx` rule). The per-IP
  values are deliberately loose because Saudi mobile carriers put many users
  behind one CGNAT IP. The per-email caps do the precise work.
- The middleware's 10/60 s per IP per path (`rate_limit.py:56-58`) stays as it
  is and keeps failing open. The OTP-specific limits above are the strict ones.

**3. Captcha.**
- **Web: Cloudflare Turnstile on `/otp/request` only.** `/otp/verify` is
  already protected by the fail counters and GoTrue, and a captcha there
  would break auto-submit. The backend checks it with
  `ask_service.verify_turnstile(token, ip)` (fail-closed when enabled). Move
  it to a shared module (`backend/app/services/turnstile.py`) instead of
  importing from `ask_service`.
- **Enforcement knob:** `OTP_TURNSTILE_REQUIRED` (default `true`).
  Verification only runs when `TURNSTILE_SECRET_KEY` is set.
  **Coupling trap:** setting `TURNSTILE_SECRET_KEY` on `luna-backend` also
  turns on enforcement for anonymous «اسأل ريحان». Before setting it, confirm
  that `AskRayhanWidget` really sends a token in prod. The frontend site key
  falls back to a hard-coded `0x4AAAAAAEAMNZfHSmuNL4Vm`
  (`TurnstileGate.tsx:27`); check that this key's widget is allowed on
  `www.rayhanai.com`, `rayhanai.com` and the app bridge page below.
- **App: Turnstile in a WebView bridge (recommended).** There is no native
  Turnstile SDK. Cloudflare's guidance for mobile is to run the widget inside
  a WebView, so check the current Turnstile docs before building. Turnstile
  checks the hostname, so the WebView must load a real page on an allowed
  hostname, not inline HTML with no origin. Plan:
  - New tiny web page `frontend/app/turnstile-bridge/page.tsx`: noindex, no
    chrome, renders the widget in managed/invisible mode, and
    `window.ReactNativeWebView.postMessage(token)`.
  - The app opens it in a hidden or bottom-sheet `react-native-webview` just
    before `otpRequest`, then sends the token. A token is single-use and
    valid for about 300 s, so fetch it per request.
  - The same bridge can be reused later for app signup.
- **Rejected for the app: skipping captcha for "app requests".** An
  `X-Client: rayhan-app` header or user-agent is trivially forged. Any skip
  rule becomes the attacker's bypass and makes the web captcha pointless.
  App Attest / Play Integrity would be a real attestation, but that means two
  platform integrations and server verification, which is too much for v1.
  Revisit only if the WebView bridge proves unreliable.
- **Fallback if Turnstile is unreachable** (Cloudflare outage or a blocked
  script): the client shows «تعذّر التحقق — استخدم كلمة المرور». We fail
  closed on captcha and do not silently skip it.

**4. Supabase / GoTrue checks (owner, dashboard — cannot be verified from
here):**
- **Auth → Email Templates → Magic Link** contains `{{ .Token }}` and **no**
  `{{ .ConfirmationURL }}`. `signInWithOtp` to an existing user sends the
  Magic Link template. Arabic, RTL, Rayhan-branded: «رمز الدخول إلى ريحان:
  {{ .Token }} — صالح لمدة 10 دقائق. إن لم تطلبه فتجاهل هذه الرسالة.»
- **Auth → Providers → Email:** OTP expiry **600 s**; OTP length **6**,
  matching the `EmailOtpLogin` copy «6 أرقام». The backend accepts 6–10.
- **Auth → Rate Limits → "emails sent per hour"**: with custom SMTP this is
  the project-wide cap shared by **signup confirmations, password resets and
  OTP**. Check the current value. An OTP flood that hits it would also block
  signups and password recovery. Set it comfortably **above**
  `OTP_DAILY_SEND_MAX / 24 × peak factor`. Our global cap must trip first so
  that GoTrue's cap never starves signup emails.
- **Resend:** confirm the plan's daily and monthly quota and that
  `send.rayhanai.com` SPF/DKIM/DMARC pass. Bounces and complaints from bombed
  inboxes hurt sender reputation for every auth email.

**5. Monitoring (fix the gap above).**
- In both routes, set attributes on the current OTEL span
  (`opentelemetry.trace.get_current_span().set_attribute`):
  `otp.outcome` ∈ {`sent`, `not_allowed`, `gotrue_rejected`,
  `capped_email_daily`, `capped_global`, `ip_limited`, `cooldown`,
  `captcha_failed`, `off`, `verified`, `invalid`, `locked`},
  `otp.limiter_backend` ∈ {`redis`,`process`}, and `otp.client` ∈ {`web`,`app`}
  (from an `X-Client` header; **analytics only, never trusted for policy**).
  Never record the email or its hash as a span attribute; use the hash in Redis only.
- Logfire alerts (project `rihan`), created at release:
  - `sent` > 100 in 1 h
  - `capped_global` ≥ 1
  - `captcha_failed` + `ip_limited` > 200 in 1 h
  - `limiter_backend='process'` ≥ 1, meaning Redis is down and we are in
    degraded mode
- Daily usage SQL: `audit_logs` rows `auth.otp.requested/verified/failed`,
  plus `analytics_events` `otp_*` split by client.

**6. Cost.** Each send is one Resend email. A send costs us nothing beyond the
Resend quota (no LLM, no points), so the risk is quota exhaustion and sender
reputation, not spend. With the caps, the worst case is `OTP_DAILY_SEND_MAX`
emails per day. Pick the cap from the Resend plan (open question 1).

### B4. Mobile app (Expo, `mobile/`)

- It calls the same endpoints: `POST /api/v1/auth/otp/request
  {email, turnstile_token}` and `POST /api/v1/auth/otp/verify {email, code}`.
  The response is `LoginResponse`, the same as `/login`. Tokens go in
  SecureStore, and refresh uses the existing `/auth/refresh`.
- Native requests carry no `Origin`, so CORS does not apply. The
  `origin_lock` middleware is default-off and is about the network path, not
  the client. **Cloudflare must not challenge** `api.rayhanai.com` calls from
  the app (non-browser user agent, no cookies). This is already a 🤖 item in
  `mobile/03_build_plan_and_env.md:55`. Include `/api/v1/auth/otp/*` in that
  check.
- Code input: `textContentType="oneTimeCode"` (iOS) and
  `autoComplete="sms-otp"`/`"one-time-code"` (Android), Latin digits only,
  normalising Arabic-Indic digits as `EmailOtpLogin.tsx:34` does.
- Send `X-Client: rayhan-app/<version>` for telemetry only.
- Shared request and response types belong in `@contracts`
  (`frontend/lib/contracts/`, Phase 1 of the mobile build plan).

---

## Files to change (Phase B)

| File | Change |
|---|---|
| `shared/config.py` | `EMAIL_OTP_MODE` (`off`/`allowlist`/`open`, default `allowlist`) + parsed property; `OTP_EMAIL_DAILY_MAX`, `OTP_IP_HOURLY_MAX`, `OTP_DAILY_SEND_MAX`, `OTP_TURNSTILE_REQUIRED`. Keep `EMAIL_OTP_ALLOWED_EMAILS`. Field names must equal env names exactly (validation-alias trap) |
| `backend/app/api/auth.py` | `_otp_gate()` replaces `_otp_allowed`; `off` → 503 on both routes; new caps (per-email daily, per-IP request/verify-fail, global daily); fail-closed process-local fallback; Turnstile check on request; background GoTrue send; span attributes; `OtpRequestBody.turnstile_token: Optional[str]` (max 4000) |
| `backend/app/services/turnstile.py` | NEW: `verify_turnstile` moved here; `ask_service` re-exports it so `public_ask` is unchanged |
| `backend/app/middleware/route_limits.py` | none, or only export `_ProcessLocalWindow` under a public name |
| `backend/app/errors.py` (`ErrorCode`) | `CAPTCHA_FAILED`, `FEATURE_UNAVAILABLE` if missing |
| `backend/tests/test_email_otp.py` | update + new tests (below). Force-add: `.gitignore` has `backend/tests/*` |
| `frontend/lib/feature-flags.ts` | DELETE |
| `frontend/components/auth/LoginForm.tsx` | drop the flag; link always shown in login mode |
| `frontend/components/auth/EmailOtpLogin.tsx` | Turnstile + 503 + captcha-fail states |
| `frontend/components/library/blocks/TurnstileGate.tsx` | maybe move to `components/security/` (shared) |
| `frontend/lib/api.ts` | `otpRequest(email, turnstileToken)`; send `X-Client: web` |
| `frontend/app/turnstile-bridge/page.tsx` | NEW: the WebView bridge for the app (noindex; exclude from sitemap) |
| `frontend/lib/analytics/events.ts` | no new names; document the new `otp_failed.reason` values |
| `mobile/…` | auth screens built in mobile Phase 1 against this contract |
| Railway `luna-backend` | `EMAIL_OTP_MODE`, `TURNSTILE_SECRET_KEY`, cap overrides if needed |
| Supabase dashboard | template / expiry / email rate limit (B3.4) |
| Logfire | 4 alerts (B3.5) |

## Tests (pytest, `backend/tests/test_email_otp.py`)

Update:
- The allowlist tests stay but now run under `EMAIL_OTP_MODE=allowlist`.
- `test_request_feature_off_when_allowlist_unset` → `allowlist` mode with an
  empty list is still silent.
- `test_request_fails_open_without_redis` →
  `test_request_degrades_strict_without_redis` (2nd request inside 120 s → 429;
  4th in the hour → 429).

New:
1. `open` mode: an unlisted email calls GoTrue with
   `should_create_user=False` (parametrise over all three modes and assert it
   is never `True`).
2. `open` mode: GoTrue "user not found" 4xx → identical `{"sent":true}`, and
   the response does not wait on GoTrue (fake GoTrue sleeps 1 s; response
   < 200 ms).
3. `off` mode: request and verify → 503 Arabic, no GoTrue call, identical
   for existing and unknown emails.
4. Unset or garbage `EMAIL_OTP_MODE` → `allowlist`.
5. Per-email daily cap: 11th send in 24 h → `{"sent":true}` with **no**
   GoTrue call.
6. Global daily cap: at the cap → silent, no GoTrue call; the counter
   increments only on real sends (not on not-allowed, captcha-failed or
   cooldown).
7. Per-IP request cap → 429; uses `client_ip_for_rate_limit` (spoofed
   `X-Forwarded-For` ignored when CF trust is off).
8. Per-IP verify-fail cap across many emails → 429.
9. Redis raises mid-call → process-local fallback, not a pass.
10. Turnstile: required + secret set + missing or bad token → 400
    `CAPTCHA_FAILED` Arabic, no GoTrue call; `OTP_TURNSTILE_REQUIRED=false`
    → skipped; secret unset → skipped. The siteverify network error is
    fail-closed (reuse `ask_service` tests if they exist).
11. Deletion-grace account: verify succeeds with `deletion_pending=True`.
12. Span attribute `otp.outcome` set for `sent` / `capped_global` / `off`;
    no email or hash in attributes.
13. `test_public_ask*`: still passes after the Turnstile move (import path).

Frontend: `npx tsc --noEmit`, `npm run lint`. Playwright check: the login
page shows the link with no flag, and the Turnstile widget renders inside the
OTP card.

## Rollout order

0. **Owner checks first (B3.4):** template, 600 s expiry, GoTrue email rate
   limit, Resend quota. Settle open question 1 (cap value).
1. **Run Phase A validation for real**, still `allowlist`: one code email on
   desktop, then the installed iPhone app (steps 2–4 under Verification). If
   the email template is wrong, this is where it shows. Nothing public yet.
2. **Backend deploy:** hardening + `EMAIL_OTP_MODE` support, with the env
   **unset**, so behaviour equals today's allowlist. Check `list_deployments`
   first (master-pull trap) and use a clean worktree (dirty-tree rule).
3. **Turnstile:** confirm the «اسأل ريحان» widget sends tokens, then set
   `TURNSTILE_SECRET_KEY` on `luna-backend` (redeploys). Smoke-test anonymous
   ask plus allowlisted OTP.
4. **Frontend deploy** with Turnstile in `EmailOtpLogin`, **still behind the
   device flag**. The tester verifies the captcha path on desktop and in the
   installed app.
5. **Create the Logfire alerts** (B3.5).
6. **Flip `EMAIL_OTP_MODE=open`** (Railway redeploys the backend). From now
   on any existing account with the device flag can use codes. Probe with a
   non-tester dev account.
7. **Frontend deploy removing the device flag**, so the link is shown to
   everyone. Watch the alerts and `otp.outcome` for 48 h.
8. **Mobile app:** builds against the live endpoints and the
   `/turnstile-bridge` page (mobile Phase 1).

## Rollback

| Symptom | Action | Effect |
|---|---|---|
| Send spike / abuse / Resend quota burn | `EMAIL_OTP_MODE=off` (Railway redeploy, ~2–3 min) | Both routes return 503 «غير متاح — استخدم كلمة المرور»; the UI shows the password route; no emails |
| Need to narrow back to testers | `EMAIL_OTP_MODE=allowlist` | Phase A behaviour. ⚠ the public link still shows, and unlisted users get silent non-sends, so pair it with step 7 reverted or prefer `off` |
| Turnstile breaks logins (Cloudflare outage) | `OTP_TURNSTILE_REQUIRED=false` | Captcha skipped for OTP only; caps still hold |
| Frontend regression | revert the flag-removal commit and redeploy the frontend | Link back behind `?otp=1` |
| Bad backend deploy | Railway rollback to the previous deployment | env is unchanged, so the mode is preserved |

None of these touch the DB. There is no migration in this plan.

## Verification

1. pytest (above) + frontend tsc/lint; clean-worktree check before push.
2. Prod desktop, `allowlist`: the allowlisted email receives an Arabic code
   email; the code logs in; `/me` is correct; refresh still works after 1 h.
   An unlisted email gets the generic «أرسلنا الرمز» and no email.
3. **Installed iPhone app (PWA):** open `/login?otp=1` inside the app (it has
   its own storage). Request a code; iOS offers it from Mail above the
   keyboard; log in; kill the app and relaunch the next day → still logged in.
4. In the same app, try Google too and record which one works.
5. After step 6 of the rollout: a non-tester existing account gets a code; an
   unknown email gets 200 and no email, and the response time matches the
   real send (background send).
6. After step 7: the login page shows the link without a flag (screenshot);
   the Turnstile widget is invisible or managed, with no visible friction for
   normal users.
7. Redis outage drill (dev/local): stop Redis; the 2nd request inside 120 s
   gets 429 and the span shows `limiter_backend=process`.

## Owner answers (2026-10-12)

1. **Email volume:** ~100 users today; current Resend quota is enough for now
   (🔎 confirm the plan — Resend free = 100 emails/day, 3,000/month, shared with
   signup + password-reset emails). Cap: `OTP_DAILY_SEND_MAX` set BELOW the
   daily quota (proposed 60) so signup/reset emails always have room.
2. **Never depend on email alone:** password login stays available on every
   screen. When the OTP cap is reached, Resend fails, or `EMAIL_OTP_MODE=off`,
   the UI says so in Arabic and switches to «الدخول بكلمة المرور» — no
   silent wait for a code that won't come. (The request still returns
   `sent:true` to unknown emails for enumeration safety; the cap-reached state
   is a global flag exposed as `{"sent":true,"fallback":"password"}` to
   everyone, so it leaks nothing per-email.)
3. **Google + Apple sign-in:** planned for the LAST stage of the mobile build
   (both together, web + app). Not now — keep v1 to password + email code.
4. **Captcha in the app (Turnstile WebView):** ❓ not answered yet.
5. **Supabase dashboard:** live Magic Link template was the Supabase default
   (English, link only, NO `{{ .Token }}` → no code would ever arrive). Branded
   Arabic code-only replacement: `supabase/email_templates/magic_link_otp_ar.html`
   (subject «رمز الدخول إلى ريحان», says 10-minute validity → set Email OTP
   Expiration = 600, length 6). Owner pastes it in the dashboard.

## Open questions for the owner (original)

1. **Daily global send cap and Resend plan:** which Resend plan is
   `send.rayhanai.com` on (daily and monthly quota, shared with password
   reset and signup emails)? The proposed `OTP_DAILY_SEND_MAX=500` must fit
   inside it.
2. **Captcha in the app:** OK to use the Turnstile WebView bridge (a brief
   invisible check before the code is sent), instead of no captcha in the app?
   The recommendation is yes; skipping it creates a bypass for the web too.
