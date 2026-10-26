# «رسائل ريحان» — in-app announcements (web + app)

Owner decisions 2026-10-26 (reflect session):

| # | Decision |
|---|---|
| D1 | A message targets **everyone** (`all`) or **paying subscribers** (`paid`). Paid = unexpired subscription on a purchasable plan (`plans.price_sar IS NOT NULL` → basic/pro/max). Promo (ترويجي), dev, free = not paid. Evaluated at read time. |
| D2 | **Every unread message pops up** (one at a time, oldest unread first). Closing it any way, or pressing its link, marks it read. |
| D3 | A **bell** sits right beside the settings gear, in theme colours (primary — never yellow), with an unread count badge while > 0 — for messages that slipped by. Bell → messages page. |
| D4 | A **separate messages page** (`/messages` on web, `messages` screen in the app) lists all visible messages, newest first; unread ones are marked read once shown. |
| D5 | Same on **mobile** (Expo) — web parity, nothing extra. |
| D6 | Messages may carry **one link button** (`link_label` + `link_url`). |
| D7 | Authoring = SQL `INSERT` into `public.announcements` (no admin UI). Future `published_at` schedules. |

First message: «طوّرنا ريحان: أسرع وأدق» (`paid`, link «سياسة حد الاستخدام» → `/learn/usage-limits`) — seeded by migration 189.

## Contract

Types: `frontend/lib/contracts/announcements.ts` (web `@/lib/contracts`, app `@contracts`).

- `GET  /api/v1/announcements` (authed) → `{items: Announcement[], unread_count}` newest first, ≤ 50.
- `POST /api/v1/announcements/read` (authed) body `{ids: string[]}` (1–50) → 204. Idempotent; ids the caller cannot see are ignored.

## Pieces

| Layer | Files |
|---|---|
| DB | `shared/db/migrations/189_announcements.sql` — `announcements`, `announcement_reads` (RLS on, no client policies, revoked from anon/authenticated), `get_user_announcements(p_user_id)` RPC (service_role only) = THE visibility rule. APPLIED 2026-10-26. |
| Backend | `backend/app/services/announcement_service.py`, `backend/app/api/announcements.py`, mounted in `main.py`; tests `backend/tests/test_announcements.py`. |
| Web | `lib/api.ts` `announcementsApi`, `hooks/use-announcements.ts`, `components/announcements/{AnnouncementsBell,AnnouncementPopup,MessagesPage,AnnouncementBody}.tsx`, `app/messages/{layout,page}.tsx`, bell in `SidebarFooter.tsx`, popup in `ChatLayoutClient.tsx`, `/messages` in robots disallow. |
| App | bell beside the home-header settings button, popup host in the `(app)` layout, `app/(app)/messages.tsx`. |

## Popup rules (both clients)

- Only for a signed-in user, after the list has loaded, never on the messages page itself.
- Never on top of another dialog/sheet (web: wait while any `[role="dialog"]` is open; app: wait while another modal is up).
- Closing → `POST /read` for that id (optimistic: cache flips `read`, count drops) → next unread pops.
- Link press → mark read, close, navigate (in-site path → router; https → external browser).
