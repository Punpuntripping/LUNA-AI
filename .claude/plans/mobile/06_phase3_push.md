# Mobile — Phase 3: Push «إجابتك جاهزة»

**Status (2026-10-25):** APPROVED by the owner (O1–O4 in §5, all as recommended) → build started, code
only. Applying the migration, deploying the backend and starting a native build each need their own go.
Marks: ✅ decided · ❓ owner decision · 🔎 verify before relying on it. Scope: `01_app_scope.md` §Push
(Expo Push Service, content-free payload as on the web). Phases: `03` §3. Hand-off from `05` (B5).

## 1. Goal + exit criteria
A lawyer starts a deep search, locks the phone or switches app, and gets a notification **«ريحان —
إجابتك جاهزة»** when the answer lands. Tapping it opens that conversation with the answer.

1. **Opt-in, same as the web.** No cold permission prompt at launch.
   - The deep-search tracker shows the chip «نبّهني عند الجاهزية» (web `PushReadyChip`).
   - Settings gets a «الإشعارات» row (web `ConversationSettingsDialog`).
   - Android 13+ shows the system prompt when the user taps either one.
2. **Delivery.** Each registered device of the account gets the push, under the **same conditions as web
   push** (`message_service.py:1437-1444`):
   - the turn took longer than 20 s, or the client disconnected;
   - never for a paused turn (a question, not an answer).
3. **Content-free payload.** Title «ريحان», body «إجابتك جاهزة», and `data.conversation_id` only. No
   question, answer or conversation title ever goes to Expo, Google or Apple (PDPL, وضع السرية).
4. **Tap.** From killed, background or foreground, the tap opens `/c/<id>`. Signed out → login → that
   conversation.
5. **Foreground.** While that conversation is on screen there is no banner, and the answer streams as
   usual. On another screen a banner shows and the conversation list refreshes.
6. **Sign-out** removes the device's token. Signing in as someone else on the same phone moves the token
   to the new account.
7. **Web push unchanged.** Same payload, same table, same behaviour.

## 2. Dependencies
- **Android:** new native module `expo-notifications`, so a **new Android dev build** is needed (EAS
  build credit). The FCM V1 service-account key must be uploaded to EAS (see §4).
- **iOS:** needs the APNs key, which needs the Apple Developer account, which waits on D-U-N-S. **The
  same code serves iOS.** Only the key and an iOS build are missing; iOS sign-off comes later.
- `expo-device` and `expo-linking` are already installed. The deep-link route `c/[id]` exists from
  Phase 2 (M1).
- Backend deploy: `luna-backend` (watch paths `/backend /shared`). The **migration must be applied
  BEFORE the deploy** (the [[moyasar]] trap).

## 3. Work items

### B — Backend (`@sql-migration` + `@fastapi-backend`, after the owner's OK)
- **B1 Migration `185_app_push_tokens.sql`** (✅ 185 is free in prod as of 2026-10-25; the latest applied
  is 184). A new table, not columns on `push_subscriptions`: that table has `p256dh` / `auth` NOT NULL and
  a URL-allowlist model that doesn't fit.
  - `app_push_tokens(id uuid pk, user_id uuid → users ON DELETE CASCADE, token text UNIQUE,`
    `platform text check in ('android','ios'), app_version text, created_at, last_success_at,`
    `failure_count int default 0)`.
  - Index on `user_id`. RLS on, with self-only select / insert / delete policies (copy 165). anon revoked.
  - Add the table to the nuke-account checklist.
- **B2 `push_service` — app half.** Mirrors the web half, in the same file or `app_push_service.py`.
  - `upsert_app_token` (ON CONFLICT token → re-own to the caller, reset `failure_count`, trim to
    `MAX_SUBS_PER_USER` = 5 per user) and `delete_app_token(user_id, token)`.
  - Token format check: `^ExponentPushToken\[[A-Za-z0-9_-]+\]$` (🔎 the exact charset).
  - Sender `notify_turn_ready_app`, async and never raises:
    - ONE `httpx.AsyncClient` POST to `https://exp.host/--/api/v2/push/send` (fixed host, so no SSRF),
      batch ≤ 100 (🔎).
    - Header `Authorization: Bearer $EXPO_ACCESS_TOKEN` when set.
    - Message: `{to, title:"ريحان", body:"إجابتك جاهزة", data:{conversation_id}, channelId:"turn-ready",
      priority:"high", sound:"default", ttl:21600}`.
  - Bookkeeping per push ticket:
    - `ok` → `last_success_at`;
    - `DeviceNotRegistered` → delete the row;
    - any other error → `failure_count`, deleting the row at 5.
  - Push receipts (`/getReceipts`, which report late FCM/APNs errors) are deferred to v2. The ticket
    errors plus the failure counter cover v1. 🔎
  - Logfire: `push.sent` / `push.failed` / `push.pruned` gain `channel="expo"`. Never log the token.
- **B3 `schedule_turn_ready` split.** Today it returns early when VAPID is unset.
  - New: run the web half `if VAPID`, and the app half `if EXPO_PUSH_ENABLED`.
  - Same trigger, same fire-and-forget, so `message_service` is NOT touched.
  - `EXPO_PUSH_ENABLED` defaults to **false**, so it can deploy dark and be flipped after the device test.
- **B4 Routes** in `api/push.py`. Authed, global rate limit, Arabic errors, additive only (old app
  versions never call them):
  - `POST /api/v1/push/app-token` with body `{token, platform, app_version?}` → 204, or 422 on a bad
    token.
  - `DELETE /api/v1/push/app-token` with body `{token}` → 204, idempotent.
- **B5 Config:** `EXPO_PUSH_ENABLED: bool = False` and `EXPO_ACCESS_TOKEN: Optional[str]` in
  `shared/config.py`. Mind the [[validation-alias]] trap: the env var name must equal the alias.
- **B6 Tests:** extend `backend/tests/test_push.py`:
  - token validation, upsert re-own and trim, ownership on delete;
  - sender bookkeeping per ticket status, with Expo mocked via httpx MockTransport;
  - the web half unchanged when Expo is on or off;
  - the payload carries no content.

### C — Contracts (`frontend/lib/contracts/push.ts`, additive)
- `AppPushTokenRequest`, `AppPushTokenDelete`, and `TURN_READY_DATA` key names (`conversation_id`).
- The web rebuild is harmless: types only.

### P — App (`@expo-mobile`)
- **P1 Native setup.** Add `expo-notifications` and its config plugin.
  - Android small icon: a **monochrome** PNG. A temporary white leaf now; the real one with Phase 5
    brand assets.
  - Accent colour from the theme.
  - Then a new Android dev build.
- **P2 Channel.** Android channel `turn-ready`, named «إجابات ريحان», importance HIGH, created at startup.
- **P3 Registration** (`src/push/`):
  - `getExpoPushTokenAsync({ projectId })` → `POST /push/app-token`.
  - Runs on sign-in and app start, and only when the permission is granted.
  - Also on `addPushTokenListener` (token rotation).
  - The last-sent token and user id are cached in SecureStore, so it re-posts only when they change.
- **P4 Opt-in UI** (web parity, same texts):
  - the chip «نبّهني عند الجاهزية» → «سننبّهك» on `DeepSearchProgress` while running, hidden once
    granted or after a refusal;
  - the settings row «الإشعارات» with the web's explanation line;
  - when blocked: «الإشعارات محظورة؛ لتفعيلها اسمح بها من إعدادات الجهاز» plus a button to open the
    system settings (`Linking.openSettings`).
- **P5 Handling.**
  - `setNotificationHandler`: no banner when the pushed `conversation_id` is the open chat; otherwise a
    banner. In both cases, invalidate that conversation's messages and the list.
  - Tap: `useLastNotificationResponse` (cold start) plus a response listener → `router.push('/c/<id>')`.
  - Signed out → keep the target and open it after login.
- **P6 Sign-out:** best-effort `DELETE /push/app-token` BEFORE clearing the session
  (`src/auth/session-store.ts`). A forced sign-out (a password change elsewhere) leaves the row; it's
  content-free, and the next sign-in re-owns it. ❓ O4
- **P7 Privacy:** the token is a device identifier, so declare it in the store forms (Phase 5) and in
  the privacy policy (`00` §5).

## 4. Owner tasks
1. **OK the backend change** (B1–B6).
2. **Upload the FCM V1 key to EAS:** expo.dev → `@rayhan-ai/rayhan` → Credentials → Android
   `com.rayhanai.app` → FCM V1 service account key. Use the JSON in `%USERPROFILE%\.secrets\rayhan\`.
   `eas credentials` is interactive only.
3. **Expo «Enhanced push security»** (recommended). Without it, anyone who learns a device token can
   push to that phone in our name.
   - Turn it on in the expo.dev project settings.
   - Create an access token → Railway `luna-backend` env `EXPO_ACCESS_TOKEN`, with `--skip-deploys`
     until B ships.
4. Later, with the Apple account: an APNs key (EAS can create it during the first iOS build), then an
   iPhone test.

## 5. Owner decisions — ✅ answered 2026-10-25
✅ O1 approved · ✅ O2 web-parity chip + settings row · ✅ O3 enhanced push security ON
(`EXPO_ACCESS_TOKEN`) · ✅ O4 leave the token on a forced sign-out (no unauthenticated delete route).
The original options, for the record:
- **O1 Backend change**: approve B1–B6 (new table + sender + 2 routes; web push untouched).
- **O2 Opt-in style**: web-parity chip + settings row (recommended), OR the system prompt right after
  the first login. The prompt gets more opt-ins; the chip asks when the value is obvious (a deep search
  is running).
- **O3 Enhanced push security**: on (recommended) / off.
- **O4 Forced sign-out**: leave the token (recommended, content-free) OR add an unauthenticated
  `DELETE` by token (possessing the token proves ownership) so a signed-out phone stops at once.

## 6. Test plan
- **Backend:** pytest (B6). `@integration-lead` §6 (contracts + backward compatibility).
  `@security-reviewer` §8 (token storage, payload, no token in logs).
- **Device** (`@mobile-qa`, Android emulator with Google APIs 🔎, then a physical phone):
  - permission flow;
  - the DB row appears and is re-owned on an account switch;
  - a background deep search → a lock-screen notification → the tap opens the chat;
  - foreground on the same or another conversation;
  - sign-out deletes the row;
  - Logfire `push.sent channel=expo`, and the payload has no content.
- **Rollout:**
  1. apply the migration;
  2. deploy the backend with the flag off;
  3. Android dev build + EAS Update;
  4. flip `EXPO_PUSH_ENABLED` once and run the device test;
  5. keep it on.

## 7. Order of execution
1. Owner: O1–O4 + §4 tasks 2 and 3.
2. B1 → B2–B6 (with tests) and C, in parallel with P1–P7.
3. Apply the migration → deploy the backend with the flag off → Android dev build (P1) → QA (§6)
   → flip the flag.
4. iOS once the Apple account exists: APNs key → iOS dev build → the same QA on an iPhone.
