# Public Blog v2 — مدوناتي + public gallery + curation

Status: **PLAN** (v1 partially built — see §10; v2 **inverts** the access model + adds مدوناتي and the «حفظ كمدونة» action).
Design: **Editorial + TOC rail**. Byline: **branded** (no personal name). Public gallery route: **`/blog`**.

---

## 1. The three blog surfaces (naming)

The user identified three "blog endpoints" — here's the consistent naming. Convention: **anon read surfaces live under `/public/...`; owner-scoped surfaces under `/blogs/...`.**

| # | Concept (user's word) | Who views | Frontend route | Backend route | Auth |
|---|---|---|---|---|---|
| 1 | **blog** — one shared post (current) | anyone with the link | `/blog/{token}` | `GET /public/blog/{token}` | anon |
| 2 | **blogs_public** — the public gallery | everyone, even logged-out (SEO) | `/blog` | `GET /public/blogs` | anon |
| 3 | **blogs_internal** = مدوناتي — my own blogs | the author only | `/blogs` (sidebar pill **beneath قوالبي**) | `GET /blogs/mine` | authed (owner) |

Service fns: `get_public_post` (1, exists), `list_public_blogs` (2 — replaces v1 `list_directory_posts`, filters `is_public`), `list_my_blogs` (3 — new, owner-scoped).

---

## 2. Access model (INVERTED from v1)

- **Anyone** publishes/saves blogs (both templates), as today.
- `/blog` gallery **and** every `/blog/{token}` post are **PUBLIC** — anonymous, indexable. No login to read.
- **`can_access_blog` changes meaning:** it no longer gates *viewing*. It now gates **curation** — *who can push a post into the public gallery* (`is_public = true`). **XL0rch is the first curator.**
- Net: viewing is open to all; the only gate is *publishing-to-public*.

---

## 3. Data model — one more flag (migration 085)

Reuse `blog_posts`. Three orthogonal booleans:
- `display_mode` — `question` | `title` (✅ built, 084).
- `is_published` — the post/link exists; kill switch (existing).
- **`is_public`** — **NEW**: present in the public gallery. Default `false`.

```sql
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_blog_posts_public_gallery
  ON blog_posts (created_at DESC)
  WHERE is_public AND is_published AND deleted_at IS NULL;
```
(The v1 `idx_blog_posts_directory` on `display_mode='title'` can be dropped — the gallery now keys on `is_public`.)

---

## 4. Actions on an agent output (the action bar)

Row becomes: **معاينة/تحرير · مشاركة · حفظ كمدونة · نسخ · 👍 · 👎**

- **مشاركة** (existing) — the share-by-link dialog (سؤال default / مدونة) → mints/returns the public `/blog/{token}` link.
- **حفظ كمدونة** (NEW) — one-tap "save this answer as a blog" into **مدوناتي**. Small dialog: a **title** field (pre-filled from the item title) → create → toast «تم الحفظ في مدوناتك». Always a مدونة (title-mode) post, `is_public=false`.
- **نسخ** / 👍 / 👎 unchanged.

Both مشاركة and حفظ كمدونة snapshot the item into a `blog_posts` row owned by the user, so **both appear in مدوناتي**. The difference is intent: مشاركة surfaces the link + template choice; حفظ كمدونة is the quick "add to my collection."

`WorkspaceItemActionBar` gains an `onSaveBlog?` prop (renders «حفظ كمدونة»), wired wherever `onShare` already is (agent_search / agent_writing viewers).

---

## 5. مدوناتي — a new sidebar pill **beneath قوالبي** (NOT inside /templates)

The sidebar (`components/sidebar/Sidebar.tsx`) is a pill nav — **المحادثات · القضايا · قوالبي** — where a single panel below swaps on `activeTab` (`ConversationList` / `CaseList` / `TemplateList`). مدوناتي is a **4th pill placed directly after قوالبي**, its own tab + list panel.

- **Sidebar:** add a `NavPill` «مدوناتي» right after the قوالبي pill (count = my blogs). New `activeTab` value **`"blogs"`** in `stores/sidebar-store.ts`. When active, the panel renders a **new `BlogList`** component.
- **`BlogList`** (mirrors `TemplateList`): lists the user's own posts via `GET /blogs/mine` — each row: **title, سؤال/مدونة badge, date, public/private dot**. Empty state «لا توجد مدونات محفوظة بعد.».
- **Route group `/blogs`** (mirrors the `/templates` group): `app/blogs/layout.tsx` + `BlogsLayoutClient` (sets `activeTab="blogs"`, same Sidebar + main split) and `app/blogs/[token]/page.tsx` to render the selected blog in the main pane with **management actions**: نسخ الرابط · حذف · and — **only if `can_access_blog`** — **نشر في المدونة العامة / إلغاء النشر** (toggle `is_public`). فتح-as-public opens `/blog/{token}`.
- Clicking a `BlogList` row routes to `/blogs/{token}`.

---

## 6. `/blog` public gallery (anon)

- The v1 `/blog` page **flips gated → anonymous**. Lists `is_public` posts as cards → each opens `/blog/{token}`.
- No login; **add to `sitemap.xml`** for SEO (follow-up, robots/sitemap already exist).
- **Removed** from v1: `user_can_access_blog` gating on the *view*, and the `_authorized_author_ids` author-scoping. `can_access_blog` is reused only for the **curate** action (§7 endpoints).

---

## 7. Design refinements (from the screenshots)

Diagnosed: the screenshots are a `question`-mode post with an **empty `question_text`** (a تحليل قانوني shared via the سؤال template, no derived question). `PublicAnswerView` renders the «السؤال» card unconditionally → a **hollow box**, and its `h1` isn't centered.

- **Kill the empty box** — render the «السؤال» card **only when `question_text.trim()` is non-empty**. When empty, fall back to the **centered-title hero** (the `BlogArticleView` look) — no hollow card.
- **Center the title** — center the heading in question mode too, matching the article.
- **Any سؤال or تحليل قانوني** — both source kinds publish fine; show the subtype kicker (تحليل قانوني / رأي قانوني …); show the «السؤال» label **only** when there's a real question.
- Result: an answer with no question reads as a clean centered article instead of a broken empty box. (Also lets us share تحليل قانوني cleanly without forcing a question.)

---

## 8. Endpoints — final set

| Purpose | Method · Route | Auth |
|---|---|---|
| Read one post | `GET /public/blog/{token}` | anon (exists) |
| Public gallery list | `GET /public/blogs` | anon (new; replaces `/blog/directory`) |
| My blogs (مدوناتي) | `GET /blogs/mine` | authed (new) |
| Pre-fill share/save draft | `GET /workspace/{item_id}/share-draft` | authed (exists; returns default_title) |
| Create blog from an item (مشاركة + حفظ كمدونة) | `POST /workspace/{item_id}/share` | authed (exists; takes display_mode/title) |
| Publish to public gallery | `POST /blogs/{post_id}/publish` | authed **+ can_access_blog** (new) |
| Remove from public gallery | `DELETE /blogs/{post_id}/publish` | authed (owner/curator) (new) |
| Delete a blog (kill switch) | `DELETE /blog/posts/{post_id}` | authed owner (exists) |

> `GET /blog/directory` (v1, gated) is **removed/renamed** to `GET /public/blogs` (anon). The curation gate (`can_access_blog`) moves onto `POST/DELETE /blogs/{post_id}/publish`.

---

## 9. What changes vs the v1 build

**Reused as-is:** مدونة template toggle in the share dialog, `BlogArticleView` + `BlogTableOfContents`, `BlogPageShell`, `MarkdownRenderer` `headingAnchors`, share-by-link, `display_mode`/`can_access_blog` columns.

**Changed:**
- `list_directory_posts` (gated, author-scoped) → `list_public_blogs` (anon, `is_public`). Drop `_authorized_author_ids`.
- `GET /blog/directory` (auth) → `GET /public/blogs` (anon).
- `/blog` page: gated client page → **anon** gallery.
- `can_access_blog`: view-gate → **curate-gate**.

**New:**
- Migration 085 (`is_public` + index).
- `list_my_blogs` + `GET /blogs/mine`; publish/unpublish endpoints.
- «حفظ كمدونة» action + dialog; `onSaveBlog` on the action bar.
- مدوناتي page under `/templates`.
- `PublicAnswerView` empty-question refinement + centered title.

---

## 10. v1 build state (already on disk / applied)
- ✅ Migration **084** applied (display_mode, can_access_blog, directory index).
- ✅ Backend share per-template + `get_public_post` display_mode + `FORBIDDEN` code.
- ✅ Frontend: types, api, headings util, `MarkdownRenderer.headingAnchors`, `BlogPageShell`, `ShareArtifactDialog` template toggle, `BlogArticleView` + TOC, `PublicAnswerView` refactor, `[token]` branch, gated `/blog` directory page.
- ⚠️ The gated directory + author-scoping (`list_directory_posts`, `_authorized_author_ids`, `GET /blog/directory`, gated `/blog` page) get **reworked** by v2.
- XL0rch (`xl0rch@gmail.com`) already `can_access_blog = true`.

---

## 11. File manifest (v2 delta)
**New:** `085_public_blog_is_public.sql`; `app/blog/page.tsx` rewrite (anon gallery); **مدوناتي sidebar pill + `BlogList`** (`components/sidebar/BlogList.tsx`); **`/blogs` route group** (`app/blogs/layout.tsx`, `BlogsLayoutClient`, `app/blogs/[token]/page.tsx`); a «حفظ كمدونة» dialog component; per-card publish toggle.
**Modified:** `stores/sidebar-store.ts` (+`"blogs"` tab), `components/sidebar/Sidebar.tsx` (+مدوناتي pill), `blog_service.py` (list fns + publish toggles), `api/blog.py` (new routes), `responses.py` (my-blogs + status types), `types/index.ts`, `lib/api.ts`, `WorkspaceItemActionBar.tsx` (+onSaveBlog), the viewers that wire it, `PublicAnswerView.tsx` (empty-question + centered title).

---

## 12. Open confirmations (before building)
1. **مدوناتي contents** — show **all** my blog_posts (both templates, with a سؤال/مدونة badge), or only مدونة (title) ones? *(rec: all, badged.)*
2. **مشاركة vs حفظ كمدونة** — both create a `blog_posts` row, so both land in مدوناتي. OK? Or should مشاركة links be excluded from مدوناتي? *(rec: both appear.)*
3. **Endpoint names** — `blog` / `public/blogs` / `blogs/mine` good?
4. **مدوناتي placement** — a tab inside the قوالبي sidebar vs a `/templates/blogs` sub-route. *(rec: tab in the templates area.)*
5. Proceed with **migration 085** (`is_public`)?
