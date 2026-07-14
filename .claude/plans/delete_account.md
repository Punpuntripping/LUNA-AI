# Delete Account (حذف الحساب) — 30-Day Grace + Hard Purge

Status: PLANNED (not built). Author: Claude, 2026-07-12.
> **Scope expanded — see `account_settings.md`** (إعدادات الحساب dialog: change password + logout-all-devices + this delete flow). That file is the entry point; Steps 1–8 here remain the authoritative delete/purge detail. Delta: `DeleteAccountRequest.password` is now Optional — Google-OAuth-only users (no password identity, checked server-side via `admin.get_user_by_id`) confirm via type-to-confirm instead.

## Context

Luna has no way for a user to delete their account — no endpoint, no UI, no `auth.admin` usage anywhere. For a Saudi legal app with PDPL erasure obligations this is a gap. Decisions made with the user:

- **30-day grace period**: requesting deletion deactivates the account immediately; recoverable for 30 days by logging in and clicking restore; a daily job then hard-purges.
- **Password re-entry** required in the dialog; backend re-verifies via GoTrue before scheduling.
- **`llm_calls` ledger rows are RETAINED** (orphaned/pseudonymous — cost source of truth). `audit_logs` also retained (FK is SET NULL).

**Verified against live prod DB** (not just migration files): FKs referencing `users(user_id)` — CASCADE: conversations, lawyer_cases, message_feedback, pii_mappings, user_subscriptions, user_templates; SET NULL: audit_logs; **NO ACTION (block any users-row delete)**: workspace_items, user_preferences, retrieval_artifacts, blog_posts, task_state *(drift — exists only in prod, no migration file)*, plan_codes.redeemed_by. No FK at all: llm_calls, paused_runs, plan_codes.redeemed_by_users uuid[]. Storage never cascades. So today `auth.admin.delete_user` would simply fail for any real user.

**Architecture**: `users.deletion_requested_at timestamptz NULL` is the single state marker. During grace, all data routes are rejected by a zero-cost check piggybacked on `get_user_id` (already queried once per request by every data module); `/auth/*` stays reachable so the frontend can show a blocking restore screen. Daily APScheduler job (03:45 + startup catch-up, same pattern as existing 03:00/03:15/03:30 jobs in `main.py`) purges accounts past 30 days: storage → transactional purge RPC → `auth.admin.delete_user` **last** (its `auth.users → public.users` CASCADE removes the now-slim users row). The users row survives until that final step, so any partial failure is retried by the next sweep — no straggler-tracking state.

---

## Step 1 — Migration `shared/db/migrations/090_account_deletion.sql` (new)

Apply to prod via Supabase MCP `apply_migration`; header comment per 065 convention.

1. `ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at timestamptz;` + partial index `WHERE deletion_requested_at IS NOT NULL`.
2. **FK flips** (discover live constraint names first: `SELECT conname, conrelid::regclass FROM pg_constraint WHERE confrelid='public.users'::regclass AND contype='f';`):
   - `workspace_items.user_id`, `user_preferences.user_id`, `retrieval_artifacts.user_id`, `blog_posts.owner_user_id` → `ON DELETE CASCADE`
   - `plan_codes.redeemed_by` → `ON DELETE SET NULL`
   - `task_state.user_id` → CASCADE, inside a `DO $$` block guarded with `to_regclass('public.task_state')` + dynamic constraint-name lookup (drift: table has no migration file).
3. **RPC `purge_user_data(p_user_id uuid)`** — SECURITY INVOKER, `REVOKE ALL FROM PUBLIC; GRANT EXECUTE TO service_role` (065 precedent). One transaction; empties heavy children so the final auth-delete cascade is tiny; **does NOT delete the users row** (it's the idempotency marker). Ordered deletes:
   `retrieval_artifacts` (first — its `artifact_id → workspace_items` FK is NO ACTION) → `workspace_items` → `conversations` → `lawyer_cases` → `message_feedback` → `pii_mappings` → `user_templates` → `user_preferences` → `blog_posts` → `paused_runs` → `task_state` (guarded EXECUTE) → `UPDATE plan_codes SET redeemed_by_users = array_remove(redeemed_by_users, p_user_id)` (uses_count NOT decremented — capacity stays consumed). Skip: llm_calls, audit_logs (retained), user_subscriptions (cascades with users row).

## Step 2 — Backend: errors + models

- `backend/app/errors.py`: add `ACCOUNT_DELETION_PENDING` (used with 403). Bad password reuses `AUTH_INVALID` (consistent with login).
- `backend/app/models/requests.py`: `DeleteAccountRequest(password: str, min_length=1)`.
- `backend/app/models/responses.py`: extend `UserProfile`/`UserProfileResponse` with `deletion_pending: bool = False`, `deletion_requested_at: Optional[datetime] = None`, `purge_at: Optional[datetime] = None` (server-computed: `requested_at + 30 days`).

## Step 3 — `backend/app/services/account_service.py` (new)

Sync fns (routes call via `run_db` from `shared/db/run.py`). `GRACE_PERIOD_DAYS = 30` lives here.

- `schedule_account_deletion(supabase, auth_id)` — `UPDATE users SET deletion_requested_at = now() WHERE auth_id = ? AND deletion_requested_at IS NULL` (never overwrites — re-request can't extend grace) + `write_audit_log(action="delete", resource_type="account", metadata={"event": "deletion_requested"})`.
- `cancel_account_deletion(supabase, auth_id)` — set NULL, idempotent + audit `action="update"`, `{"event": "deletion_cancelled"}`.

## Step 4 — Endpoints in `backend/app/api/auth.py`

**`POST /delete-account`** (→ `/api/v1/auth/delete-account`, `SuccessResponse`): deps `get_current_user`, `get_supabase`, `get_supabase_auth`, `get_redis`.
1. Re-verify password: `_gotrue_call(supabase_auth.auth.sign_in_with_password, {"email": current_user.email, "password": body.password})` — email from the **verified JWT claim**, never the client. Error mapping mirrors login (`auth.py:81-113`): AuthApiError 400/401/403/422 → 401 `AUTH_INVALID` «كلمة المرور غير صحيحة»; retryable/timeout/unknown → 503.
2. `run_db(schedule_account_deletion, ...)`.
3. Best-effort `redis.delete(f"session:{auth_id}")` (logout pattern, auth.py:258).

No GoTrue global sign-out: the Step-5 gate is authoritative server-side; a still-valid JWT can only reach `/auth/*` — exactly the surface a pending user needs for restore.

**`POST /restore-account`** (`SuccessResponse`): authenticated, no body; `run_db(cancel_account_deletion, ...)`; idempotent. Grace is effectively "at least 30 days" (restore works until the sweep actually runs) — fine.

**Extend `GET /me`**: add `deletion_requested_at` to the users select; populate the 3 new fields. `/me` must never 403 for pending users — the blocking screen depends on it.

**Extend `POST /login`**: add `get_supabase` dep + one service-role `users` query by auth_id (login currently never touches `public.users`, builds UserProfile from GoTrue only — auth.py:138-148); populate the same fields so a pending user lands directly on the blocking screen.

## Step 5 — Deactivation gate: `backend/app/services/case_service.py:30` `get_user_id`

Change select to `"user_id, deletion_requested_at"`; if set → raise 403 `ACCOUNT_DELETION_PENDING` «الحساب قيد الحذف — يمكنك استعادته أو تسجيل الخروج». This one helper is imported by every data module (~75 call sites) and already queries `users` per request → zero extra round-trips, no middleware. Auth routes don't use it, so login//me//logout//restore stay reachable.

## Step 6 — `shared/storage/client.py`: `delete_folder_recursive(bucket, prefix) -> int`

Existing `delete_folder` (line 148) is single-level and swallows errors. New fn: BFS via `.list(folder, {limit: 1000, offset})` pagination (entries with `id is None` = subfolders → enqueue), batch `.remove()` in chunks of 100, **raises on failure** — the purge must not delete DB rows if files were left behind (once case_ids are gone the prefixes are unrecoverable). Leave `delete_folder` untouched.

## Step 7 — Purge job: `backend/app/services/account_purge_service.py` (new)

`purge_expired_accounts(supabase) -> dict` (sync, run via `asyncio.to_thread`):
1. Select `user_id, auth_id` from users where `deletion_requested_at <= now() - 30 days` (partial index).
2. Per user in try/except (log + continue — one failure never stops the sweep):
   a. Capture `case_ids` from `lawyer_cases` (**no deleted_at filter** — soft-deleted cases still own storage) **before** any deletion.
   b. `delete_folder_recursive("documents", f"general/{user_id}")` + per case `f"cases/{case_id}"`.
   c. `supabase.rpc("purge_user_data", {"p_user_id": user_id})`.
   d. `supabase.auth.admin.delete_user(auth_id)` — **terminal step**; cascade removes users row + user_subscriptions. First `auth.admin` use in codebase; service-role client has the privilege.
   e. Audit via **direct insert** into `audit_logs` with `user_id` omitted (`write_audit_log` requires user_id, which would now violate the FK): `{action: "delete", resource_type: "account", resource_id: user_id, metadata: {event: "purged", auth_id}}`.
3. Return `{scanned, purged, failed}`.

Idempotency: users row dies only in (d), in the same DB transaction as the auth row; (a)–(c) are safe re-runs → next sweep retries the whole user. No "auth user without users row" state is reachable.

## Step 8 — Scheduler wiring: `backend/app/main.py`

Follow the exact existing pattern (lines 150-258): `_run_account_purge` via `asyncio.to_thread`, `CronTrigger(hour=3, minute=45)` id `account_purge`, plus one-shot `DateTrigger` startup catch-up (~90-120s, like the reconciler at line ~214). Update the "Scheduler started" summary log.

## Step 9 — Frontend

- **`frontend/types/index.ts`** — `User` += `deletion_pending?: boolean; deletion_requested_at?: string | null; purge_at?: string | null;`
- **`frontend/lib/api.ts`** `authApi` (line ~354): `deleteAccount(password)` → `api.post("/auth/delete-account", {password})`; `restoreAccount()` → `api.post("/auth/restore-account")`.
- **`frontend/stores/auth-store.ts`**: `deleteAccount(password)` — call API (errors propagate to dialog), then reuse the exact logout teardown tail (lines 176-181: cancelProactiveRefresh, clearTokens, supabase.auth.signOut, preferences reset, user null). `restoreAccount()` — call API then refresh via `authApi.me()` + `setUser`.
- **`frontend/components/Settings/DeleteAccountDialog.tsx`** (new) — controlled `Dialog` (needs a form input, so Dialog not AlertDialog; RedeemCodeDialog is the template), `dir="rtl"`, destructive styling per `ConversationSettingsDialog.tsx:165-192`. Copy: «سيتم إلغاء تنشيط حسابك فورًا وحذفه نهائيًا بعد ٣٠ يومًا، بما في ذلك جميع القضايا والمحادثات والمستندات. يمكنك استعادة الحساب خلال هذه الفترة بتسجيل الدخول مجددًا.» Password field, destructive confirm disabled while empty, loading state; 401 → «كلمة المرور غير صحيحة», 429 → rate-limit copy. On success: `deleteAccount(password)` then `router.push("/login")`. data-testids: `delete-account-dialog/-password/-confirm`.
- **`frontend/components/sidebar/SidebarFooter.tsx`** — after the privacy row (~line 161): `<Separator />` + destructive row (UserX icon, `text-destructive`, «حذف الحساب», `data-testid="sidebar-settings-delete-account"`); mount the dialog next to the others.
- **`frontend/components/auth/AccountDeletionPendingScreen.tsx`** (new) — full-screen RTL card: «حسابك قيد الحذف», purge date from `user.purge_at` (server-computed, no client date math), primary «استعادة الحساب» → `restoreAccount()` (on success the gate turns false and the app renders), ghost «تسجيل الخروج» → logout + `/login`.
- **`frontend/components/auth/AuthGuard.tsx`** — select `user` from the store (line 44); after the isLoading block (line 131-140): `if (isAuthenticated && user?.deletion_pending && !isPublic) return <AccountDeletionPendingScreen />;`

## Rate limiting

None needed — both endpoints live under `/api/v1/auth/` → existing strict 10/min window (`middleware/rate_limit.py:69`).

## Tests & Verification

**Backend** — `backend/tests/test_account_deletion.py` (scripted-fake-Supabase convention of `test_blog_import.py`):
1. Both routes present in `create_app().routes`.
2. `get_user_id` → 403 `ACCOUNT_DELETION_PENDING` when row carries `deletion_requested_at`.
3. `schedule_account_deletion` filters on `deletion_requested_at IS NULL` (no overwrite).
4. `purge_expired_accounts`: call order storage → rpc → `auth.admin.delete_user` → audit insert without `user_id`; storage raise → admin delete NOT called, sweep continues to next user.
5. delete-account endpoint: fake `AuthApiError(400)` → 401 `AUTH_INVALID`; success → timestamp set + Redis session key deleted.

**Frontend** — `npx tsc --noEmit` + `npm run lint` (no test runner exists).

**Manual E2E** (local uvicorn + npm run dev, throwaway account):
1. Sign up; create case + conversation; upload one doc (populates `general/{uid}/` and `cases/{cid}/`).
2. Settings popover → حذف الحساب: wrong password → inline error; correct → lands on /login; SQL-verify `deletion_requested_at` set.
3. Log in again → blocking screen with purge date; `GET /api/v1/cases` with that token → 403 `ACCOUNT_DELETION_PENDING`.
4. «استعادة الحساب» → app restored, data intact, column NULL.
5. Purge: re-request deletion, backdate via SQL (`- interval '31 days'`), restart uvicorn, wait for startup catch-up. Verify: users row gone; child tables empty; **llm_calls rows still present**; audit_logs present (user_id NULL) + purge audit row; both storage prefixes empty; login fails; `plan_codes.redeemed_by_users` scrubbed.

## Build order

1. Migration 090 (discover live FK names → write → apply via MCP)
2. errors + models → account_service + auth.py endpoints (/me, /login extensions)
3. `get_user_id` gate
4. `delete_folder_recursive` → purge service → scheduler wiring
5. Backend tests → pytest
6. Frontend: types → api.ts → auth-store → DeleteAccountDialog → SidebarFooter → PendingScreen → AuthGuard gate
7. tsc + lint → manual E2E

Deploy note: NOT deployed until asked; migration 090 touches prod DB at build time (house convention).
