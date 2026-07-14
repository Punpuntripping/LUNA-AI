# إعدادات الحساب — Account Settings (Change Password · Logout All Devices · Delete Account)

Status: **BUILT 2026-07-13 — NOT committed, NOT deployed.** Migration 090 IS applied to prod. Author: Claude, 2026-07-12. Supersedes `delete_account.md` (delete-account plan absorbed here unchanged unless noted).

## Build outcome (2026-07-13)

Built by 3 parallel agents + migration. 18 new tests in `backend/tests/test_account_settings.py` (152 backend tests pass; the 2 failures in `test_wave_8b_legacy_removal.py` are pre-existing — they grep `agents/agent_writer/publisher.py`, which is not in the repo). `npx tsc --noEmit` + `npm run lint` clean.

Deviations from the plan as written:
- `account_service.get_account_user_id()` added — an UNGATED user_id lookup. The auth surface must resolve a pending user (audit rows, restore), and routing that through the now-gated `case_service.get_user_id` would 403 a user *after* their password had already been changed.
- change-password maps GoTrue 400/422 on the *new* password (e.g. same as current) to 400 VALIDATION_ERROR «تعذّر تحديث كلمة المرور. اختر كلمة مرور مختلفة» rather than the blanket 503.
- `_verify_password` runs on a THROWAWAY anon client (`shared/db/client.create_isolated_anon_client`), not `app.state.supabase_auth` — see the finding below.
- Frontend formats `purge_at` with `ar-EG`, not `ar-SA`: `ar-SA` resolves to the Umm al-Qura calendar in V8 and would render the Gregorian purge date as a Hijri one.
- DeleteAccountDialog falls back to the password variant on a 422, so a stale client-side provider read can't dead-end the user.

## ⚠ Pre-existing bug found during the build (NOT fixed — needs a decision)

`app.state.supabase_auth` is an **lru_cached singleton anon client shared by every request**. `sign_in_with_password` parks the resulting session in that client's in-memory GoTrue store, and `auth.sign_out()` (`supabase_auth/_sync/gotrue_client.py:789`) reads *whatever session is parked there* and revokes it with **scope="global"**.

So today: user A logs in (their session is parked) → user B calls `POST /auth/logout` → GoTrue globally revokes **user A's** refresh tokens. A gets signed out on every device, seemingly at random. `/logout` swallows the error and returns 200, so nothing surfaces.

This build does not make it worse — the new re-auth path deliberately uses an isolated client — but `/login` still parks sessions and `/logout` still acts on them.

Suggested fix (one line, not applied): `/logout` should call `supabase.auth.admin.sign_out(raw_jwt, "local")` on the service-role client — targeting the caller's own token — exactly as `/logout-all` does with `"global"`. The `_raw_jwt(request)` helper already exists in `auth.py`.

## Context

The SidebarFooter settings popover gets a new entry **«إعدادات الحساب»** opening an `AccountSettingsDialog` with three capabilities: change password, log out of all devices, and delete account (30-day grace — full design below, carried over from the delete-account plan). 

**Google OAuth constraint (verified)**: Google sign-in exists (`frontend/components/auth/LoginForm.tsx:184` `signInWithOAuth({provider:"google"})`). OAuth-only users have **no password identity**, so: change-password is hidden/rejected for them, and delete-account confirmation falls back to a type-to-confirm phrase instead of password re-entry. Identity check is done **server-side** via `supabase.auth.admin.get_user_by_id(auth_id)` → `identities[].provider` (never trust the client's claim); the frontend reads `session.user.app_metadata.providers` from the Supabase JS session purely for show/hide.

**Installed API surface (verified in `.venv/Lib/site-packages/supabase_auth/_sync/gotrue_admin_api.py`)**: `admin.sign_out(jwt, scope)` (line 70, scopes global/others/local — revokes refresh tokens), `admin.update_user_by_id(uid, attributes)` (line 165, accepts `{"password": ...}`), `admin.delete_user(id)` (line 184). First `auth.admin` usage in the codebase; service-role client (`app.state.supabase`) has the privilege.

**Stateless-JWT caveat** (existing known limitation): revoking refresh tokens doesn't invalidate already-issued access tokens — other devices stay live up to ~1h until their token expires and refresh fails. UI copy must not promise "instant".

**Password rule** (mirror signup, `LoginForm.tsx:46-49`): min 8 chars, message «كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل».

---

## Part A — Delete account (30-day grace + purge) — UNCHANGED except confirmation

Everything from `delete_account.md` Steps 1–8 carries over verbatim (migration 090 with FK flips + `purge_user_data` RPC; `deletion_requested_at` marker; `get_user_id` 403 gate; `delete_folder_recursive`; purge job at 03:45; restore endpoint; `/me` + `/login` extensions). **One change** to `POST /auth/delete-account`:

- `DeleteAccountRequest.password` becomes `Optional[str]`.
- Endpoint first calls `has_password_identity(supabase, auth_id)` (new helper, see Part D): if **true** → password required (422 if missing) and verified via `sign_in_with_password` as planned; if **false** (Google-only) → password verification skipped — the authenticated JWT + the UI type-to-confirm is the confirmation. Server decides; client input never chooses the branch.
- `DeleteAccountDialog`: if session providers include `"email"` → password field (as planned); else → type-to-confirm field: confirm button enabled only when input equals «حذف حسابي».

## Part B — `POST /api/v1/auth/change-password`

**Request**: `ChangePasswordRequest(current_password: str, new_password: str = Field(min_length=8))` in `backend/app/models/requests.py`.

**Endpoint** in `backend/app/api/auth.py` (deps: `get_current_user`, `get_supabase`, `get_supabase_auth`, `Request`):
1. `has_password_identity(...)` — if false → 400 `VALIDATION_ERROR` «هذا الحساب مسجّل عبر Google ولا يملك كلمة مرور».
2. Verify current: `_gotrue_call(supabase_auth.auth.sign_in_with_password, {"email": current_user.email, "password": body.current_password})` — email from verified JWT claim. Error mapping mirrors login (auth.py:81-113); wrong password → 401 `AUTH_INVALID` «كلمة المرور الحالية غير صحيحة».
3. `run_db` → `supabase.auth.admin.update_user_by_id(auth_id, {"password": new_password})`. (Admin update may bypass GoTrue's policy — Pydantic min_length=8 is the enforcement.)
4. Best-effort security tail: `supabase.auth.admin.sign_out(raw_jwt, scope="others")` — kills other devices' refresh tokens after a password change (log-and-continue on failure). Raw JWT re-extracted from the `Authorization` header (same split as `deps.py` `get_current_user`).
5. `write_audit_log(action="update", resource_type="account", metadata={"event": "password_changed"})`. Return `SuccessResponse`.

Current session stays valid (scope="others") — the user is not logged out by their own password change.

## Part C — `POST /api/v1/auth/logout-all`

Small — kept (the user offered to drop it if heavy; it isn't: one admin call).

**Endpoint** (deps: `get_current_user`, `get_supabase`, `get_redis`, `Request`):
1. `supabase.auth.admin.sign_out(raw_jwt, scope="global")` via `run_db` — revokes ALL refresh tokens including the current device's. Unlike `/logout` (which returns 200 even degraded), failure here → 503 `SERVICE_UNAVAILABLE`: the whole point is killing other sessions, a silent no-op would be false safety.
2. Best-effort `redis.delete(f"session:{auth_id}")`.
3. Audit `{"event": "logout_all_devices"}`. Return `SuccessResponse`.

**Frontend**: on success run the logout teardown tail (auth-store lines 176-181) + `router.push("/login")` — the current session's refresh token is dead, so a clean local logout is the honest UX.

Note: existing `/logout`'s `supabase_auth.auth.sign_out` on the session-less anon client is effectively a no-op (pre-existing quirk, out of scope) — do NOT copy that pattern; the admin call with the user's JWT is the one that actually revokes.

## Part D — Shared helper: `has_password_identity`

In `backend/app/services/account_service.py`:
```python
def has_password_identity(supabase, auth_id: str) -> bool:
    user = supabase.auth.admin.get_user_by_id(auth_id).user
    return any(i.provider == "email" for i in (user.identities or []))
```
Used by delete-account and change-password. Authoritative (live GoTrue state, not the ≤1h-stale JWT).

## Part E — Frontend: `AccountSettingsDialog` (إعدادات الحساب)

**`frontend/components/Settings/AccountSettingsDialog.tsx`** (new) — controlled Dialog (RedeemCodeDialog template), `dir="rtl"`, three sections:

1. **تغيير كلمة المرور** — current/new/confirm password fields, zod: new min 8 (same Arabic message as signup), confirm must match («كلمتا المرور غير متطابقتين»). Submit → `authApi.changePassword(...)`; 401 → «كلمة المرور الحالية غير صحيحة» inline; success → green inline confirmation + clear fields. **Section hidden entirely when session providers lack "email"** — resolve on dialog open via `supabase.auth.getSession()` → `session.user.app_metadata.providers`.
2. **الجلسات** — «تسجيل الخروج من جميع الأجهزة» button + nested AlertDialog confirm («سيتم تسجيل خروجك من جميع الأجهزة بما فيها هذا الجهاز.») → `authApi.logoutAll()` → teardown + `/login`.
3. **منطقة الخطر** — destructive «حذف الحساب» button → opens `DeleteAccountDialog` (Part A variant logic).

**`frontend/components/sidebar/SidebarFooter.tsx`** — one new popover row «إعدادات الحساب» (UserCog icon, `data-testid="sidebar-settings-account"`), placed with the other account rows (after تفعيل برمز); mounts the dialog. (Replaces the previously planned bare حذف الحساب row.)

**`frontend/lib/api.ts`** `authApi` additions:
```ts
changePassword: (current_password: string, new_password: string) =>
  api.post<{ success: boolean }>("/auth/change-password", { current_password, new_password }),
logoutAll: () => api.post<{ success: boolean }>("/auth/logout-all"),
deleteAccount: (password?: string) => api.post<{ success: boolean }>("/auth/delete-account", { password }),
restoreAccount: () => api.post<{ success: boolean }>("/auth/restore-account"),
```
Plus auth-store `deleteAccount`/`restoreAccount` actions and the `AccountDeletionPendingScreen` + `AuthGuard` gate exactly as in the delete-account plan.

## Rate limiting

All four endpoints under `/api/v1/auth/` → existing strict 10/min window. No work.

## Tests & Verification (delta over delete-account plan)

**Backend** (`backend/tests/test_account_deletion.py` + `test_account_settings.py`, scripted-fake convention):
- change-password: OAuth-only fake (identities=[google]) → 400; wrong current → 401 AUTH_INVALID; success → `update_user_by_id` called with new password, then `admin.sign_out(scope="others")`.
- logout-all: admin sign_out raises → 503 (NOT 200); success → Redis key deleted.
- delete-account: password-identity fake without password in body → 422; Google-only fake without password → proceeds.

**Manual E2E additions**:
1. Email account: change password (wrong current → error; success), logout, log back in with NEW password.
2. After change-password on device A, device B's session dies within ~1h (or immediately on next refresh) — spot-check via second browser profile.
3. logout-all from device A with device B logged in → A lands on /login; B bounces to /login on next refresh.
4. Google-only account: dialog shows no password section; delete flow shows type-to-confirm «حذف حسابي»; deletion schedules successfully without password.

## Build order (full feature)

1. Migration 090 (unchanged)
2. Backend: errors/models → `account_service.py` (schedule/cancel + `has_password_identity`) → auth.py endpoints: delete-account (optional password), restore-account, change-password, logout-all; `/me` + `/login` extensions
3. `get_user_id` gate → `delete_folder_recursive` → purge service → scheduler
4. Backend tests → pytest
5. Frontend: types → api.ts → auth-store → `AccountSettingsDialog` → `DeleteAccountDialog` (two confirm variants) → SidebarFooter row → `AccountDeletionPendingScreen` → AuthGuard gate
6. tsc + lint → manual E2E

Deploy note: NOT deployed until asked; migration 090 touches prod DB at build time.
