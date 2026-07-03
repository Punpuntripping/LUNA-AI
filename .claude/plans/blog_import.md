# Blog Import — «اتحدث مع المدونة» + استيراد المدونات

**Status:** BUILT 2026-07-02 (same day) — migration 088 applied to prod; tsc/lint/build green; 18 new backend tests + prod smoke pass. NOT deployed (code local/uncommitted).
**Build deltas vs plan:** (1) `message_attachments.document_id` FK was live-enforced against `case_documents` and the table was EMPTY (every insert silently failing since 009) → migration 088 re-points the FK to `workspace_items` and `list_messages` now embeds `workspace_items(title, kind, metadata)`; (2) `_insert_attachment_links` now filters ids to sender-owned items (closes the IDOR the FK fix would have widened); (3) `ImportBlogRequest` lives in blog.py (blog-only bodies precedent); (4) chip removal deletes the item only when `createdByChip` (server dedup reuse is never deleted).
**Revision 2026-07-03 (user decision):** the conversation copy is **`kind=agent_search`** (تحليل قانوني), NOT `note` — same treatment as a native search output incl. a REAL المراجع panel. Route renamed `POST /conversations/{id}/blog-items` (`blog_service.create_blog_item`, `BlogItemResponse`, `api.createBlogItem`). References are **materialized** into `workspace_item_references`: primary path copies the original shared WI's `used=true` rows (via `post.source_item_id`, root-resolved for copies — `item_id` PKs carried verbatim so compliance refs survive the irreversible hash); fallback rebuilds rows from the frozen `references_json` (reg+case recover via `ref_id`, compliance degrades to stub cards). `metadata.references_json` no longer stored; `metadata.subtype` = original post subtype (fallback `legal_synthesis`). WI dedup is now kind-agnostic (metadata `source_post_id` only). Also fixed: `_estimate_ocr_pages` no longer 1-page-floors resolved non-attachment kinds (blog chips ride `attachment_ids`). Validated: 20 backend tests + full prod smoke (WI shape, 10 refs copied, `fetch_item_references` round-trip, dedup, cleanup).
**Date:** 2026-07-02
**Depends on:** blog share-by-link (migration 070, deployed), display_mode (084), is_public (085)

Blogs are currently a one-way exit: artifact → snapshot → public page. This feature closes the loop —
a blog post (public, own, or received via shared link) can be pulled **back into the product** two ways:
into a **conversation** as a `kind=note` workspace item, and into the user's own **مدوناتي** library
as a snapshot copy.

---

## Locked decisions (from /reflect Q&A with user)

1. **Three mechanisms, all in scope:**
   - «اتحدث مع المدونة» button on blog reading surfaces (public post page, unlisted shared-link page, own مدوناتي post page).
   - «+» on مدوناتي: paste any `…/blog/{token}` URL → saved into the caller's مدوناتي.
   - **Composer paste**: pasting a blog URL into the chat input behaves *like a file attachment* — becomes a chip, imported as a WI bound to the sent message.
2. **Destination:** button offers BOTH a new conversation and picking an existing one (small picker dialog).
3. **The copy in a conversation is `kind=note`** — plain `content_md`, no clickable citations in v1. Store the blog snippet as the WI `summary` at insert time (router context needs no analyzer pass). Stash `references_json` in WI metadata so nothing is lost.
4. **Access rule = viewing rule:** anyone holding a valid token of a *published* post may import it. Unpublished/revoked → 404.
5. **Agents:** imported note is treated exactly like any other WI. No force_attach, no special router behavior.
6. **Dedup everywhere:** same blog → same conversation = reuse existing item; same token → مدوناتي twice = one entry.
7. **مدوناتي import = snapshot copy** — a NEW `blog_posts` row owned by the importer, frozen content.
   Consequence (user signed off): revocation is no longer a total kill switch — saved copies survive.
8. **Imported copies ARE re-publishable/re-shareable — under their own new token** (DB-minted on insert). No restriction vs own posts.
9. **Anonymous visitor clicking the button:** redirect to login/signup, then **resume the flow** with the blog pre-attached (growth loop).

---

## Design

### D1 — Provenance & dedup: `source_post_id` with ROOT propagation

New nullable column `blog_posts.source_post_id` = the **root original** post_id, not the immediate source:
when copying a post that itself has `source_post_id` set, **propagate that value**. Chains collapse
(A→B→C all point at A's post), so dedup by `(owner_user_id, source_post_id)` catches "imported the same
content via two different copies' tokens".

- مدوناتي dedup: partial unique index on `(owner_user_id, source_post_id)` `WHERE deleted_at IS NULL AND source_post_id IS NOT NULL`. Deleting your copy allows re-import.
- Self-import (pasting a token that resolves to your own post, or a copy whose root you already own/imported): return the existing row with `already_saved: true`, insert nothing.
- Conversation dedup: WI `metadata->>'source_post_id'` stores the same root id; before insert, look for a live `kind=note` row in the conversation with that value → return it with `already_attached: true`.
- `is_imported` (derived: `source_post_id IS NOT NULL`) is added to the مدوناتي list payload for the badge.

### D2 — Workspace item shape

| Field | Value |
|---|---|
| kind / created_by | `note` / `user` |
| title | post.title → fallback truncated question_text → fallback «مدونة مستوردة» |
| content_md | snapshot `content_md` verbatim |
| summary | `make_snippet(content_md, 400)` — set at insert (extend `create_workspace_item` with optional `summary` param) |
| metadata | `{ "subtype": "blog", "source_post_id": <root uuid>, "source_token": <pasted token>, "references_json": [...] }` |

**Cap decision:** notes count toward the 15-per-conversation cap (DB trigger `enforce_artifact_cap`, migration 031)
and toward the app-layer dispatch pre-flight. Blog notes **stay counted** — they are real content, and the user
chose standard-WI treatment. Catch SQLSTATE 23514 / `workspace_items_cap_exceeded` → Arabic 400
«وصلت المحادثة إلى الحد الأقصى للعناصر». (Alternative — exempting `subtype='blog'` in the trigger — REJECTED for v1.)

Agent side needs **zero changes**: router loads all-kind WI summaries (`agents/router/context.py:146-190`),
`unfold_workspace_item` reads `content_md` (empty citation manifest for notes is fine), OCR runner only touches
`kind='attachment'`, and `_estimate_ocr_pages` finds no `page_count` on a note → contributes 0.

### D3 — Backend endpoints (both in `backend/app/api/blog.py`, mounted under `/api/v1`)

**`POST /blogs/import`** (auth) — save into مدوناتي.
Body `{ token: str }` — backend tolerantly extracts a 32-hex token from a full URL or bare token.
Resolve token → published, non-deleted post (new `blog_service.resolve_post_by_token` returning the FULL row
incl. `post_id`, `owner_user_id`, `source_post_id`) → self/dedup checks (D1) → snapshot-copy insert
(new token DB-minted, `is_published=true`, `is_public=false`, `source_post_id`=root).
Response `{ post: MyBlogItem, already_saved: bool }`. 404 Arabic «المدونة غير موجودة أو تم إلغاء نشرها» on bad token.
Rate limit: default 60/min per-user bucket — no changes needed.

**`POST /conversations/{conversation_id}/blog-notes`** (auth) — create the note WI.
Body `{ token: str }`. Verify conversation ownership (same helper the notes endpoint uses) → resolve token →
conversation dedup (D1) → `create_workspace_item` per D2 → response `{ item: WorkspaceItem, already_attached: bool }`.
Precedent: `POST /conversations/{id}/workspace/notes` (`backend/app/api/workspace.py:322-346`).

No preview endpoint needed — the chip fetches the title from the existing anonymous `GET /public/blog/{token}`.

### D4 — Frontend surfaces

**«اتحدث مع المدونة» button** — new `components/blog/ChatWithBlogButton.tsx`, placed in:
- `BlogArticleView.tsx` hero action row (~:131-152, next to «نسخ المقال») — covers public + shared-link + own, title mode
- `PublicAnswerView.tsx` header area — question mode
- `/blogs/[token]` management toolbar (app/blogs/[token]/page.tsx ~:184-267)

Authed click → `BlogDestinationDialog` (new): «محادثة جديدة» (primary) + recent conversations list
(`conversationsApi.list`). New: `POST /conversations` → `POST /conversations/{id}/blog-notes` → `router.push('/chat/{id}')`.
Existing: import → navigate. No pending-slot needed on this path — the WI exists before the chat mounts.
Anon click → stash intent (D6) → `router.push('/login')`.

**مدوناتي «+»** — wire `onCreate` on the «مدوناتي» NavPill (`components/sidebar/Sidebar.tsx:260-265` — the
reserved empty slot at :132-136 already exists) + a matching affordance in the `/blogs` page header (~:34-44).
Opens `ImportBlogDialog` (new): URL/token paste field → `api.importBlog` → invalidate `myBlogsKeys` → toast
(«تمت إضافة المدونة» / «موجودة لديك مسبقًا»). Imported badge «مستوردة» (keyed on `is_imported`) in
`BlogList.tsx` badge row (~:71-88) and `/blogs` page cards (~:75-95).

**Composer paste** — mirrors the file-attachment pre-send model:
- `onPaste` handler on the textarea (`ChatInput.tsx:338-357`, none exists today). Regex the pasted text for
  `/blog/([0-9a-f]{32})` (host-agnostic — matches prod domain and localhost). On match: strip the URL from the
  inserted text, add a blog chip.
- Chip state: new `pendingBlogs: {token, title?, itemId?, status}[]` in `chat-store.ts` (alongside `pendingFiles`).
  Title fetched anonymously from `GET /public/blog/{token}`; invalid token → error chip, removable, send NOT blocked.
- Import timing = attachment timing (at add, pre-send): with a `conversationId`, call `/blog-notes` immediately and
  store `itemId` on the chip. Empty chat: stash tokens in a new `pendingBlogTokens` chat-store slot, let
  `onRequireConversation` create the conversation (exact `pendingAttachFiles` pattern, `app/chat/page.tsx:45-69`),
  and a consume-effect in `ChatInput` (like :252-258) imports them once mounted with the new id.
  Same-token pasted twice in one composer → single chip (dedup client-side; server dedup backstops).
- Send: `use-chat.ts:99-102` appends blog chip `itemId`s into the same `attachment_ids` array → rides
  `message_attachments` (best-effort insert, `message_service.py:269-278`) → renders as a chip on the user
  message via `artifactLookup`. Polish: `AttachmentChip`/`WorkspaceCard` already label `kind=note`; verify the
  chip icon/label for `subtype='blog'` reads sensibly (optional small tweak, e.g. BookText icon + «مدونة»).

### D5 — What the user answered "both" to, made concrete

Path into a conversation exists at three altitudes: button→new convo, button→existing convo (picker),
paste-in-composer→current convo (or new via create-on-attach). All three converge on `POST .../blog-notes`.

### D6 — Login-resume intent (anon growth loop)

No `returnTo` mechanism exists (`LoginForm.tsx:146/157` hardcode `router.push("/chat")`; OAuth callback
hardcodes it server-side at `app/auth/callback/route.ts:63`). Zustand slots die on the OAuth full-page redirect,
so use **sessionStorage**:

- New `lib/post-login-intent.ts`: `setPendingIntent({type:'chat_with_blog', token})` / `consumePendingIntent()`
  (JSON, single key `luna_pending_intent`, cleared on read, ~30-min expiry stamp).
- Producer: `ChatWithBlogButton` when unauthenticated, before pushing `/login`.
- Consumer: **one** place — an effect that runs once authenticated (in `AuthGuard` after the session resolves,
  or on `/chat` empty-page mount, whichever proves cleaner): reads the intent → create conversation → import
  blog-note → `router.replace('/chat/{id}')`. Covers email login, registration, AND OAuth (all funnel to `/chat`).
- Failure tolerance: expired/invalid intent → silently drop, land on `/chat` normally.

---

## Migration 088 — `088_blog_import.sql`

Highest existing migration is 087 (`pii_mappings`). Idempotent:

```sql
ALTER TABLE public.blog_posts ADD COLUMN IF NOT EXISTS source_post_id UUID;  -- root provenance, no FK (matches source_item_id convention)
COMMENT ON COLUMN public.blog_posts.source_post_id IS 'Root original blog_posts.post_id this row was imported from (propagated through chains). NULL = authored, not imported.';
CREATE UNIQUE INDEX IF NOT EXISTS uq_blog_posts_owner_source
  ON public.blog_posts (owner_user_id, source_post_id)
  WHERE deleted_at IS NULL AND source_post_id IS NOT NULL;
```

No RLS changes (SELECT policy already covers; inserts are service-role only).
**Pre-work per migration-drift memory:** verify live schema via Supabase MCP before writing — confirm 084/085
columns exist in prod, confirm `message_attachments.document_id` FK to `case_documents` is NOT enforced in the
live DB (file attachments already write WI ids through it today; if it IS enforced there, the blog-chip send
path needs the same best-effort behavior it already has — the insert is try/except).

---

## File manifest

### Backend
| File | Change |
|---|---|
| `shared/db/migrations/088_blog_import.sql` | NEW — column + partial unique index (above) |
| `backend/app/services/blog_service.py` | `resolve_post_by_token` (full row); `import_post_for_user` (self-check → dedup → root-propagated snapshot copy, returns `(row, already_saved)`); `list_my_blogs` emits `is_imported` |
| `backend/app/api/blog.py` | `POST /blogs/import`; `POST /conversations/{conversation_id}/blog-notes` |
| `backend/app/models/requests.py` | `ImportBlogRequest {token}` (shared by both endpoints) |
| `backend/app/models/responses.py` | `ImportBlogResponse`, `BlogNoteResponse`; `MyBlogItem.is_imported: bool` |
| `backend/app/services/workspace_service.py` | optional `summary=None` param on `create_workspace_item` |

### Frontend
| File | Change |
|---|---|
| `types/index.ts` | `MyBlogItem.is_imported`; import request/response types |
| `lib/api.ts` | `importBlog(token)`, `createBlogNote(conversationId, token)`, `getPublicBlogClient(token)` (anon fetch for chip titles) |
| `lib/post-login-intent.ts` | NEW — sessionStorage intent helpers |
| `components/blog/ChatWithBlogButton.tsx` | NEW — auth-aware button (dialog vs intent+login) |
| `components/blog/BlogDestinationDialog.tsx` | NEW — new-vs-existing conversation picker |
| `components/blogs/ImportBlogDialog.tsx` | NEW — paste-URL dialog for مدوناتي |
| `components/blog/BlogArticleView.tsx` / `PublicAnswerView.tsx` | mount the button |
| `app/blogs/[token]/page.tsx` | button in management toolbar |
| `app/blogs/page.tsx` | «+» in header; «مستوردة» badge on cards |
| `components/sidebar/Sidebar.tsx` | `onCreate` on the blogs NavPill |
| `components/sidebar/BlogList.tsx` | «مستوردة» badge in row |
| `stores/chat-store.ts` | `pendingBlogs` chips + `pendingBlogTokens` carry slot (+ clear in `reset()`) |
| `components/chat/ChatInput.tsx` | `onPaste` detection; blog chips render; consume-effect for carried tokens |
| `components/chat/BlogChip.tsx` | NEW — chip (title, status, remove) |
| `hooks/use-chat.ts` | merge blog `itemId`s into `attachment_ids` |
| `components/auth/AuthGuard.tsx` (or `/chat` page) | post-login intent consumer |

---

## Build order

1. **Verify live schema** (Supabase MCP): blog_posts columns, message_attachments FK reality. Then migration 088 → apply.
2. **Backend**: blog_service functions + two routes + models + `summary` param. Tests: resolve (published/revoked/bad token), self-import, dedup both axes, root propagation through a chain, cap-hit → Arabic 400, `is_imported` in list.
3. **مدوناتي import UI**: + button (sidebar & page), ImportBlogDialog, badges, api/types.
4. **«اتحدث مع المدونة» (authed)**: button + destination dialog on all three surfaces.
5. **Composer paste path**: store slots, chips, paste handler, create-on-attach carry, send wiring.
6. **Login-resume intent** (anon path, email + register + OAuth).
7. **Validation**: `npx tsc --noEmit`, `npm run build`, backend boot, live smoke (paste flows, dedup, cap, anon resume). NOT deployed until validated.

## Edge cases

- Invalid/revoked/unpublished token → 404 «المدونة غير موجودة أو تم إلغاء نشرها»; composer chip shows error, message still sendable.
- Conversation at 15-item cap → Arabic 400 surfaced in dialog/chip.
- Paste text containing multiple blog URLs → one chip per unique token (≤ small max, e.g. 3).
- Importing into a case conversation — allowed (notes are conversation-scoped like any other).
- Blog URL pasted by its own author → note created normally (chatting with your own blog is the headline use case).
- Copy-of-copy re-share then import → root propagation dedupes back to the original.
