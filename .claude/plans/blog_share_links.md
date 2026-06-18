# Plan — مدونة / Public Share-by-Link for تحليل قانوني

**Goal:** Let any authenticated user publish a **written artifact** (`agent_writing` — تحليل قانوني / رأي قانوني …) to a **public, unlisted, read-only page** at an **unguessable URL** that anyone can open **without signing in**. The page shows **the question (السؤال) + the full answer**, rendered with the **exact same fluidity as the in-app artifact/search view** (clickable `[n]` citations → references panel → source popups), and ends with a marketing CTA («جرّب ريحان مجاناً» + sign-in / sign-up). Built for sharing a prospect's own answer with them via message.

## Decisions (locked with user)

- Source = **written artifacts only** (`kind = 'agent_writing'`). Not chat replies, not search items.
- **Snapshot model (A):** at publish time we freeze `content_md` + the fully-resolved `Reference[]` (incl. `source_view`) into the post row. The public page reads only the snapshot — immutable, no anon access to live workspace data, survives later edits/deletes of the artifact.
- **Sources ARE shown** with full fluidity (reuse `ArtifactPreview` + `ReferencePanel`, same citation wiring as `AgentSearchViewer`).
- **Question** = the triggering user message, **pre-filled but editable at publish** (scrub PII / tidy). Stored verbatim as chosen. `query_restatement` is NOT used.
- Publisher = **any authenticated user**, on an artifact they own.
- Route = `/blog/<token>`, token unguessable. No public index.
- Revoke = owner can unpublish/delete a post (kill switch for a leaked link).

## Data model — migration `shared/db/migrations/070_blog_posts.sql`

`blog_posts`:
| col | type | notes |
|---|---|---|
| `post_id` | uuid PK | `gen_random_uuid()` |
| `token` | text UNIQUE NOT NULL | unguessable slug → URL (`encode(gen_random_bytes(16),'hex')` = 32 chars) |
| `owner_user_id` | uuid NOT NULL | `users.user_id` of publisher (FK, RLS owner scope) |
| `source_item_id` | uuid | provenance ref to `workspace_items.item_id` — **no FK cascade** (snapshot is independent; artifact deletion must not delete the post) |
| `subtype` | text | e.g. `legal_synthesis` → label "تحليل قانوني" |
| `question_text` | text NOT NULL | the السؤال shown on the page (edited) |
| `title` | text | defaults to artifact title (page heading + OG title) |
| `content_md` | text NOT NULL | **snapshot** of artifact body |
| `references_json` | jsonb NOT NULL default `'[]'` | **snapshot** of resolved `Reference[]` (`model_dump(mode="json")`) |
| `is_published` | boolean NOT NULL default true | unpublish toggle (anon read filters on it) |
| `view_count` | integer NOT NULL default 0 | optional analytics (cheap; increment on public GET) |
| `created_at` / `updated_at` | timestamptz default now() | |
| `deleted_at` | timestamptz | soft delete |

**RLS (respecting 2026-06-11 audit — no anon DML):**
- `ENABLE ROW LEVEL SECURITY`.
- `SELECT` policy for `anon` + `authenticated`: `USING (is_published AND deleted_at IS NULL)` — public read of published rows only.
- **No** INSERT/UPDATE/DELETE policy for `anon`. Writes happen via backend (service role) on authenticated, ownership-checked endpoints only.
- Index on `token` (unique), partial index `WHERE is_published AND deleted_at IS NULL`.
- Apply via Supabase MCP to **prod** (migration-drift memory: files aren't auto-applied) + verify with `list_tables`/advisors.

## Backend

**`backend/app/services/blog_service.py`** (new)
- `create_post(supabase, auth_id, item_id, question_text) -> dict`: ownership check (`workspace_service.get_workspace_item`), assert `kind == 'agent_writing'`, snapshot `content_md`, resolve refs via `references_service.fetch_item_references(item_id, used_only=True)` → `model_dump(mode="json")`, mint token, insert row, return `{token, ...}`.
- `derive_default_question(supabase, item) -> str`: from `item.message_id` find the assistant message, then the **preceding user message** in the same conversation → its content. Fallback: artifact title / "".
- `get_public_post(supabase, token) -> dict | None`: select where `token = … AND is_published AND deleted_at IS NULL`; best-effort `view_count += 1`.
- `unpublish_post(supabase, auth_id, post_id)`: owner-scoped soft delete / `is_published=false`.

**`backend/app/api/blog.py`** (new router, mounted `/api/v1` in `main.py`)
- `GET /public/blog/{token}` — **no auth dependency** → `{question_text, title, content_md, references, subtype, created_at}`; 404 (Arabic) if missing. Add `/api/v1/public/blog` style path; consider adding to rate-limit handling (keep limited, tokens unguessable so enumeration is hard).
- `GET /workspace/{item_id}/share-draft` — auth → `{default_question}` (calls `derive_default_question`) to pre-fill the dialog.
- `POST /workspace/{item_id}/share` — auth, body `{question_text}` → creates post → `{token, public_url}` (public_url built from a `PUBLIC_WEB_URL`/frontend-origin setting).
- `DELETE /blog/posts/{post_id}` — auth owner-only → revoke.
- Pydantic request/response models in `backend/app/models/`.

**Public-endpoint check:** auth is per-endpoint (`Depends(get_current_user)`), so omitting that dep = public. No global auth middleware. `RateLimitMiddleware` still applies (fine).

## Frontend

1. **`frontend/components/auth/AuthGuard.tsx`** — add a `PUBLIC_PREFIXES = ['/blog']` allow-list: skip the `/login` redirect AND the `return null` when `pathname` starts with a public prefix, so anon visitors render the page. (AuthGuard wraps every route via `providers.tsx`.)

2. **`frontend/app/blog/[token]/page.tsx`** (server component) — fetch `${NEXT_PUBLIC_API_URL}/api/v1/public/blog/{token}` server-side; `notFound()` on 404; render `<PublicAnswerView post={…} />`. Export `generateMetadata` (og:title = question, description, site "ريحان") → rich link previews when shared via message/WhatsApp.

3. **`frontend/components/blog/PublicAnswerView.tsx`** (client) — the public reading surface:
   - **Header:** ريحان logo + `ThemeToggle` + «تسجيل الدخول» / «إنشاء حساب» buttons → `/login`.
   - **Question block:** a card labelled السؤال showing `question_text` (+ small subtype chip "تحليل قانوني").
   - **Answer:** reuse `ArtifactPreview` with `content={content_md}`, `onCitationClick` wired to a local `focusedN`, `footer={<ReferencePanel references={…} focusedReferenceN={…} … />}` — **mirrors `AgentSearchViewer` exactly** (clickable `[n]`, source popups, external links, cross-refs). References come from props (the snapshot) — no auth hook.
   - **Footer CTA:** «جرّب ريحان مجاناً» panel + buttons → `/login`.
   - Wrapped in `dir="rtl"`, themed (Providers already supplies theme + fonts) → same look & feel as the app.

4. **Share UI — `frontend/components/workspace/ShareArtifactDialog.tsx`** (client) + a «مشاركة» button in `WorkspaceItemViewer` header, shown when `item.kind === 'agent_writing'`:
   - On open: `GET share-draft` → pre-fill an editable `question_text` textarea.
   - «نشر ونسخ الرابط» → `POST share` → copy `public_url` to clipboard → success state showing the link + «نسخ» again + «فتح».
   - Note shown: "لإخفاء معلومات حساسة، عدّل النص هنا أو حرّر المستند قبل النشر." (Body PII is scrubbed by editing the artifact in the normal editor before sharing — share snapshots current content.)

5. **`frontend/lib/api.ts`** — `api.getShareDraft(itemId)`, `api.shareArtifact(itemId, questionText)`, `api.unpublishPost(postId)`. (Public GET is server-side `fetch`, not via the token-aware `apiFetch`.)

6. **`frontend/types/index.ts`** — `BlogPostPublic`, share request/response types. Reuse existing `Reference` type (line 520) for `references`.

## Out of scope (v1)
- Public blog **index/listing** (explicitly link-only).
- A "my shared posts" management page (revoke exists via API; UI list can be phase 2).
- Editing the answer **body** in the share dialog (use the existing artifact editor first).
- Custom OG image generation (text meta only for v1).

## Success criteria
- Publish a تحليل قانوني artifact → get an unguessable `/blog/<token>` URL on the clipboard.
- Open that URL in a **logged-out** browser → see question + answer + working clickable citations + source popups + CTA, themed/RTL, no redirect to `/login`.
- Editing/deleting the original artifact does **not** change or break the public page (snapshot).
- Anon cannot read unpublished/deleted posts; anon cannot write (RLS verified).
- Owner can revoke a post → URL returns 404.

## Build order
1. Migration 070 (+ apply to prod, verify RLS).
2. `blog_service` + `blog.py` router + models + register in `main.py`.
3. AuthGuard allow-list + `/blog/[token]` page + `PublicAnswerView`.
4. Share dialog + button + api.ts + types.
5. Local boot + manual verify (logged-out incognito), then deploy (backend + frontend) per `/deploy`.
