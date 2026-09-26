# «الدخول برمز عبر البريد» — Email OTP Login (dev-gated)

**Status:** PLANNED 2026-09-24 · nothing built
**Why:** in the installed iOS app (PWA, `pwa_step1.md`) Google OAuth may bounce
to Safari. Safari has a separate storage jar, and `/auth/callback`'s PKCE
verifier cookie lives in the context that started the flow — so a login that
finishes in Safari leaves the app logged out (or fails outright). A 6-digit
emailed code is typed INTO the app: no redirect, no second context, works on
every device.
**Rejected:** "log in in the browser, hand the session to the app" — complex,
and a pairing link is a session-phishing vector (attacker sends a victim a
link bound to the attacker's nonce, victim logs in, attacker gets the session).

## Phase A — dev-only (this plan)

Visible and usable ONLY for allowlisted dev emails, so we can test on real
phones and iterate before anyone else sees it.

### Why the gate can't be "dev plan"

The login screen runs BEFORE we know who the user is, and `plan_id = 'dev'`
rows are expired (latest expiry 2026-09-16 → they fall back to free). So the
gate is two independent layers:

1. **Server (the real gate):** env allowlist `EMAIL_OTP_ALLOWED_EMAILS`
   (comma-separated, case-insensitive) on luna-backend. Not on the list → no
   email is sent, ever. Empty/unset → feature off.
2. **UI (visibility only):** the «الدخول برمز» option renders only when the
   device flag `rayhan.ff.email_otp = "1"` is in localStorage. A tester sets it
   once by opening `/login?otp=1` (and clears with `?otp=0`). The flag is not a
   security boundary — layer 1 is.

Phase B (later, after testing): drop the device flag, show the option inside
the installed app (`isStandalone()`) for everyone, and replace the allowlist
with "any existing account".

## Backend

Mirror `POST /login` (`backend/app/api/auth.py`): same response model, same
GoTrue error mapping, same `_gotrue_call` wrapper.

**Refactor first:** extract the `LoginResponse` building tail of `login()`
(users-row read, name resolution, deletion state, profession) into
`_build_login_response(session, user, *, has_password)`. `/login` passes
`True`; OTP resolves it via the has-password RPC (migration 141) like `/me` —
an OTP login proves nothing about a password.

### `POST /api/v1/auth/otp/request` — body `{email}`
- Normalise email (strip + lower). Not in allowlist → **same 200 response**
  as success (`{"sent": true}`) and log `auth.otp.not_allowed`; never reveal
  membership.
- `supabase_auth.auth.sign_in_with_otp({"email": e, "options": {"should_create_user": False}})`
  — **no signups** through this path. Unknown email → GoTrue error → same
  generic 200.
- Rate limit: reuse the login limiter pattern — 1 request / 60s and 5 / hour
  per email, plus the per-IP limit. Over limit → 429 «انتظر قليلاً قبل طلب رمز جديد».
- Audit log `auth.otp.requested` (IP via `resolve_client_ip` only).

### `POST /api/v1/auth/otp/verify` — body `{email, code}`
- Allowlist re-checked (defence in depth).
- `supabase_auth.auth.verify_otp({"email": e, "token": code, "type": "email"})`.
- Wrong/expired code → 401 «الرمز غير صحيح أو منتهي الصلاحية». 5 failed verifies
  per email per 15 min → 429 (GoTrue also limits; ours makes the message Arabic).
- Success → `_build_login_response(...)` — identical shape to `/login`, so the
  frontend treats it as a normal login (refresh token, deletion-pending screen,
  subscription load all unchanged).
- Audit `auth.otp.verified` / `auth.otp.failed`.
- Same GoTrue session-singleton caveat as `/login` (see the note near line
  115/442 in auth.py) — use the same per-request auth client.

### Config
`shared/config.py`: `EMAIL_OTP_ALLOWED_EMAILS: Optional[str] = None` + a parsed
`email_otp_allowlist` property (frozenset, lowercased).

### Tests — `backend/tests/test_email_otp.py`
Allowlisted → sign_in_with_otp called with `should_create_user=False`; not
allowlisted / unknown → identical 200 and NO GoTrue call; verify success →
LoginResponse shape equals `/login`'s; bad code → 401 Arabic; rate limits;
`has_password` resolved, not hard-coded.

## Supabase (dashboard / Management API — one-time)

- **Magic Link email template** must contain `{{ .Token }}` — that's what
  `signInWithOtp` sends to an existing user. Nothing in the app uses magic
  links today (grep: no `signInWithOtp`), so changing it breaks nothing.
  Arabic, RTL, Rayhan-branded, matching the password-reset email: «رمز الدخول
  إلى ريحان: 123456 — صالح لمدة 10 دقائق. إن لم تطلبه فتجاهل هذه الرسالة.»
  Do NOT include the magic link itself (a link opened from Mail lands in
  Safari — the exact problem we're avoiding).
- Auth settings: OTP expiry → **600s**; confirm OTP length (6) — the UI must
  match whatever it is.
- Sending goes through the existing Resend SMTP (send.rayhanai.com) — check
  the SMTP rate limit in Auth settings won't throttle testing.

## Frontend

- `lib/api.ts` `authApi.otpRequest(email)`, `authApi.otpVerify(email, code)`.
- `stores/auth-store.ts` `loginWithOtp(email, code)` — calls verify, then runs
  the SAME post-login path as `login()` (store tokens in memory, set refresh
  token, load user). Extract that shared tail rather than duplicate it.
- `lib/feature-flags.ts` (tiny): `isEmailOtpEnabled()` reads
  `rayhan.ff.email_otp` (try/catch); `/login` page effect applies
  `?otp=1|0` then strips the param.
- `components/auth/EmailOtpLogin.tsx` — two steps inside the login card:
  1. email → «أرسل الرمز»
  2. 6 boxes (single `<input inputMode="numeric" autoComplete="one-time-code"
     maxLength={6}>` styled as boxes — iOS offers the code from Mail above the
     keyboard) → auto-submit on 6th digit, «تأكيد» button as fallback.
     «إعادة الإرسال» with a 60s countdown, «تغيير البريد» back link.
  Latin digits only; font ≥16px (no iOS zoom); errors in Arabic from the API.
- `LoginForm.tsx`: when the flag is on, a secondary link under the password
  form «الدخول برمز عبر البريد» switches to `EmailOtpLogin`. Register mode
  unchanged. Google button unchanged in Phase A (we are testing both side by
  side).
- After login: same redirect logic as password login (safe-next /
  post-login-intent).

## Analytics
`track()`: `otp_requested`, `otp_verified`, `otp_failed{reason}` — AND add them
to backend `EVENT_NAMES` (new names are dropped silently otherwise).

## Verification (dev accounts only)

1. Backend pytest + frontend tsc/lint; clean-worktree check before push.
2. Prod, desktop: `/login?otp=1` → option visible; non-allowlisted email →
   generic «أرسلنا الرمز» and no email arrives; allowlisted → email with code
   arrives in Arabic, code logs in, `/me` correct, refresh works after 1h.
3. **Installed iPhone app (the point of all this):** set flag via
   `/login?otp=1` INSIDE the app (the app has its own localStorage — setting
   it in Safari does nothing), request code, iOS suggests it from Mail above
   the keyboard, logs in, kill + relaunch next day → still logged in.
4. Same installed app: try Google too and record which works → decides Phase B.
5. Without the flag: login page identical to today (screenshot diff).

## Rollout knobs
- Add/remove testers: edit `EMAIL_OTP_ALLOWED_EMAILS` (use `--skip-deploys`
  then a normal deploy, or accept the redeploy — see master-pull trap).
- Kill switch: unset the env var → requests become no-ops, UI still shows the
  option to flagged devices but no email is sent. (Phase B removes the flag.)

## File manifest

| File | Change |
|---|---|
| `backend/app/api/auth.py` | `_build_login_response` refactor + 2 routes |
| `shared/config.py` | `EMAIL_OTP_ALLOWED_EMAILS` + parsed property |
| `backend/app/services/analytics_service.py` | 3 event names |
| `backend/tests/test_email_otp.py` | NEW (force-add; `.gitignore` has `backend/tests/*`) |
| `frontend/lib/api.ts` | 2 auth calls |
| `frontend/stores/auth-store.ts` | `loginWithOtp` + shared post-login tail |
| `frontend/lib/feature-flags.ts` | NEW |
| `frontend/components/auth/EmailOtpLogin.tsx` | NEW |
| `frontend/components/auth/LoginForm.tsx` | switch link, flag-gated |
| `frontend/lib/analytics/events.ts` | 3 event names |
| Supabase: Magic Link template + OTP expiry | dashboard / Management API |
| Railway: `EMAIL_OTP_ALLOWED_EMAILS` on luna-backend | env |
