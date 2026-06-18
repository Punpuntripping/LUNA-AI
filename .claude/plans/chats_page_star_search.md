# Plan — صفحة المحادثات (/chats) + تمييز بنجمة (Star) + بحث داخل الرسائل

**Goal:** Make the conversation list scale like Claude.ai. The sidebar shows only a short recent list (starred pinned on top) + an "عرض الكل" link to a new **`/chats`** page. The `/chats` page is a full, paginated, **searchable** index — and search reaches **inside message content**, not just titles. Each conversation's 3-dots menu gains a **Star/Unstar** action (Rename + Delete already exist). Reference UX: Claude's Chats page (search box, "Filter by" dropdown, "New chat", infinite list).

## Decisions (locked with user)

- **"Add to project" → SKIPPED for v1.** Luna has no projects; the 3-dots gains **Star** only (plus existing Rename/Delete). No `case_id` move action this round.
- **Filters = All + Starred** (no "by Case" filter in v1). Starred conversations also **pin to the top** of the sidebar list.
- **Search scope = titles + message content**, server-side, scoped to the user.
- **Sidebar cap = 15 recent** (after starred), then "عرض الكل" → `/chats`.
- **Part 3 (30-message lazy scroll inside a chat) is already shipped** (`use-messages.ts` `MESSAGE_PAGE_SIZE = 30` + cursor `before` + `IntersectionObserver` top-sentinel in `MessageList.tsx`). **Not touched** by this plan.

## Data model — migration `shared/db/migrations/071_conversation_star_search.sql`

`conversations` gets one new column:
| col | type | notes |
|---|---|---|
| `starred_at` | timestamptz NULL | null = not starred. Timestamp (not bool) so starred sort by most-recently-starred. |

Indexes (search is `ILIKE '%q%'` substring — `pg_trgm` already installed, confirmed):
- `CREATE INDEX ... ON messages USING gin (content gin_trgm_ops);`
- `CREATE INDEX ... ON conversations USING gin (title_ar gin_trgm_ops);`
- Partial index for starred ordering: `CREATE INDEX ... ON conversations (user_id, starred_at DESC) WHERE starred_at IS NOT NULL AND deleted_at IS NULL;`

**Apply to prod via Supabase MCP** (migration-drift memory: migration files are NOT auto-applied) + verify with `list_tables`. Corpus is tiny today (911 messages / 322 convos) — indexes are forward-safety, not a current need.

**RLS:** no policy change. `starred_at` is just another column on the already-RLS'd `conversations`; all reads/writes stay user-scoped through the service layer (`get_user_id` + `eq("user_id", …)`).

## Backend

**`backend/app/models/requests.py` — `UpdateConversationRequest`**
- Make `title_ar` **optional**; add optional `starred: bool`. (Today rename forces title_ar; star toggle reuses the same PATCH.) Validate "at least one field present".

**`backend/app/models/responses.py` — `ConversationSummary` + `ConversationDetail`**
- Add `is_starred: bool` (derived from `starred_at is not None`) and optionally `starred_at`.
- Add **search-result-only** optional fields: `snippet: str | None`, `match_type: Literal['title','message'] | None`.

**`backend/app/services/conversation_service.py`**
- `update_conversation(...)`: accept optional `title_ar` + `starred`. When `starred` given, set `starred_at = now()` (true) or `null` (false). Keep ownership check.
- `list_conversations(...)`: add `q: str | None` and `starred: bool` params.
  - `starred=True` → filter `starred_at is not null`.
  - Ordering: `starred_at DESC NULLS LAST, updated_at DESC` so starred float to the top (used by sidebar too).
  - `q` set → search path (below). `q` empty → current list behavior + star ordering.
- New `search_conversations(supabase, auth_id, q, *, starred, limit, offset) -> dict`:
  - Title hits: `conversations` where `title_ar ILIKE %q%` (user-scoped, not deleted).
  - Message hits: `messages` where `content ILIKE %q%` joined to user's conversations → group by `conversation_id`, take newest matching message as the **snippet** (trim to ~160 chars around the match).
  - Merge → one row per conversation with `match_type` (title preferred) + `snippet`. Order starred-first then `updated_at`. Paginate. Return `{conversations, total, has_more}`.
  - All queries user-scoped (`get_user_id`); RLS-safe.

**`backend/app/api/conversations.py`**
- `GET /conversations`: add `q: Optional[str]` and `starred: bool = False` query params; route to `search_conversations` when `q` present, else `list_conversations`. Map `is_starred`/`snippet`/`match_type` in `_to_conversation_summary`.
- `PATCH /conversations/{id}`: pass through optional `title_ar` + `starred`.

## Frontend

**`frontend/types/index.ts`**
- `ConversationSummary`: add `is_starred: boolean`, `starred_at?: string | null`, `snippet?: string | null`, `match_type?: 'title' | 'message' | null`.

**`frontend/lib/api.ts` — `conversationsApi`**
- `list(...)`: add `q?: string` and `starred?: boolean` params (append to query string).
- `update(...)`: widen to `update(id, body: { title_ar?: string; starred?: boolean })` (callers: rename passes `{title_ar}`, star passes `{starred}`).
- (Optional convenience) `star(id, starred)` wrapper.

**`frontend/hooks/use-conversations.ts`**
- `useConversations` stays for sidebar (keep `limit`, gets star ordering for free from backend).
- Add `useSearchConversations(q, filter)` (TanStack Query keyed on `[..., 'search', q, filter]`, `enabled: q.length > 0 || filter === 'starred'`).
- Add `useStarConversation()` mutation (optimistic: flip `is_starred`/`starred_at`, re-sort, rollback on error; invalidate lists on settle). Mirror `useRenameConversation` structure.

**`frontend/components/sidebar/ConversationList.tsx`**
- Render **starred-first then top 15** (backend already orders; just `slice(0, 15)` for the cap — starred beyond 15 still show because they sort first).
- Add an "عرض الكل" row/button at the bottom → `router.push('/chats')` (only when total > shown).

**`frontend/components/sidebar/ConversationItem.tsx`**
- Add **Star/Unstar** `DropdownMenuItem` above Rename (`Star`/`StarOff` lucide icons), wired to `useStarConversation`.
- Show a small filled star indicator on the row when `conversation.is_starred`.

**New route — `/chats`** (reuses the sidebar shell)
- `frontend/app/chats/layout.tsx` → wrap children in the existing `ChatLayoutClient` (same sidebar; no workspace pane needed — it only mounts the pane on `/chat/[id]`, so `/chats` renders sidebar + page cleanly).
- `frontend/app/chats/page.tsx` → `<ChatsPage />`.
- `frontend/components/chats/ChatsPage.tsx` (new, client): Claude-style —
  - Header: "المحادثات" title + "All / Starred" filter (shadcn `dropdown-menu` or `tabs`) + "محادثة جديدة" button (same lazy-create flow as sidebar `handleNewConversation` → `/chat`).
  - **Search box** = revive `ConversationSearch` (debounced ~250ms) bound to `useSearchConversations`.
  - List: full conversations, infinite scroll / "load more" with `limit`/`offset` (30/page); each row reuses `ConversationItem` passing `searchQuery` (so `HighlightedText` highlights title hits) + renders `snippet` under the title for message-content hits.
  - Empty/loading/error states in Arabic, RTL.

**Reuse (already built, currently orphaned):** `ConversationSearch.tsx`, `HighlightedText.tsx`, and the `searchQuery` prop on `ConversationItem`.

## Out of scope (v1)
- "Add to Case/project/folder" menu action and any "by Case" filter.
- "Select chats" / bulk actions (Image #2 shows it; defer).
- FTS/semantic search (trigram `ILIKE` substring is enough now; `arabic` FTS config + `conversations.embedding` exist for a later upgrade).
- Changing the in-chat 30-message scroll (already works).

## Success criteria
- Sidebar shows starred-on-top + ≤15 recent + an "عرض الكل" link.
- `/chats` lists all conversations, paginated by infinite scroll, with a working "All / Starred" filter.
- Typing in `/chats` search returns conversations matching **title OR message content**, with the matched snippet shown and the term highlighted; user-scoped (no cross-user leakage).
- Star/Unstar from the 3-dots flips the star, pins/unpins from the top, and persists across reload.
- Migration `071` applied to prod and verified.
