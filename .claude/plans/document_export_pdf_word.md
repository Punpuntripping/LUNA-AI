# Document export — «PDF» button with PDF / Word options

Status: PLANNED 2026-10-08

## Decisions (user)

| # | Decision |
|---|---|
| D1 | Surfaces: **writer drafts** (`agent_writing` workspace items) and **search reports** (`agent_search`). Not notes, templates, convo_context, or references. |
| D2 | **Server-side generation**, real file download (not a print dialog). |
| D3 | One trigger labelled **«PDF»** (download icon) in the workspace item action bar. It opens a dropdown with two items, each with its own icon: **PDF** and **Word**. |
| D4 | Structure must survive: headings, bold/italics, lists, tables, `[n]` markers, «مرفق رقم n», the «المراجع» block, Arabic RTL. |

## Contract

`POST /api/v1/export`, authenticated (same auth dependency as the workspace routes).

Request:

```json
{ "format": "pdf" | "docx", "title": "string ≤ 200", "markdown": "string ≤ 400_000 chars" }
```

- `markdown` = the action bar's existing `copyText`: body plus the «المراجع» block built by `appendReferencesForCopy`. The export therefore always equals what «نسخ» copies, and the server never re-derives references.
- Response `200`:
  - PDF: `application/pdf`.
  - Word: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`.
  - `Content-Disposition: attachment; filename="document.<ext>"; filename*=UTF-8''<percent-encoded sanitized title>.<ext>`.
- Errors carry Arabic messages (rule 5):
  - `422` invalid input;
  - `413` too large;
  - `503` «تعذّر إنشاء الملف حالياً، حاول مرة أخرى» when a renderer is unavailable.
- Rate limit with the existing limiter pattern (e.g. 20/min per user). It is a CPU job and must not become a free rendering service.

## Rendering

### Shared

Markdown → HTML through ONE converter, with tables, lists and fenced code. Use a lib already in `requirements.lock` if present; otherwise add a pinned one.

### PDF — WeasyPrint

- Wrap the HTML in `<html dir="rtl" lang="ar">` with a print CSS:
  - **Font:** IBM Plex Sans Arabic TTFs (Regular/Bold), **committed** under `backend/app/assets/fonts/`, OFL licence file alongside, loaded via `@font-face` with a file:// URL.
  - **Page:** A4, ~2cm margins.
  - **Footer:** page number, «صفحة n من m» via `@page` counters, Latin digits (latin-numerals policy).
  - **Tables:** bordered and full-width, `thead` repeats across pages.
  - **Headings:** `page-break-after: avoid`.
  - The title is rendered as H1 only when the body does not already start with an H1.
- **No branding**, no watermark: lawyers file these documents.
- WeasyPrint needs system libs. Add them to the runtime stage of `backend/Dockerfile`:

  ```
  apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
  ```

  plus `fonts-dejavu-core` as a Latin fallback, then clean apt lists.

### Word — `pypandoc_binary`

- Pinned. It bundles pandoc, so no apt package is needed.
- Converts markdown to docx with RTL (`-M dir=rtl` / `lang=ar`).
- Uses a committed **reference.docx** (`backend/app/assets/export/reference.docx`) that sets the Arabic font (IBM Plex Sans Arabic → Arial fallback), RTL paragraph direction, heading styles and table style. Generate the reference docx with a small script kept in the repo, so it is reproducible.

### Rules for both

- **Lazy imports** inside the service functions. A missing system lib must break export only (`503`), never app boot. `backend.app.main` must import cleanly without WeasyPrint's native libs (this matters on Windows dev too).
- Rendering is blocking: run it in a threadpool (`run_in_threadpool` / `asyncio.to_thread`).

## Frontend

### `components/common/ExportMenu.tsx` (new)

- Trigger: ghost button, `Download` icon + label «PDF». It matches the `bar` look of `CopyMenu` (variant prop like CopyMenu if needed).
- Dropdown (portalled z-[70] like CopyMenu), two items with distinct icons:
  - «PDF» — red-tinted file icon;
  - «Word» — blue-tinted file icon.

  lucide `FileText` / `FileType2` with color classes, or small inline SVG badges. No brand logos.
- On click:
  1. POST `{format, title, markdown: copyText}` through the existing authed api client (token in memory — never `fetch` without auth).
  2. Receive a blob and trigger the download via an object URL with `a.download = <sanitized title>.<ext>`.
- While busy: spinner on the trigger, trigger disabled. On failure: inline Arabic error / toast «تعذّر تنزيل الملف».

### Wiring

`WorkspaceItemActionBar` gains an optional prop (e.g. `exportTitle?: string`). When set, it renders ExportMenu next to نسخ with the same `copyText`. Pass it only from:

- `AgentSearchViewer` — search reports;
- `MarkdownDocEditor`, **only for agent_writing** (not notes/templates).

When in «تحرير» mode, the export uses the current editor content, the same as نسخ.

### Analytics (optional)

`document_exported {format, kind}`. If added, it must also go on the backend allow-list (`CHAT_EVENT_NAMES`) and the count test.

## Tests

- **Backend:**
  - docx export round-trip: it is a valid zip, contains the heading text and the table cell text, and `w:bidi` is present.
  - PDF:
    - when WeasyPrint is importable (CI/Docker): starts with `%PDF`;
    - otherwise the service returns 503 (mock the import failure).
  - `413` on oversize, `422` on bad format.
  - Content-Disposition carries a UTF-8 Arabic filename.
  - Auth required.
- **Frontend:** `tsc --noEmit`, lint.
- **Live, after deploy:** export a real draft and a real search report in both formats. Open them and check Arabic shaping, RTL, tables, «المراجع».

## Rollout

- The backend image grows by ~60MB (pango + pandoc wheel). Deploy backend and frontend together. The frontend button can only fail gracefully (503 → Arabic error) if the backend is behind.
