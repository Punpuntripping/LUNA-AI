# «ثبّت ريحان» — Install-to-Home-Screen Surfaces

**Status:** PLANNED 2026-09-24 · Part A (settings row) BUILT, not deployed
**Scope:** frontend only. No backend, no migration, no service worker.

## Why

Rayhan is already installable (`app/manifest.ts`, `display: standalone`,
`start_url: /chat`), but nobody knows. iOS has no install API — Apple allows a
site to *guide* the user to Share → «إضافة إلى الشاشة الرئيسية», never to
trigger it. Android Chrome fires `beforeinstallprompt`, which we can replay from
our own button. iOS 26 adds an «فتح كتطبيق ويب» toggle (default ON) to the add
dialog — the guide tells users to leave it on.

## Placement decisions

| Surface | Format | Audience | Status |
|---|---|---|---|
| A. Settings popover | Permanent row → guide dialog | Signed-in, phone or Chrome-installable, not installed | BUILT |
| B. Chat | Dismissible inline card at a value moment | Signed-in, phone, not installed | TODO |
| C. `/app` page + footer / mobile-nav link | Static guide page | Anyone (shareable on WhatsApp / email) | TODO |
| Library / blog / about | **No popup** | — | Rejected |

**Why no popup on public pages:** visitors there are anonymous Google readers
after one article; the installed app opens `/chat` → login wall; and that slot
belongs to the anon-conversion popup. Two popups compete and both lose.

## A. Settings row — BUILT

- `frontend/lib/install-app.ts` — `detectInstallPlatform()` (iPadOS desktop-UA
  handled via `maxTouchPoints`), `isStandalone()`, `useDeferredInstallPrompt()`.
  `beforeinstallprompt` is captured at **module load** — it fires once, early;
  a listener attached when a dialog mounts would miss it.
- `frontend/components/Settings/InstallAppDialog.tsx` — iOS steps / Android
  prompt button or menu steps / desktop fallback text. `presentation="mobileSheet"`.
- `frontend/components/sidebar/SidebarFooter.tsx` — «ثبّت ريحان على جوالك» row
  above the bottom separator; hidden when `isStandalone()` or on desktop without
  a Chrome prompt.

## Shared refactor (do first)

Extract the step lists + `StepList` out of `InstallAppDialog` into
`components/install/InstallSteps.tsx` (`<InstallSteps platform canPrompt onInstall />`).
Used by the dialog, the chat card's «كيف؟», and `/app`. One copy of the Arabic
instructions — the iOS wording will change again with the next Safari redesign.

**Illustrations:** add 2 small original SVG mock-ups (Safari bottom bar with the
Share glyph highlighted; the share sheet row «إضافة إلى الشاشة الرئيسية»). Draw
them ourselves — do not ship Apple screenshots. Theme-aware via `currentColor`.

## B. Chat nudge card

### Store — `frontend/stores/install-nudge-store.ts`

State in **localStorage, not `user_preferences`**: installation is per-device.
A user who installed on their iPhone must still be offered it on their Android,
and a server flag would suppress that.

```
key: rayhan.install_nudge  →  { impressions: number, snoozedUntil: number | null, done: boolean }
```

`noteTurn()` — called from `use-chat.ts` on `done`, next to
`useEduStore.getState().bumpTurn()` (line ~682). Keeps a session-local
completed-turn counter and runs the gate chain:

1. `detectInstallPlatform() !== "other"` (phone/tablet only)
2. `!isStandalone()`
3. `!done` and `impressions < 3` (3 ignored shows ⇒ stop forever on this device)
4. `snoozedUntil` null or past (✕ ⇒ snooze **14 days**)
5. ≥ **2 completed answers this session** (value moment — never on first load)
6. not already shown this session
7. **no competing surface:** edu `activeLesson === null` AND edu
   `shownThisSession.length === 0` (one teaching surface per session);
   onboarding / tour / promo popup closed; `aModalIsOpen()` false
8. Reuse the edu 24h rule loosely: skip if an edu lesson was shown < 24h ago
   (`useEduStore.lastShownAt`)

And the reverse: `edu-store.maybeDeliver` gains gate 5b —
`if (useInstallNudgeStore.getState().isOpen) return;` — so the two never stack.

Exits:
- «كيف؟» → opens `InstallAppDialog` (Android with prompt: call `install()` directly)
  → `done = true` on `appinstalled`, otherwise snooze 14 days.
- ✕ → snooze 14 days.
- `appinstalled` event anywhere → `done = true`.

### UI — `frontend/components/install/InstallNudgeCard.tsx`

- Mounted in `ChatLayoutClient` beside `<EduLessonHost />`, self-gating (`null`
  almost always).
- Same slot and visual family as `EduLessonCard` (above the composer, z-40, under
  every dialog). Copy: **«ثبّت ريحان على شاشتك الرئيسية»** · sub «افتحه كتطبيق
  بضغطة، بدون شريط المتصفح» · buttons «كيف؟» / ✕.
- Respect safe-area insets; ≥ 44px touch targets (mobile-compat rules).
- Never render while `chat-store` is streaming (fires on `done`, but a second
  send can start before the user reads it — hide on stream start, keep the
  impression).

## C. `/app` page

- `frontend/app/app/page.tsx` — composes `SitePageShell` (every non-sidebar shell
  does). Static, no auth.
- Content: headline «ريحان على جوالك», 3 benefit bullets (أيقونة على الشاشة ·
  بدون شريط المتصفح · يفتح مباشرة على المحادثة), then `<InstallSteps>` for the
  detected platform, with tabs «آيفون / أندرويد» so a desktop visitor (or someone
  helping a colleague) can read either. Desktop also shows a QR code to
  `rayhanai.com/app` (generate at build time as inline SVG — no runtime fetch).
- Already-installed visitor (`isStandalone`) → «ريحان مثبّت على جهازك ✓» + link to `/chat`.
- Metadata: title/description in Arabic; OG via `ogImageUrl()` + bump
  `OG_VERSION` — never hand-build `/og?title=`. Add to `sitemap`.
- Links: `SiteFooter.tsx` platform group («تطبيق ريحان» → `/app`) and
  `SiteMobileNav` (via `lib/nav/site-nav.ts`). **Not** in the desktop header.
- Check CSP / edge rules: `/app` must not collide with any Cloudflare or
  middleware path rule (grep `middleware.ts` + CF rules for `/app` prefixes).

## Analytics

Via `lib/analytics/client.ts` `track()`:
`install_nudge_shown` · `install_nudge_how` · `install_nudge_dismissed` ·
`install_dialog_opened {source: settings|nudge|app_page}` ·
`install_prompt_result {outcome}` (Android) · `app_installed` (`appinstalled`) ·
`standalone_session` (once per session when `isStandalone()` — the only way to
measure iOS installs, which fire no event).

## Out of scope (later steps)

- Service worker, offline screen, web push («بحثك جاهز») — Step 1b.
- Capacitor store app, share-target for PDFs, Sign in with Apple, IAP question — Step 2.

## Verification

1. `npx tsc --noEmit`, `npm run lint`.
2. Playwright with `browser.newContext({ userAgent: <iPhone Safari>, viewport 390×844, hasTouch })`:
   settings row visible → dialog shows iOS steps; after 2 mocked `done` events the
   card appears; ✕ → gone and localStorage `snoozedUntil` set; reload → not shown.
3. Android UA: dispatch a synthetic `beforeinstallprompt` → «تثبيت ريحان» button.
4. `matchMedia('(display-mode: standalone)')` stubbed true → row, card and `/app`
   CTA all hidden.
5. Edu collision: force an edu lesson active → card does not show, and vice versa.
6. Real devices: one iPhone (iOS 26 Safari) + one Android Chrome before calling it done.

## File manifest

| File | Change |
|---|---|
| `lib/install-app.ts` | BUILT |
| `components/Settings/InstallAppDialog.tsx` | BUILT → refactor to use `InstallSteps` |
| `components/sidebar/SidebarFooter.tsx` | BUILT |
| `components/install/InstallSteps.tsx` | NEW (+ 2 inline SVG illustrations) |
| `components/install/InstallNudgeCard.tsx` | NEW |
| `stores/install-nudge-store.ts` | NEW |
| `stores/edu-store.ts` | gate 5b |
| `hooks/use-chat.ts` | `noteTurn()` on `done` |
| `components/chat/ChatLayoutClient.tsx` | mount card |
| `app/app/page.tsx` | NEW |
| `components/site/SiteFooter.tsx`, `lib/nav/site-nav.ts` | «تطبيق ريحان» link |
| sitemap | add `/app` |
