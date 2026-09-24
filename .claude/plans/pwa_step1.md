# Step 1 — Rayhan as an Installable Web App (PWA)

**Status:** PLANNED 2026-09-24 · 1A partly BUILT (settings row), nothing deployed
**Goal:** a phone user taps a home-screen icon and gets the chat as an app —
full-screen, survives a dead connection gracefully, and pings them when a long
answer is ready. No App Store, no Capacitor (that is Step 2).
**Scope of the app:** chat + conversations + settings. Library / blog / about
stay web pages (open in the browser from inside the app).

## Phases

| Phase | What | Backend? | Depends on |
|---|---|---|---|
| 1A | Install surfaces — settings row, chat nudge card, `/app` page | no | — |
| 1B | Manifest polish + standalone app shell | no | — |
| 1C | Service worker + offline screen | no | — |
| 1D | Web push «إجابتك جاهزة» | **yes** (table + sender) | 1C |
| 1E | Measurement | no | 1A–1D |

Ship order: **1B+1C together → 1A → 1D**. 1A drives installs, so the installed
experience (1B/1C) must be solid before we invite people in. 1D is the biggest
and the only one touching the backend; it ships last and can slip without
blocking the rest.

---

## 1A. Install surfaces

Full detail: `.claude/plans/install_app_nudge.md`. Summary:
- Settings row «ثبّت ريحان على جوالك» → guide dialog — **BUILT**
  (`lib/install-app.ts`, `components/Settings/InstallAppDialog.tsx`, `SidebarFooter.tsx`).
- Chat nudge card after ≥2 completed answers/session, phone + not installed,
  ✕ = 14-day snooze, max 3 impressions, never stacks with edu/onboarding/promo.
  State in localStorage (install is per-device).
- `/app` public guide page (iPhone/Android tabs, QR on desktop), linked from
  footer + mobile nav only. **No popup on library/blog/about.**

## 1B. Manifest polish + standalone shell

`frontend/app/manifest.ts` additions:
- `id: "/chat"` — stable app identity so a future `start_url` change doesn't
  create a second install.
- `shortcuts`: «محادثة جديدة» → `/chat?new=1`, «محادثاتي» → `/chats`
  (Android long-press menu; iOS ignores harmlessly).
- `screenshots` (2× narrow, 1× wide) — Android Chrome shows the rich install
  sheet only when present. Real app screenshots, anonymised demo conversation.
- Keep `start_url: /chat`, `display: standalone`, orientation unlocked.

`app/layout.tsx` viewport: `themeColor` as a light/dark media pair so the
status bar follows the theme (manifest `theme_color` stays light — the splash
paints before CSS). `appleWebApp: { capable, title: "ريحان", statusBarStyle: "default" }`.

**Standalone shell** — `isStandalone()` from `lib/install-app.ts`, exposed as a
tiny hook `useIsStandalone()`:
- Hide marketing-only chrome inside the app (e.g. links back to the landing
  page). Library/about links remain but open with `window.open(_, "_blank")` →
  iOS in-app Safari sheet / Android custom tab. Already the pattern in
  `SidebarFooter`.
- No back button exists in iOS standalone — audit every full-screen surface
  (workspace overlay, sheets, `/chats`) for an in-UI way back. The overlay
  already pushes history (mobile-compat Phase 3); verify swipe-back works.
- External links (payment `/pricing` → Moyasar) — verify the checkout, 3-D
  Secure redirect and Apple Pay all work in standalone on iOS, or force
  `/pricing` to open in the browser from inside the app.

### ⚠ Login inside the installed app (test first — highest risk)

iOS gives a home-screen app its **own storage jar**, separate from Safari. A
user logged in on Safari opens the new icon and meets the login screen. That is
expected and fine — but:
- **Google sign-in** (`GoogleQuickSignup.tsx`): the OAuth redirect from a
  standalone iOS app can bounce out to Safari and complete the login *there*,
  leaving the app logged out. Must be tested on a real iPhone. Fallbacks, in
  order: use redirect (not popup) flow with `redirectTo` back into scope; if
  still broken, hide Google in standalone iOS and show email/password + magic
  link only.
- Refresh token persistence: confirm the Supabase session survives the app
  being killed and relaunched a day later (session-timebox work, cross-tab
  fixes — re-verify in standalone).

## 1C. Service worker + offline screen

Hand-written, minimal, **no caching framework** — `next-pwa`/Workbox caching of
Next pages fights ISR, auth and the Cloudflare purge workflow.

`frontend/public/sw.js`:
- `install`: precache only `/offline` (+ its CSS/font/logo) under a versioned
  cache name `rayhan-shell-v{N}`. `skipWaiting()`.
- `activate`: delete old `rayhan-shell-*` caches, `clients.claim()`.
- `fetch`: handle **only** `request.mode === "navigate"` → network first, on
  failure serve cached `/offline`. Everything else — `/api/*`, SSE streams,
  `_next/static`, cross-origin (Supabase, backend, Moyasar) — is **not
  intercepted** (no `respondWith`). This keeps SSE, auth and payments
  byte-for-byte unchanged.
- `push` / `notificationclick` handlers — added in 1D.

`frontend/app/offline/page.tsx` — static, no data: logo, «لا يوجد اتصال
بالإنترنت», «إعادة المحاولة» button (`location.reload()`). Theme-aware.
`noindex`.

Registration: `components/pwa/ServiceWorkerRegistrar.tsx` in the root layout,
registers `/sw.js` after `load`, production only (dev HMR + SW = pain).

Headers / edge:
- `/sw.js` must be served `Cache-Control: no-cache` from Next (add to
  `next.config.mjs` headers) **and** bypass Cloudflare cache (cache rule) —
  otherwise a bad SW is pinned on users' phones for the edge TTL.
- CSP: `worker-src` falls back to `script-src 'self'` — no change needed. Push
  service endpoints are contacted by the browser, not the page — `connect-src`
  unchanged.

Kill switch: keep a `sw-kill.js` pattern ready — if a SW ships broken, deploy a
`sw.js` that unregisters itself and clears caches. Document in the file header.

## 1D. Web push — «إجابتك جاهزة»

**The use case:** deep searches take minutes. The user locks the phone; the
pipeline already continues in the background after SSE detach
(`message_service.py` ~1240–1290); when it finishes, a notification brings
them back. This is the single strongest reason for the app to exist.

iOS: works only for the **installed** app, iOS 16.4+, and permission must be
requested from a user tap. Android: works in the browser too.

### Asking for permission (never on load)

- **Primary:** a «نبّهني عند الجاهزية 🔔» chip on the deep-search progress bar
  (`project_deep_search_progress_bar`) — the user is literally waiting, the
  moment of value. Tap → `Notification.requestPermission()` → subscribe.
- **Secondary:** a toggle «الإشعارات» in إعدادات المحادثة.
- On iOS in-browser (not installed) the chip instead says «ثبّت ريحان لتصلك
  الإشعارات» → opens the install guide (ties 1A and 1D together).
- Denied → hide the chip permanently for this device; the settings toggle shows
  how to re-enable from system settings.

### Database — migration `164_push_subscriptions.sql`

```sql
create table public.push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(user_id) on delete cascade,
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  user_agent text,
  created_at timestamptz not null default now(),
  last_success_at timestamptz,
  failure_count int not null default 0
);
alter table public.push_subscriptions enable row level security;
-- users manage only their own rows; backend uses service role for sending
```
Account deletion (`delete-account` / `nuke-account`) must cover this table —
cascade handles the row; add it to the nuke-account checklist.

### Backend

- Dep: `pywebpush` (pin it; see dependency-reproducibility note).
- Env: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT=mailto:<support>`
  on luna-backend. Public key also exposed to the frontend
  (`NEXT_PUBLIC_VAPID_PUBLIC_KEY`, build arg — remember the API_URL build-arg trap).
- Routes (`backend/app/api/push.py`): `POST /push/subscribe`,
  `DELETE /push/subscribe` (by endpoint). Arabic errors, auth required.
- Sender `backend/app/services/push_service.py`: `notify_turn_ready(user_id,
  conversation_id)` — fire-and-forget task, never blocks or fails the turn.
  On 404/410 from the push service → delete the subscription; other errors →
  `failure_count++`, delete after 5.
- **Trigger:** right after the `done` event is queued in `message_service.py`
  (~line 1151), only if the turn was long (**> 20 s** wall time) **or** the
  client had detached (`_disconnect_detected`). Short answers the user watched
  arrive never notify.
- Also consider: a paused turn waiting for the user (planner asks a question) —
  «ريحان يحتاج إجابتك» — same sender, second template. Phase 1D.2, optional.

### Payload — privacy first (PDPL, وضع السرية)

Legal content never goes to Apple/Google push servers:
- title «ريحان», body «إجابتك جاهزة» — **no question text, no answer text**.
- Conversation title only if the user has NOT enabled وضع السرية, and even then
  truncated to ~40 chars. Default: no title.
- `data: { url: "/chat/<conversation_id>" }`.

### Service worker additions

- `push`: if a visible client is already on `/chat/<id>` → skip the
  notification (they are looking at it). Else `showNotification` with the
  Rayhan icon, `tag: conversation_id` (collapses duplicates), `lang: "ar"`,
  `dir: "rtl"`. Optionally `navigator.setAppBadge()`.
- `notificationclick`: focus an existing client and navigate it to `data.url`,
  or `clients.openWindow(data.url)`. Clear badge.

## 1E. Measurement

`lib/analytics/client.ts` `track()` events (analytics_events, migration 139):
- 1A: `install_nudge_shown|how|dismissed`, `install_dialog_opened{source}`,
  `install_prompt_result{outcome}`, `app_installed`.
- `standalone_session` once per session when `isStandalone()` — the only way to
  count iOS installs (Safari fires no install event). **Primary KPI.**
- 1C: `offline_screen_shown`.
- 1D: `push_permission{result}`, `push_subscribed`, backend logs
  `push.sent|failed|pruned` to Logfire; `push_opened` from `notificationclick`
  (via the landing URL `?src=push`).

Success after 30 days: share of mobile sessions that are standalone; push
opt-in rate among installed users; return rate of users who received ≥1 push.

## Verification

1. `npx tsc --noEmit`, `npm run lint`, backend pytest for push routes/sender
   (mock `pywebpush`).
2. Lighthouse PWA/installability check on prod `/chat`.
3. Playwright (iPhone UA, 390×844, `hasTouch`): settings row, nudge card rules,
   `/app` page; `context.setOffline(true)` → navigation shows `/offline`; SSE
   stream still works with SW active (send a message, `done` arrives).
4. **Real devices, required before calling it done:**
   - iPhone iOS 26 Safari: add to home screen → launches standalone → login
     (email + **Google**) → send deep search → lock phone → notification → tap →
     lands on the conversation. Airplane mode → offline screen. Kill + relaunch
     next day → still logged in.
   - Android Chrome: install prompt from our button → same flow.
5. SW rollback drill: deploy the self-unregistering `sw.js` on a preview and
   confirm it clears.

## Out of scope → Step 2 (Capacitor)

Share-sheet target for PDFs on iOS, Sign in with Apple, App Store listing, IAP
vs Moyasar question, native Face ID, haptics.

## File manifest

| File | Phase | Change |
|---|---|---|
| `frontend/lib/install-app.ts` | 1A | BUILT (+ `useIsStandalone`) |
| `frontend/components/Settings/InstallAppDialog.tsx` | 1A | BUILT → use `InstallSteps` |
| `frontend/components/sidebar/SidebarFooter.tsx` | 1A | BUILT |
| `frontend/components/install/InstallSteps.tsx`, `InstallNudgeCard.tsx` | 1A | NEW |
| `frontend/stores/install-nudge-store.ts` | 1A | NEW |
| `frontend/stores/edu-store.ts`, `hooks/use-chat.ts`, `components/chat/ChatLayoutClient.tsx` | 1A | gate + `noteTurn()` + mount |
| `frontend/app/app/page.tsx`, `SiteFooter.tsx`, `lib/nav/site-nav.ts`, sitemap | 1A | NEW / link |
| `frontend/app/manifest.ts`, `app/layout.tsx` | 1B | id, shortcuts, screenshots, themeColor, appleWebApp |
| `frontend/public/manifest-screenshots/*` | 1B | NEW |
| `frontend/public/sw.js` | 1C/1D | NEW |
| `frontend/app/offline/page.tsx` | 1C | NEW |
| `frontend/components/pwa/ServiceWorkerRegistrar.tsx` | 1C | NEW |
| `frontend/next.config.mjs` | 1C | `/sw.js` no-cache header |
| Cloudflare cache rule | 1C | bypass `/sw.js` |
| `frontend/lib/push.ts`, progress-bar chip, settings toggle | 1D | NEW |
| `shared/db/migrations/164_push_subscriptions.sql` | 1D | NEW (RLS) |
| `backend/app/api/push.py`, `services/push_service.py` | 1D | NEW |
| `backend/app/services/message_service.py` | 1D | trigger after `done` |
| backend requirements + VAPID env (backend + frontend build arg) | 1D | NEW |
