# «ملفاتي» — the user's file library, and files shown by sections

**Status:** Phase 1 + the backend half of 1b BUILT 2026-11-19, uncommitted, not deployed, flags OFF —
worktree `C:\Programming\LUNA_AI_my_files`, branch `feat/my-files` from `origin/master` @ `1098ce4`.
Waiting for the owner's review of the look and the Arabic copy on the local preview (§12). No
migration, no agent or prompt change, no production write so far.

**Code base for line numbers:** `origin/master` @ `1098ce4`. The main checkout is on
`feat/mobile-phase4b-iap` with a dirty tree and does NOT contain the segmentation code — build in a
fresh worktree from `origin/master`.

**Related plans:** `attachment_segmentation.md` (the map this UI shows), `helper_frame.md` (how OCR →
summary → segmenter run), `large_document_index.md` (100+ page files), `case_documents_forwarding.md`.

**Vocabulary:** *file* = an uploaded attachment in its extracted form (text, summary, type, sections).
*Library* = «ملفاتي». *Pane* = the conversation's side panel opened by «المخرجات». *Map* = the stored
segmentation of one file (`attachment_segment_maps` + `attachment_segments`).

---

## 0. In one paragraph

Today an uploaded file belongs to exactly one conversation, shows as a title-only card in the pane, and
opens as one long block of extracted text. This plan (1) splits the pane into two tabs, «العناصر» and
«الملفات»; (2) shows a file as title + summary, and opens it section by section using the map the
segmenter already stores; (3) adds «ملفاتي», a tab inside the conversations page listing all the user's
files arranged by document type; (4) lets the user attach a file from «ملفاتي» to any conversation and
upload a file straight into «ملفاتي»; (5) moves files out of `workspace_items` into their own table so a
file is no longer owned by one conversation. Phases 0–1 need no agent change and no migration. Phases
2–3 touch storage, the upload flow and what agents read, and each needs the owner's go.

---

## 1. Owner decisions (from /reflect, 2026-11-18)

| # | Decision |
|---|---|
| D1 | The pane is split into two tabs: «العناصر» (drafts, search results, notes) and «الملفات» (uploaded files, extracted form). |
| D2 | A file card shows the title and the summary. |
| D3 | An opened file is shown by its sections, taken from the stored map. From the reference page only the section list is kept. |
| D4 | **Corrected by the owner 2026-11-19.** The library is its own row in the SIDEBAR, beneath a «المحادثات» row, and is named **«الملفات»** (not «ملفاتي»). It opens its own page. It is NOT a tab inside the conversations page — the first build did that by misreading the answer. The two-tab design is only for inside the pane («المخرجات»). Wherever this plan still says «ملفاتي», read «الملفات». |
| D14 | **Every attachment migrates to the new table, and the agents read from it (owner 2026-11-19: "we must migrate all attachment").** No attachment is left behind: live and soft-deleted rows, with or without text, sent or never sent. Prod on 11-19: 875 attachment rows (835 live, 40 soft-deleted) for 109 users, with 805 page rows, 80 maps (2,682 section rows) and 80 helper runs that move under the file, and 599 message links that stay on the conversation's item (the pointer) and so keep working untouched. Phase 2 ends with a count check: rows before = rows after, per user. Shape: D15. |
| D15 | **Table shape = thin pointer (owner 2026-11-19, closes O2).** All file content lives in the new table; each use of a file in a conversation keeps one small `workspace_items` row with the WI number and a link to the file. |
| D16 | **Type backfill from summaries, 10 files per call (owner 2026-11-19)** — not one summarizer run per document. See §6. |
| D13 | Sidebar rows, in this order (owner 2026-11-19): «المحادثات» · «الملفات» · «القوالب» (was «قوالبي») · «المدونات» (was «مدوناتي») · «المكتبة» (was «مكتبتي»). Only the sidebar rows are renamed so far; the page headings, the composer menu, dialogs, learn copy and the mobile app still say the old names — owner to decide. |
| D5 | Files enter «ملفاتي» automatically. **Taken as: every file whose text was extracted.** See O1 — the answer could also be read as "segmented only". |
| D6 | The user can attach a file from «ملفاتي» to a conversation, AND upload a file straight into «ملفاتي» with no conversation. |
| D7 | No metadata fields. A section that is metadata is only tagged as metadata (the segmenter already does this). Structured metadata belongs to another agent, out of this plan. |
| D8 | «ملفاتي» is arranged by document type (the summarizer's `doc_type`). Existing files without a type get one. |
| D9 | Deleting from «ملفاتي» removes the file from the library and from future attaching; conversations that already used it keep working. |
| D10 | Files move from `workspace_items` to their own table. It is its own step, after the read-only screens; «ملفاتي» reads the current table until then. |
| D11 | Order: mockup first, then build. |
| D12 | When the user attaches a file that we are confident is one they already have (same file type, size and name), we tell them we found it and offer to use the existing one. It is used only after the user approves; otherwise the new upload goes ahead as normal. |

Carried from round 1 as defaults (owner did not object): hide letterheads and repeats; no
جوهري/مساند labels shown to the user; a file without a map opens as full text; two summary lines on the
card and the full summary inside the file; web first, mobile later.

---

## 2. What exists today

**The pane.** `frontend/components/workspace/WorkspacePane.tsx` — list mode renders `WorkspaceList`
(groups by `kind`, order: المسودات، نتائج البحث، الملاحظات، المرفقات، المراجع، ملخص المحادثة), detail
mode routes by kind; attachments go to `AttachmentRenderer` (full `content_md` + «الملف الأصلي» toggle).
Card = `WorkspaceCard.tsx`: title, WI badge, «مرفق» pill, time. The same `WorkspaceList` is used by
`/cases/[case_id]/artifacts` (cases feature is OFF) and is ported in `mobile/src/components/workspace/`.

**The conversations page.** `frontend/components/chats/ChatsPage.tsx` — `h1` «المحادثات», a filter
dropdown, «محادثة جديدة», a search box, an infinite list. No tabs.

**Where a file's data lives** (all on the `workspace_items` row, `kind='attachment'`):

| Data | Where | Reaches the app? |
|---|---|---|
| Title (grounded, by the summarizer) | `title` | yes |
| Extracted text | `content_md` | yes |
| Summary | `summary` column (`agents/memory/summarize.py:328`) | **no** — `WorkspaceItemResponse` has no such field |
| Document type | `metadata.doc_type` (`agents/memory/summarize.py:325-326`), 14 values in `agents/memory/artifact_summarizer/models.py` `DocType` | yes (inside `metadata`), unused by the UI |
| Pages | `attachment_pages` (migration 215) | no |
| Map + sections | `attachment_segment_maps`, `attachment_segments` (215): `seq`, page range, `label` (English key), `value` (metadata / core / supporting / boilerplate / repeat), `what` (Arabic, ≤ 15 words), `content_md` (original text) | **no** — service-role only, no endpoint |
| Original file | Supabase Storage via `storage_path`; purged by the retention sweep (`metadata.original_purged_at`), text kept | signed URL |

The `metadata` value is in the segmenter prompt: `agents/memory/attachment_segmenter/prompts.py:151`
(seg-v5.1) — "the document's identifying information at its FIRST occurrence". Nothing to add for D7.

**A file is owned by one conversation.** `workspace_items.conversation_id` + the per-conversation alias
`wi_seq` (trigger, migration 052). `message_attachments.document_id` points at the item. About 30 agent
queries load a conversation's items with `.eq("conversation_id", …)` (router, planners, orchestrator,
memory, OCR, summarizer, segmenter). There is a precedent for a pointer row: `POST
/conversations/{id}/workspace/attachments/from-document` creates an attachment item that only carries
`document_id` → `case_documents`.

**Processing happens at message-send.** `agents/orchestrator.py:2796-2830`: OCR for the conversation's
unread files (`run_ocr_extraction(supabase, conversation_id, user_id)`), then the summarizer per file,
then the segmenter through the helper frame (`agents/helpers/registry.py`: `ocr` → `summary` →
`segmenter`). The summarizer reads the conversation context and writes a «صلة المستند بالمحادثة» section
inside the summary; the segmenter reads the user's messages (promote-only).

**Live numbers (prod, 2026-11-18, read-only).**

- 823 files (not deleted) for 101 users; median 3 files per user, maximum 169.
- 629 have extracted text; 194 have none. 615 have a summary (median 2,132 chars).
- 385 have a type; 438 do not (244 of those have text — uploaded before the label existed).
- By type: حكم 62 · عقد 58 · سجل رسمي 51 · محضر جلسة 33 · محادثة 32 · خطاب 26 · صحيفة دعوى 26 · مذكرة 26 · مرجع 19 · تقرير خبير 18 · مالي 14 · أخرى 9 · ملاحظات المستخدم 6 · إقرار 5.
- 65 files have a map (segmenter on for everyone since 11-14; files > 5,000 chars, or small files sent together). Median 17 sections per map, p90 96, maximum 318; on average 17 core, 14 letterhead/repeat, 3 metadata, 2 supporting. Longest single section 31,886 chars. 15 maps hold several documents in one file.
- 425 originals already purged by retention (text kept).
- 116 groups of the same file (name + size + type) uploaded more than once by the same user: 272
  files, 156 extra copies, 34 users, up to 7 copies of one file. 54 groups sit inside one conversation;
  62 span several conversations (91 extra copies). 93 extra copies had their text extracted again.
  Half of the groups were re-uploaded within about 30 minutes of the first copy.
- How safe the name + size + type match is: of the 75 groups where 2 or more copies have text, 66 have
  text lengths within 5% of each other; 3 differ by more than half (partial or failed extractions of
  the same bytes, to confirm at build).
- 265 files were never sent with a message.

---

## 3. What the user sees

### 3.1 Pane tabs

Two tabs under the pane header, list mode only: **«العناصر»** and **«الملفات»**, each with its count.

- «العناصر»: `agent_writing`, `agent_search`, `note`, `references`, `convo_context` — grouped as today.
- «الملفات»: `attachment` items as file cards (§3.2), newest first.
- Default tab «العناصر». Opening or highlighting a file from a chat chip switches to «الملفات».
- The add menu at the bottom follows the tab: note / reference in «العناصر»; «رفع ملف» and (Phase 3)
  «من ملفاتي» in «الملفات».
- The pane title is «العناصر» today, the same word as the first tab. Proposal for the mockup: pane
  title becomes «المخرجات» (the name of the button that opens it).
- `data-tour` anchors of «جولة المخرجات» must keep working (`pane-close`, `wi-badge`, the list).

### 3.2 File card

Type pill (Arabic name of `doc_type`) · title · the first two lines of the summary · a quiet line with
page count (when known) and date. In the pane the card also keeps the WI badge. States: «جارٍ استخراج
النص…» while processing; «لا يحتوي على نص قابل للاستخراج» / «تعذّر استخراج النص» from
`metadata.ocr_status`.

### 3.3 Opened file

One `FileViewer` component, used by the pane's detail mode and by «ملفاتي».

- Header: title, type pill, pages, date.
- Summary: the full stored summary, collapsible.
- Toolbar: «الملف الأصلي» (hidden once purged, with the existing retention line), «نسخ النص».
- **Sections** (when the file has a fresh map): one row per section in document order, titled by `what`,
  with «ص 3–5» when page numbers exist. A row expands to the section's original text. Rows start
  collapsed, so a long file reads as an outline; «توسيع الكل» opens everything.
  - `boilerplate` and `repeat` sections are not shown.
  - `metadata` sections are shown with a small tag «بيانات المستند» (D7).
  - `core` and `supporting` carry no tag.
  - A file holding several documents (`documents_in_file` > 1) gets one heading per document.
- **No map, or a stale map** (`content_chars` ≠ current text length): the full extracted text, as today,
  under the same header and summary.

Not shown: English labels, paragraph numbers, character counts, the value strip, key-paragraph stars,
section owners (seg-v5.2, not deployed).

### 3.4 «الملفات» — a sidebar row and its own page

The sidebar nav becomes: «المحادثات» (opens `/chats`) · «الملفات» (opens `/files`) · «القوالب» ·
«المدونات» · «المكتبة» (D13). «الملفات» is a page of its own in the app shell, in the same family as the
other per-user pages (`/library/mine`, `/templates/mine`, `/blogs/mine`). The conversations page is
unchanged. All sidebar changes sit behind the feature flag, so with the flag off the sidebar is exactly
today's.

**Card grid (owner 2026-11-19: "design something unique if you want to, like the cards in templates").**
The page is a card grid in the family of `MyTemplatesGrid` (header with icon tile, title, count line and
action; grid of 2–3 columns), not a single-column list. The file card is a "document tile": a type icon
per document type, a small paper cue (folded corner or tinted spine), tint by a few type families from
the theme's existing tokens, title, three summary lines, pages and date, and a quiet mark when the file
opens as an outline. The pane's file cards inside «المخرجات» keep their list form. The bullets below
that describe a single list (search, chips, grouping rule, menu) still apply, laid out as grids.

- Top: a search box (filters by title and summary) and «رفع ملف» (Phase 3).
- Filter chips: «الكل» + one chip per type the user actually has, with counts.
- Body: with more than 6 files in 2 or more types, files are grouped under type headings, largest group
  first, newest first inside a group. Otherwise one flat list, newest first — the median user has 3
  files and headings would be noise.
- Card = §3.2 plus a menu: «فتح», «أضف إلى محادثة» (Phase 3), «حذف من ملفاتي».
- Opening a card goes to `/files/{id}` (the `FileViewer` as a page; back returns to `/files`).
- Empty state explains that files sent in conversations are collected here.

Proposed Arabic names for the types (copy to approve in the mockup): أحكام · محاضر جلسات · صحف دعوى
ولوائح · مذكرات · عقود · مراسلات ومحادثات · خطابات وإنذارات · مستندات مالية · سجلات رسمية · تقارير خبرة ·
إقرارات وشهادات · ملاحظاتي وجداولي · مراجع ونصوص · أخرى. («محادثة» is renamed in the heading so it
does not collide with the «المحادثات» tab.)

### 3.5 Attach from «ملفاتي» (Phase 3)

- In a conversation: composer «+» → «مرفق» becomes a submenu «رفع ملف» / «من ملفاتي», the same shape as
  «مدونة» → «من مدوناتي». The pane's «الملفات» add menu offers the same.
- In «ملفاتي»: «أضف إلى محادثة» on a card → pick a recent conversation or start a new one with the file
  attached.
- The file arrives already extracted, summarised and segmented: no new OCR pages, no new model calls.

### 3.6 Upload into «ملفاتي» (Phase 3)

«رفع ملف» in the tab. The card appears at once in the processing state; extraction, summary and
segmentation run in the background; the card fills in when ready. A file the user already has gets the
same proposal as §3.8.

### 3.7 Delete from «ملفاتي»

A confirmation that says the file will leave the library and stay inside the conversations that used
it. After that it is not listed and cannot be attached.

### 3.8 The same file attached again (D12)

When the user picks a file in the composer (or in «ملفاتي»), the app asks the backend whether this user
already has it.

- **Match rule:** same user, same file name, same size in bytes, same file type. Only a file whose text
  was extracted completely is proposed — not one whose extraction failed, was empty, or was cut short
  by the page budget (a fresh upload may do better). A file removed from «ملفاتي» is never proposed.
  Several matches → the newest; a copy in the same conversation wins over a newer one elsewhere.
- **Also not proposed (found at build, 11-19):** a copy with pages that could not be read
  (`ocr_lost_ranges`), a copy whose text came from the PDF's own text layer after OCR failed
  (`ocr_text_layer_ranges`), and a copy from before 11-16 where extraction silently stopped at 30 pages
  (`ocr_pages < page_count`). One function, `files_service._extraction_complete`.
- **What the user sees (owner, 2026-11-18): an approval popup built like the ask-user question popup.**
  It is a sibling of `AskQuestionsDialog` (`frontend/components/chat/AskQuestionsDialog.tsx`, contract
  `frontend/lib/contracts/ask-questions.ts`) made for approvals: the question «وجدنا هذا الملف ضمن
  ملفاتك: «العنوان» · التاريخ — هل نستخدمه؟» and two options, «استخدام الموجود» (with the note that
  nothing is extracted again) and «رفع نسخة جديدة». No «أخرى» box and no skip. A match in the same
  conversation names its WI number. Several matched files in one message = one question per file in
  the same popup (the popup already pages through up to 7).
- **Who asks.** The app itself, at the moment the file is picked — not an agent. Nothing is sent to a
  model, no turn is paused, and the answer is not a chat message. This is the difference from
  `ask_user` / `ask_questions`, where an agent pauses the turn and the answer returns as a user message.
- **Nothing changes without approval.** The upload starts as usual while the popup is open. «استخدام
  الموجود» → the upload is cancelled and the existing file is attached. «رفع نسخة جديدة», or closing
  the popup → the new file is used, exactly as today.
- **Same shape for mobile.** The approval uses the question-popup contract (question + options), so the
  mobile app can show it with the same popup once that is ported.
- **What approval saves:** no new extraction (no OCR pages), no new summary or segmentation, no wait.
- **Two cases, two phases.** A match inside the same conversation is re-attached as the same item —
  this works on today's storage. A match from another conversation needs the file to be attachable
  elsewhere, so it arrives with Phase 3.

---

## 4. Data model — files in their own table (Phase 2)

### 4.1 Recommended shape (A): file table + thin pointer row

**`user_files`** — one row per file per user: `file_id`, `user_id`, `title`, `filename`, `mime_type`,
`file_size_bytes`, `storage_path`, `original_purged_at`, `content_md`, `content_chars`,
`page_count`, `ocr_status`, `summary`, `summary_updated_at`, `doc_type`, `origin` (`chat` / `library`),
`in_library` (false after §3.7), `created_at`, `updated_at`, `deleted_at`. RLS on, owner-scoped SELECT
policy, no grant to anon / authenticated (migration 195 stance), service_role DML.

**`attachment_pages`, `attachment_segment_maps` (+ `attachment_segments`), `helper_runs`** — keyed by
`file_id` instead of the item.

**A conversation's use of a file stays a `workspace_items` row** of `kind='attachment'` carrying
`file_id`. It keeps what is per conversation: `conversation_id`, `wi_seq` (WI number), `is_visible`,
the message links. It holds no text of its own. This is the existing `document_id` pattern.

Why this shape: the ~30 conversation-scoped agent queries, the WI numbering trigger and
`message_attachments` all stay where they are. Only the code that reads a file's *content* (text,
summary, type, pages, sections) changes, and it changes to one loader.

### 4.2 Alternative (B): no row in `workspace_items` at all

A pure link table `conversation_files(conversation_id, file_id, wi_seq, …)`. Every conversation-scoped
query that expects files, the WI numbering (one sequence across two tables), `message_attachments` and
the history tags must change. Same result for the user, much larger change in the agents. See O2.

### 4.3 Migration path for A — expand, switch, contract

1. **Expand** (additive, reversible): create `user_files`; add `workspace_items.file_id`; add `file_id`
   to pages / maps / helper_runs; backfill one file per existing attachment row. New uploads write both.
   No reader changes yet.
2. **Switch** (flag): readers take content through one loader — router context
   (`agents/router/context.py`), delivery (`agents/utils/attachment_delivery.py`,
   `attachment_sections.py`), writer exhibits, case documents, the orchestrator's attachment loaders;
   writers (OCR, summarizer, segmenter, retention sweep, upload reconciler) write to the file.
3. **Contract** (after a soak): stop the double write; empty `content_md` / `summary` on attachment rows;
   pages / maps primary keys move to `file_id`.

Next free migration number: **220** (216 is the last on master's segmentation line, 217–218 exist,
219 is held by the uncommitted seg-v5.2 work). Verify prod before numbering (`project_migration_drift`).

---

## 5. Backend API

**Phase 1 (read-only, no migration):**

The exact shapes are in §11 (the contract both the backend and the web are built against).

- `WorkspaceItemResponse` gains `summary_preview: Optional[str]` (additive; mobile-safe) — the two
  lines the pane's file card shows.
- `GET /api/v1/files` — the user's library, light rows only. One response, newest first, capped at 500
  (the largest library today is 169); type filter and search happen on the client. Never returns
  `content_md` or the full summary.
  Rule (D5): `kind='attachment'`, not deleted, non-empty extracted text, home conversation not deleted
  (O4), not hidden from the library; identical files collapsed to one row (O6).
- `GET /api/v1/files/{item_id}` — everything the opened file needs: the row, the full summary, the full
  text, and `sections` (or `null`). Boilerplate and repeats are left out on the server; the English
  `label` and the core/supporting rating are not sent. No map or a stale map → `sections: null` and
  the client shows the full text. The pane's opened file uses the same endpoint.
- `DELETE /api/v1/files/{item_id}` — Phase 1: stamps `metadata.library_hidden_at`; the conversation's
  item is untouched.
- `POST /api/v1/files/match` (Phase 1b) — body `{ filename, size_bytes, mime_type, conversation_id? }`;
  answers `{ match: { item_id, title, created_at, same_conversation, wi_seq } | null }` by the rule in
  §3.8. Called before the upload session is opened, so a match costs no row and no storage. In Phase 1b
  only `same_conversation` matches are returned; Phase 3 returns the others too. An index on
  (`user_id`, name, size) backs it once files have their own table; on the current table the user's
  attachment rows are few enough to filter directly (maximum 169).

All three verify ownership by `user_id` the way `get_workspace_item` does (404 for another user's id),
return Arabic errors, and sit under the existing workspace rate limits. Text is returned real, as
`content_md` is today (وضع السرية applies to what models see, not to the owner's own screen).

**Phase 3:**

- `POST /api/v1/conversations/{id}/workspace/attachments/from-file` — body `{ file_id }`; creates the
  pointer row (twin of `from-document`); verifies ownership of both the conversation and the file.
- `POST /api/v1/files/upload/init | finalize | cancel` — the existing resumable upload, without a
  conversation; `finalize` applies the OCR page gate and starts processing.
- `DELETE /api/v1/files/{file_id}` — sets `in_library=false`.

---

## 6. Agents — what is touched

Phases 0–1 touch **no agent code and no prompt**.

**Type backfill (Phase 1, a script) — method changed by the owner 2026-11-19: "one agent … 1 for every
10 docs … instead of wasting money".** `scripts/my_files_type_backfill.py` does not re-read the
documents. One call labels 10 files from their title and stored summary (the first 1,500 chars of text
when a file has no summary), with the type list taken from the summarizer's own prompt and the
summarizer's model (tier_2). Text is masked as in `summarize.py`; no ledger scope, so no user is charged.
Only `metadata.doc_type` is written, and only on rows that still have none; titles and summaries are
untouched. Dry run by default; `--apply --yes` writes; `--validate N` measures accuracy.

Measured 2026-11-19 (no writes):
- Accuracy on 150 files that already have a type: 139 the same (93%). Of the 11 that differ, 6 were
  labelled `other` where the summarizer had picked a type.
- Against the full-document labels of the largest library (92 files): 86 the same (93%).
- Dry run on all 244 untyped files: 244 labelled in 25 calls, 217k tokens in, 21k out — about $0.04 by
  token count, against about $0.26 for re-reading every document. It also labels the files under 300
  chars and the 18 without a summary, which the per-document route skips.
- Result: عقد 33 · محادثة 32 · حكم 31 · مالي 24 · محضر جلسة 24 · ملاحظات المستخدم 20 · خطاب 18 ·
  سجل رسمي 17 · أخرى 14 · صحيفة دعوى 10 · مرجع 8 · مذكرة 8 · إقرار 4 · تقرير خبير 1.

It is a prod write: the owner's go is still needed for `--apply`.

**Library uploads (Phase 3).** There is no message to trigger processing, so `finalize` starts the
helper chain for that one file: OCR needs an entry that takes one file instead of a conversation
(`agents/memory/ocr_extractor/runner.py` selects by conversation today); the summarizer already accepts
an empty conversation context; the segmenter already runs without user words.

**Reused files (Phase 3).** One summary and one map per file, used in every conversation. See O3 for
the «صلة المستند بالمحادثة» part of the summary.

**Phase 2** changes where agents read a file's content from (§4.3 step 2) but not what they read.

---

## 7. Phases

| Phase | What | Touches | Owner go needed |
|---|---|---|---|
| 0 | Mockup page, anonymized data: pane tabs + file card; opened file (with map / without); «ملفاتي» with 3 files and with 40; «+» → «من ملفاتي»; a card in the processing state | nothing in the app | approve look + copy |
| 1 | Read-only on the current table: §5 Phase-1 endpoints; pane tabs; file card; `FileViewer`; «ملفاتي» tab; delete-from-library; type backfill | backend API + web frontend; no agent, no migration | deploy; backfill run |
| 1b | Same-file proposal inside one conversation (§3.8): match endpoint + the approval popup | backend API + composer + the question popup; no agent, no migration | deploy |
| 2 | Own table, §4.3 steps 1–2 | migration, backend, agent readers/writers | each step |
| 3 | Attach from «ملفاتي»; upload into «ملفاتي»; same-file proposal across conversations and in «ملفاتي»; final delete semantics | backend, upload flow, OCR entry, web frontend | deploy |
| 4 | Contract step of §4.3; mobile port | migration; `mobile/` | each |

Rollout for 1–3: behind `MY_FILES_ENABLED` with a `_USERS` allow-list (the segmenter's pattern) — the
test account first, then everyone.

Attaching a library file to another conversation cannot ship before Phase 2: until a file stops being
owned by one conversation, the only way is to copy the whole row (text, pages, map) per conversation,
which the move would then have to undo.

---

## 8. Open decisions for the owner

- **O1 — which files enter «ملفاتي».** The plan takes "every file whose text was extracted" (629 today).
  "Segmented only" would list 65, and a short file uploaded straight into «ملفاتي» (D6) would never
  appear. It is one rule in `GET /files`; say so if segmented-only was meant.
- **O2 — table shape.** A (file table + pointer row, recommended) or B (no row in `workspace_items`).
- **O3 — the conversation link inside the summary.** The summary is written for the conversation the
  file was first sent in, and ends with «صلة المستند بالمحادثة». In «ملفاتي», and in a second
  conversation, that part is about another conversation. Options: leave it (Phase 1 does); or change the
  summarizer so the summary describes the document only and the link is stored per conversation.
  Recommended with Phase 3. It is a prompt change and needs the owner's OK.
- **O4 — deleting a conversation.** Today that soft-deletes the conversation only. Recommended: in
  Phase 1 its files leave «ملفاتي» too (what a user expects today); from Phase 2 the files stay in the
  library unless deleted there, and the delete dialog says so.
- **O5 — points for library uploads.** A file uploaded into «ملفاتي» costs OCR pages (the page budget)
  plus a summarizer and a segmenter call, which today are billed like any agent. Decide whether those
  two calls count against the user's points when no question was asked.
- **O6 — the 116 groups that already exist.** New attaches are covered by D12. For the copies already
  stored, recommended: «ملفاتي» shows one card per identical file (the D12 rule: same name, size and
  type), the newest copy with complete text; the stored rows are not merged or deleted.
- **O7 — how sure "confident" must be.** Name + size + type is what D12 names, and the user sees the
  found file's title and date before approving. It misses a renamed copy (harmless: it is uploaded as
  new). Comparing the file's bytes would be stricter but needs the whole file read first; not planned
  unless wanted.

---

## 9. Risks and traps

- The pane's list endpoint returns every item with its full `content_md`; the library list must not
  copy that (§5).
- A map made by an older prompt version is re-made on next use (`PROMPT_VERSION` bump); the sections
  endpoint must treat a missing or stale map as "no sections", never as an error.
- The longest section is 31,886 chars and one map has 318 sections: rows must render collapsed and the
  text only on expand.
- Section text is markdown from OCR (tables, image placeholders `![img-…]`): render with the existing
  preview renderer and drop the image placeholders.
- Joined sections (seg-v5.2 page-break join) will no longer be an exact slice of `content_md`; the
  viewer shows the stored section text and must not re-slice.
- `message_attachments.document_id` cascades from `workspace_items` — never hard-delete an attachment
  row (`project_attachment_retention_purge`).
- The retention sweep, the upload reconciler and the abandoned-upload OCR issue all act on attachment
  rows; Phase 2 must move them together.
- The question popup the approval is built on (`AskQuestionsDialog`, `ask-questions.ts`) is built but
  uncommitted, lives only in the main checkout, and no agent registers its tool
  (`project_ask_questions_tool`). Phase 1b must bring the popup component and its contract along; it
  must NOT register the `ask_questions` agent tool or touch `agents/orchestrator.py`.
- `frontend/lib/contracts` is shared with the mobile app: additive changes only; run @integration-lead.
- Section text and summaries are the user's own documents: endpoints owner-scoped, new table RLS on with
  no client grants; run @security-reviewer and @rls-auditor before Phase 2 ships.
- Any master push kills in-flight searches (`project_redeploy_kills_inflight_searches`): deploy in a
  quiet window.

---

## 10. Verification

- **Phase 1:** backend tests — another user's item → 404; no map → `sections: null`; stale map → null;
  boilerplate and repeats never returned; library list never carries `content_md`; hidden files not
  listed. Frontend — `tsc`, lint, build; Playwright on the test account: pane tabs and counts, file card
  with summary, a segmented file and an unsegmented file, «ملفاتي» with few and many files, type chips,
  delete from library, dark mode, phone width, the tour still runs.
- **Backfill:** dry-run prints counts per type and touches nothing; after the run, files with text and
  no type = 0, and titles / summaries of the 226 already-summarised files are byte-identical.
- **Phase 2:** before / after comparison of what the router, deep search and the writer receive for the
  same conversation (must be identical); WI numbers unchanged; account purge removes the file rows.
- **Phase 1b:** the same file picked twice in one conversation shows the approval popup; approve → upload
  cancelled, no orphan row, the message carries the existing item and no OCR pages are spent; decline or
  send without answering → a new upload exactly as today; a file whose first extraction failed or was
  partial gets no proposal; another user's identical file never matches.
- **Phase 3:** attach a library file to a second conversation — no OCR pages spent, no summarizer or
  segmenter call in the ledger, agents see it with a WI number; upload into «ملفاتي» — card goes from
  processing to ready; the same file picked in a different conversation or in «ملفاتي» shows the
  proposal and, once approved, is not processed again.

---

## 11. API contract — Phase 1 and 1b

Field names are final for the build; both sides are written against this section. All routes are under
`/api/v1`, need a signed-in user, and answer 404 (Arabic `detail`) for an id that is not the caller's.

```ts
/** agents/memory/artifact_summarizer/models.py `DocType`. */
type FileDocType =
  | 'judgment' | 'hearing_minutes' | 'claim' | 'memo' | 'contract' | 'chat' | 'letter'
  | 'financial' | 'official_record' | 'expert_report' | 'statement' | 'user_notes'
  | 'reference' | 'other';

interface LibraryFile {
  item_id: string;
  title: string;
  filename: string | null;        // metadata.filename (the upload's own name)
  mime_type: string | null;
  doc_type: FileDocType | null;   // null = no type yet
  summary_preview: string;        // plain text, at most 280 chars, "" when there is no summary
  page_count: number | null;
  chars: number;                  // length of the extracted text
  has_sections: boolean;          // a map exists and is not stale
  original_available: boolean;    // the original file can still be opened
  conversation_id: string | null;
  wi_seq: number | null;
  copies: number;                 // identical files folded into this row (1 = no copies)
  created_at: string;             // ISO 8601
}

// GET /files
interface LibraryFileListResponse {
  files: LibraryFile[];                    // newest first, at most 500
  total: number;
  type_counts: Record<string, number>;     // keys: a FileDocType, or "untyped"
}

interface FileSection {
  seq: number;                    // document order
  what: string;                   // Arabic description of the section
  is_metadata: boolean;           // value === 'metadata'
  page_from: number | null;
  page_to: number | null;
  document_index: number | null;  // index into `documents`; null when the file is one document
  content_md: string;             // the section's original text
}

// GET /files/{item_id}
interface LibraryFileDetail extends LibraryFile {
  summary: string | null;         // full markdown summary
  content_md: string;             // full extracted text
  documents: { name: string }[];  // empty unless the file holds 2 or more documents
  sections: FileSection[] | null; // null = no map or a stale map
}

// DELETE /files/{item_id}  → 204

// POST /files/match  (Phase 1b)
interface FileMatchRequest { filename: string; size_bytes: number; mime_type: string; conversation_id?: string }
interface FileMatch { item_id: string; title: string; created_at: string; same_conversation: boolean; wi_seq: number | null }
interface FileMatchResponse { match: FileMatch | null }

// WorkspaceItem (existing) gains:
//   summary_preview?: string | null   — attachments only; null / absent for other kinds
```

**`summary_preview` rule (one implementation, on the server):** take the stored summary; drop a leading
label line such as `**ملخص المستند:**`; take the first non-empty paragraph; remove markdown marks
(`**`, `#`, list bullets, backticks); collapse whitespace; cut at 280 chars on a word boundary and add
«…» when cut.

**Sections rule:** rows of `attachment_segments` in `seq` order, minus `value IN ('boilerplate',
'repeat')`. `document_index` comes from `attachment_segment_maps.documents_in_file` (paragraph ranges
`from_p`–`to_p`) matched against the section's `from_p`; when the map lists fewer than 2 documents,
`documents` is `[]` and every `document_index` is `null`. A map whose `content_chars` differs from the
current text length is stale → `sections: null`.

**Identical files (O6):** rows with the same `metadata.filename`, `metadata.file_size_bytes` and
`metadata.mime_type` are one file. The row shown is the newest copy; `copies` counts the group.

---

## 12. Build log — 2026-11-19

**Where:** worktree `C:\Programming\LUNA_AI_my_files` (`feat/my-files`, from `origin/master` @ `1098ce4`).
Uncommitted. The worktree has its own `node_modules` (`npm ci`) and local copies of `.env` and
`frontend/.env.local` (both git-ignored).

### Built

**Backend (Phase 1 + 1b)** — `backend/app/services/files_service.py`, `backend/app/api/files.py`
(`GET /files`, `GET /files/{item_id}`, `DELETE /files/{item_id}`, `POST /files/match`), the §11 models
in `backend/app/models/responses.py` / `requests.py`, `summary_preview` on `WorkspaceItemResponse`
(attachments only, never on a locked item), flags `MY_FILES_ENABLED` / `MY_FILES_USERS` in
`shared/config.py` (default off → every `/files` route 404s), router registered in `backend/app/main.py`,
read-only tool `scripts/my_files_preview.py`. Routes answer `Cache-Control: private, no-store`.
`CROSS_CONVERSATION_MATCH = False` is the single switch Phase 3 opens.

**Web (Phase 1)** — contract `frontend/lib/contracts/files.ts` (+ tests), `filesApi` in
`frontend/lib/api.ts`, `frontend/hooks/use-files.ts`, components in `frontend/components/files/`
(`FileCard`, `FileTypePill`, `FileViewer`, `FileSections`, `FilesTab`, `PaneFileList`, `FileMatchDialog`,
`LibraryFilePage`), the «ملفاتي» tab in `ChatsPage`, route `/chats/files/[itemId]`, pane tabs and the
sectioned viewer in `WorkspacePane`, flag `MY_FILES_ENABLED` in `frontend/lib/features.ts`
(`NEXT_PUBLIC_MY_FILES_ENABLED`, build-time). With the flag off the app is unchanged (checked in a
browser). `FileMatchDialog` exists but is NOT wired into the composer yet.

**Preview harness (dev only)** — `frontend/app/dev-preview/my-files/`: renders the real `ChatsPage`,
`LibraryFilePage`, `WorkspacePane`, `FileMatchDialog` and `FileCard`, with `window.fetch` answered from
`MY_FILES_FIXTURE_DIR` (a folder outside the repo). 404 outside `next dev`. For it, `AuthGuard` lets
`/dev-preview` through in development only (`DEV_PUBLIC_PREFIXES`, empty in a production build).

### Verified

- Backend: 56 new tests pass (`backend/tests/test_files_service.py`); the agent reports the CI backend
  whitelist (1,317 tests) still green.
- The backend's real service code, run read-only on prod for the largest library (169 attachments →
  120 rows after folding 29 copies), returned exactly what an independent SQL dump produced: same ids,
  order, type counts, previews, page counts, text, summaries and sections for all 120 files.
- Web: `tsc --noEmit` clean, 18 new contract tests pass, lint adds no warning; every preview screen
  opened in headless Chromium in light, dark and phone width.
- Type backfill, dry (nothing stored, no ledger row): 92 of that user's 94 untyped files got a type;
  2 are under the summarizer's 300-char minimum and stay untyped. 486k tokens in, 113k out.
- Segmenter preview (nothing stored): 38 of 38 long files mapped (seg-v5.1), 1,110 sections, 690 shown
  after hiding letterheads and repeats, 8 multi-document files, $0.13.

### How to look at it

Start, from `C:\Programming\LUNA_AI_my_files\frontend`, in its own terminal window:
`set "MY_FILES_FIXTURE_DIR=<fixture folder>" && set "NEXT_PUBLIC_MY_FILES_ENABLED=true" && npx next dev -H 127.0.0.1 -p 3100`
then open `http://localhost:3100/dev-preview/my-files`. Screens (`?screen=`): `library`, `library-3`,
`library-empty`, `file-sections`, `file-plain`, `file-stress` (generated, 318 sections), `pane`, `match`,
`states`. The fixture folder holds a real user's documents and must stay outside the repo; regenerate it
with `scripts/my_files_preview.py --user … --out … --pane-conversation …`.

### Differences from §3 as built (owner to accept or change)

- «أخرى» (untyped) is always the last group and the last chip, not "largest first".
- The grouping rule is applied to what is on screen after search and the chip filter; chip counts are
  for the whole library; the chip row hides when the user has one type; `other` gets no pill.
- In the pane the viewer leaves out the title (the pane header already shows it with the WI number).
- The approval popup: closing it means «رفع نسخة جديدة» for every file; the last step is «تأكيد».
- OCR image placeholders are dropped from the full-text view too; «نسخ النص» copies the stored text.
- New Arabic copy written at build, shown together on the `states` screen: the singular type pills
  («مراسلة» for chat), «إضافة ملف», the empty-state hint, the delete confirmation, «لا يوجد ملخص بعد»,
  the popup title «ملف موجود في ملفاتي», «يُستخدم النص المستخرج سابقًا، دون استخراج جديد», «تأكيد»,
  «ابحث في ملفاتي…».

### Left before this can ship

1. Owner approval of the look and the copy above.
2. **Feature-off answer.** The web switch is build-time; the backend switch can be per user. With the
   web on and the backend limited to some users, others get the 404 «هذه الميزة غير متاحة» and the
   «ملفاتي» tab shows an error with a retry. The web must treat that answer as "feature off" (hide the
   tab), or both switches must be opened for everyone together.
3. Wire `FileMatchDialog` into the composer's attach flow (Phase 1b, web half).
4. A file with no `ocr_status` is one still waiting for its message, not one being extracted: the card
   must say so instead of «جارٍ استخراج النص…».
5. §11 has no "original purged" field: the viewer shows the retention line whenever
   `original_available` is false. Add `original_purged` if another reason can occur.
6. Opening a file in the pane downloads its text twice (`/workspace/{id}` and `/files/{id}`).
7. `hide_library_file` rewrites the whole `metadata` value; an OCR or summary write finishing at the same
   moment on a copy can drop the stamp. A clean fix needs the own-table step (Phase 2).
8. `backend/tests/test_files_service.py` is git-ignored by `backend/tests/*`; it needs a whitelist line
   in `.gitignore` and in the CI list at commit time.
9. Decide whether the dev-only harness (and the `AuthGuard` development exemption) is committed or
   stripped before merge.
10. The type backfill on prod (§6) — dry-run, then the owner's go. Without it most libraries show almost
    everything under «أخرى» (the test user: 94 of 120).
11. @integration-lead, @security-reviewer before deploy; production `npm run build` could not finish
    locally (prerendering `/compliance` needs the backend) — verify in CI.

### Rework after the owner's review — 2026-11-19 (same day)

The first build put the library in a tab inside `/chats`. Corrected (D4, D13, §3.4):

- **Sidebar** (`frontend/components/sidebar/Sidebar.tsx`): «المحادثات» · «الملفات» · «القوالب» ·
  «المدونات» · «المكتبة», all behind the flag; flag off = today's nav. `/chats` is identical to
  `origin/master` again; `/chats/files/…` and `FilesTab.tsx` are gone.
- **Pages** `frontend/app/files/` (`/files`, `/files/[itemId]`) in the same sidebar shell as the
  templates and blogs pages; `components/files/FilesPage.tsx`, `FileDocCard.tsx` (the document tile),
  `file-type-style.tsx` (14 type icons, 5 tint families from existing tokens).
- **Copy**: the section is «الملفات» everywhere it is named; the popup title is «ملف موجود ضمن ملفاتك».
- A file not sent yet says «لم يُستخرج النص بعد — يُستخرج عند إرسال الرسالة» (item 4 of the list above: done).
- New production-code hooks added only for the preview: `components/shell/app-nav-override.tsx` (a
  context the app never sets; lets the preview keep sidebar clicks inside itself). `/files` was added as
  a re-login return target in `lib/safe-next.ts`, only while the flag is on.
- As built, differing from the brief: `/files` scrolls natively (inside the Radix ScrollArea the type
  chips stretched the page on a phone); on phones the page header gets start padding so the sidebar's
  menu button does not cover it (the templates and blogs pages have that overlap today); an untyped
  file shows its format («PDF» / «صورة») where the type label would be; in dark mode the correspondence
  tint comes out grey.
- Verified: `tsc` clean, 96 contract tests pass, 26 screen / theme / width combinations opened in
  headless Chromium with no error. NOT verified: the flag-off behaviour of `/files` at runtime in a
  production build (in dev a nested `notFound()` answers the not-found page with status 200).
- Preview screens now: `files`, `files-3`, `files-empty`, `file-sections`, `file-plain`, `file-stress`,
  `chats`, `sidebar-compare`, `pane`, `match`, `states`.
- Open with the owner: whether «قوالبي / مدوناتي / مكتبتي» are renamed beyond the sidebar rows (about 40
  web files mention them, plus the mobile app); new copy «الملفات التي ترسلها في محادثاتك تُجمع هنا.»,
  «مقسّم إلى أقسام».

---

## 13. Phase 2 — spec and build (2026-11-19)

**The build spec is its own file: `.claude/plans/my_files_phase2.md`** (inventory of every call site,
full SQL for the migrations, code design, lifecycle, verification, rollout). It supersedes §4 where they
differ. What changed against §4:

- **Sync by database triggers, not application double-write.** After the backfill, a write to an
  attachment's conversation item reaches the file, and a write to the file reaches every conversation
  item, in the same transaction. So no code/migration ordering can leave the two tables different.
- **Ids stay equal for existing files** (`file_id = item_id`), so pages, maps and helper runs need no
  data rewrite: they keep the column `item_id` and only their foreign key is re-pointed (migration 222).
- **`user_files.metadata` stays the complete blob**; `filename`, `mime_type`, `file_size_bytes`,
  `doc_type`, `ocr_status`, `upload_status`, `content_chars`, `word_count` are generated columns.
  `in_library` became `library_hidden_at`.
- **Small fields stay readable on the conversation item** (title, summary, metadata, storage path), kept
  in sync by trigger; only the extracted text leaves the item, and only in the later contract step. The
  17 agent queries that list a conversation's items never change.
- **Migrations:** 220 (table, inert) · 221 (link column, copy of all 875 rows, triggers, purge function,
  self-checks) · 222 (re-point pages / maps / helper runs) · 223 (contract, Phase 4). 219 and 219b on
  master are the trial-limits migrations, so the unmerged seg-v5.2 work must renumber.
- **Flags:** `USER_FILES_READ_ENABLED` / `_USERS` and `USER_FILES_WRITE_ENABLED` / `_USERS`, both off;
  they also read as off until the database serves the new table.
- **O4 moves to Phase 3:** files surviving a conversation delete changes what users see, so it does not
  belong in Phase 2. Phase 2 changes nothing a user can see.
- **«حذف من الملفات»** becomes one atomic statement on the file (closes §12 item 7).

**Build in progress (uncommitted, same worktree, nothing applied anywhere):**
- Migrations 220–222 written and tested on a LOCAL Postgres built from a structure-only extract of prod
  (no row data) with invented rows. Never run against production by the build.
- Python: `shared/files/` (flags + loader/writer), the reader and writer call sites from the inventory,
  the file path in `files_service.py`, `scripts/user_files_parity.py`, tests.

**Gates that need the owner:** applying 220 / 221 / 222 to production (each in a quiet window, dry run
first), turning each flag on (test account, then everyone), and the three product questions in the
spec's §H (a file removed from one conversation's pane; when files start surviving a conversation
delete; the «صلة المستند بالمحادثة» part of the summary).

### Phase 2 build result — 2026-11-19

Built, uncommitted, in `C:\Programming\LUNA_AI_my_files`. Nothing applied to production (checked live:
no `user_files` table, no `workspace_items.file_id`). All flags off.

**Migrations** `shared/db/migrations/220_user_files.sql`, `221_user_files_backfill_and_sync.sql`,
`222_user_files_dependents.sql`. Tested on a local PostgreSQL 17.6 (same version as prod) built from a
structure-only extract of prod and seeded with invented rows at the live counts (109 users, 3,566 items,
875 attachments, 805 pages, 80 maps / 2,682 sections, 80 helper runs, 599 + 34 message links), roles
mirrored from Supabase (non-superuser owner, `service_role` through `authenticator`). **101 of 101
checks pass**, re-run from zero by a second person. Harness: session scratchpad `phase2_dbtest`
(`node run_all.js --fresh`, `README.md`, `results.txt`, `rollback/rollback_22{0,1,2}.sql`).

What the local test changed in the spec's SQL (the files in the repo are the source of truth now):
- The step that makes writers wait went from about 5.3 s to about 0.13 s on prod-sized data (the draft
  recomputed word counts for every existing row and compared full texts under the lock). The full
  byte-for-byte text comparison moved to a read-only fourth transaction. Readers are never blocked
  except for about 2 ms when the link column is added.
- The loop guard is a transaction-local flag (`luna.user_files_sync`), not `pg_trigger_depth()`: the
  depth guard silently skipped an attachment write made from inside another trigger.
- Triggers are created with `CREATE OR REPLACE TRIGGER`: dropping an existing trigger takes a full lock.
- `user_file_patch` is strict: only `title, content_md, summary, summary_source_length,
  summary_updated_at, storage_path, deleted_at` may be passed; anything else raises. `metadata` must be
  a JSON object.
- A metadata-only write no longer re-copies a large text between the two tables.
- Each rollback refuses to run out of order; 222 → 221 → 220 leaves `workspace_items` byte-identical.
- The self-test insert in 221 cannot fail or leave anything behind (cap trigger returns for attachments,
  the webhook trigger's condition is false because the test rows carry a summary, pg_net's queue is
  transactional); it skips itself if an unknown INSERT trigger appears.

Measured locally: 220 ≈ 50 ms · 221: link column 2 ms (full block), copy ≈ 2.9 s (blocks nobody),
catch-up + triggers ≈ 0.15 s (writers wait), verification ≈ 0.2 s (read-only) · 222 ≈ 15 ms + 3 ms.
Prod `authenticator` has `lock_timeout=8s`, `statement_timeout=30s`, so waiting writers do not error.

**Python** — `shared/files/` (`flags.py`, `store.py`), four flags in `shared/config.py`, the S2 reader
sites and S3 writer sites of the spec's inventory, the file path in `files_service.py` (+ `file_id` on
the library models, omitted when unset), `scripts/user_files_parity.py`, `scripts/nuke_account.py`.
294 new tests pass; the agent reports the CI durable list green (3,133). With both flags off the
switch-off path issues today's exact statements (pinned by tests).

**Findings to act on**
1. **Phase 3 prerequisite, proven:** soft-deleting a file's first conversation item soft-deletes the
   file even if a second conversation still uses it. The item → file mirror must be removed (write flag
   on for everyone, legacy branch deleted) before any second pointer exists.
2. A hard delete of one attachment item leaves its file behind as an orphan (only purge or the user
   cascade removes it). Nothing hard-deletes single attachment items today; keep it that way.
3. **Pre-existing bug, not ours:** `purge_user_data` deletes `workspace_items` and `conversations`
   before `task_state`, whose foreign keys to both are NO ACTION — account deletion fails for any user
   with `task_state` rows. Prod has 25 such rows for 2 users. 221 keeps the live body plus one line; the
   fix (delete `task_state` first) needs the owner's OK to ride along.
4. With a flag limited to an allow-list, five background readers that have no user stay on the old path
   (`resummarize_dirty_items`, the segmenter's `_load_item` / `_load_items` / `_item_row`,
   `has_unread_attachments`); they switch only when the flag is on for everyone.
5. `delete_workspace_item` was left as is: the trigger carries the delete to the file.
6. Cost with flags on: one extra read per load, two requests per write; a metadata patch on a 2.6 MB
   text costs about 250 ms because the generated `word_count` is recomputed.
7. Not verifiable locally, to check in the prod dry run: the database API's schema reload and the
   `user_files` → `workspace_items` embed, real default privileges, TEMP privilege for `postgres`,
   Supabase's DDL event triggers, timings under real traffic.
8. Tests under `shared/files/tests/` and `backend/tests/test_files_service.py` are git-ignored; both need
   whitelist lines in `.gitignore` and in the CI list, together.
9. The nightly parity job from the spec is not built (the script is).

**Production procedure** is in the headers of the three files and in the spec's §G: dry run then apply
220; capture agent digests; dry run then apply 221 in a quiet window; probes + one manual upload +
digests again; deploy code with flags off; read flag (test account → everyone); write flag (same);
then 222. Rollback: flags off, redeploy, rollbacks 222 → 221 → 220.

### Production and git log — 2026-11-19 (owner go on each)

- **Type backfill APPLIED on prod.** `scripts/my_files_type_backfill.py --apply --yes --from-json` wrote
  exactly the labels of the reviewed dry run (no new model call): 244 of 244 rows got the planned
  `metadata.doc_type`; titles, summaries, text and every other metadata key unchanged (hash check before
  and after); live attachments with text and no type: 244 → 0; typed: 397 → 641 of 836. Each write moved
  that row's `updated_at`.
- **Migration 220 APPLIED on prod** after a clean dry run (self-check `220 OK`, 0.15 s): `user_files`
  exists, 0 rows, RLS on, no client grants, 8 generated columns, RPCs service-only, served by the
  database API. Nothing reads or writes it; `workspace_items.file_id` does not exist yet, so the
  `USER_FILES_*` flags still read as off whatever their value.
- **Local commits on `feat/my-files` (not pushed):** `0eeac93` Phase 1; `e24e22f` the dev-only preview
  page with the `AuthGuard` development exemption, kept separate so it can be dropped before merge.
  `403c81e` Phase 2 (code, migrations 220-222, spec), committed after the account-deletion fix landed in
  221 and the local suite passed 108 of 108 from zero. The branch has no upstream set, so a plain
  push cannot land on master by accident.
- **Owner approved** fixing the `purge_user_data` / `task_state` order inside 221 (done by a second
  agent pass, re-tested locally).

**Next gates:** 221 on prod (quiet window; dry run first; capture agent digests before and after), then
code deploy with flags off, then the read flag, the write flag, and 222.

### Finishing Phase 1 for release — 2026-11-20

- **Per-user gate (closes §12 "Left" item 2).** New route `GET /api/v1/files/status` →
  `200 { "enabled": boolean }` for any signed-in user (never 404, reads no file data). The web shows the
  «الملفات» row, the page, the pane tabs and the same-file popup only when the build flag is on AND this
  route answers `enabled: true`; while loading or on error the app looks as it does today. A staged
  rollout (`MY_FILES_USERS` = the test account) therefore shows nothing to other users.
  Contract addition to §11: `interface FilesStatusResponse { enabled: boolean }`.
- **Same-file popup wired into the attach flow (Phase 1b, web half; closes item 3).** On a file pick in
  an existing conversation the composer asks `POST /files/match`; the upload is never delayed; a match
  opens the approval popup; «استخدام الموجود» cancels (or removes) the new upload and attaches the
  existing item through the composer's existing attach-an-item path; «رفع نسخة جديدة», closing, or
  sending without answering keeps today's behaviour.
- **Built, verified, committed locally (not pushed):** `d29d11d` (status route + per-user gate + popup
  wired into «+» → مرفق and the pane's «إضافة ملف») and `7f97082` (preview: a `composer` screen that runs
  the real composer and pane against a stubbed API, and `?status=off`). `tsc` clean, 24 contract tests,
  295 backend tests. The composer has no paste or drag-and-drop upload today, so there was nothing else
  to wire. With the build flag on and the status answering off, the app shows today's sidebar and pane
  and makes no `/files` call other than the status.
- Noticed, not changed: image thumbnails on the composer's attachment chip are blocked by the page's
  content security policy (`img-src` has no `blob:`) — seen in the preview with the app's own policy;
  to confirm on the live site.
- Remaining "Left before this can ship" items after this: 5 (`original_purged` field), 6 (text
  downloaded twice in the pane), 9 (keep or drop the preview commits), 11 (reviews + production build
  in CI). Items 2, 3, 4, 7, 8 and 10 are done.

### «ننصح بإعادة إرفاق هذا الملف» — owner 2026-11-20

Until 2026-11-16 text extraction silently stopped at the first 30 pages of a file. Owner: tell the user
to redo it, "something like ننصح بإعادة إرفاقها", with no details, and never show the same-file popup
for such a file.

- **Who is affected (prod, 2026-11-20):** 19 files of 8 users, all read to exactly page 30 of files up
  to 303 pages long; 1,251 pages were never read. First 2026-10-30, last 2026-11-16. No extracted file
  is in the unknowable state (30 pages read, page count missing).
- **Rule (one function, `files_service.reattach_advised`):** extraction finished, fewer pages read than
  the file has, and no budget marker (`ocr_partial`). A file cut by the page budget is excluded — it
  already carries its own notice.
- **API:** `LibraryFile.reattach_advised: boolean` (list and detail, both storage paths) and
  `WorkspaceItem.reattach_advised` on attachments, present only when true.
- **UI:** the line «ننصح بإعادة إرفاق هذا الملف», no reason given — a tag on the «الملفات» card and on the
  pane's file card, a line on the opened file, and (NOT behind the «الملفات» flag) a banner on the old
  attachment view and a tag on the old pane card, so it reaches users whatever the rollout state.
- **Same-file popup:** such a copy was already excluded from the match (`_extraction_complete`), now
  pinned by a test together with the advice. A re-attached copy is extracted afresh; in «الملفات» the
  newest copy is the one shown, so the advice disappears from the library card once the file is
  re-attached (it stays on the old item inside its conversation).
- Mobile app: not done (the mobile attachment view does not read the new field yet).

### Rebase, renumbering, and the section owner — 2026-11-20

- **Rebased onto master `642cb6a`** (seg-v5.2 and the artifact editor change), no conflicts; 631
  segmenter / delivery / helper / file tests pass on the rebased branch. A backup tag of the old tip
  exists locally: `backup/my-files-before-rebase`.
- **Migrations renumbered** because master took 220 (`220_attachment_segment_owner.sql`):
  `221_user_files.sql` (APPLIED on prod 2026-11-19 under its old number 220; SQL unchanged),
  `222_user_files_backfill_and_sync.sql`, `223_user_files_dependents.sql`; the contract step is 224.
  **Everywhere this plan and `my_files_phase2.md` say 220 / 221 / 222 / 223 for the files migrations,
  read 221 / 222 / 223 / 224.** The local migration test passes 108 of 108 with the new numbers.
- **Section owner (owner 2026-11-20: "make sure to have its place in the frontend").** seg-v5.2 is live
  on prod (3 maps, 63 sections with an owner, 4 with `brought_by`). `GET /files/{id}` now returns
  `owner_class`, `owner`, `brought_by` per section (null on older maps and for `unknown`). In the opened
  file each section shows, under its title, a tag with the document's own role words («المدعى عليه
  الثاني», «الخبير»), tinted by side (claimant side · respondent side · whoever decides or issues ·
  everyone else), and «قدّمه: …» when the map names who filed it. A filter by owner appears above the
  sections when the file has two or more owner classes. «بيانات المستند» sections keep only their own
  tag. Arabic names of the 20 owner classes are first drafts in `lib/contracts/files.ts`
  (`SECTION_OWNER_CLASS_LABELS`) for the owner to approve.
- Not verified on real documents here: the local model keys no longer work (see below), so the visual
  check used the preview's generated file with invented owners. The 3 real seg-v5.2 maps on prod belong
  to other users.
- **Seen while testing, unrelated to this work:** the local Baseten key answers HTTP 402 ("check your
  current payment status"). On prod, since 2026-11-20 11:02 Riyadh, the tier_2 flash agents (memory
  writer, summarizer, responder, style profiler, compactor) are being served by the Alibaba fallback
  (`deepseek-v4-flash-0731`, 38 calls by 14:30) instead of Baseten. Reported to the owner.

### Pre-deploy reviews — 2026-11-20

**Readiness checks on the final branch:** the exact CI list passes locally (3,475 backend and agent
tests); web `tsc` clean, lint adds no warning, 105 tests; mobile `tsc` clean against the changed shared
types. Branch up to date with master.

**@integration-lead (contracts):** no break for shipped mobile builds, nothing that fails at runtime.
Fixed: a separate `FEATURE_OFF` error code and the web turning the feature off on the first such
answer (was: sidebar row and an error page with a retry that never works, when the feature is turned
off for a user with an open tab); `original_purged` so the retention line shows only when true;
«مقسّم إلى أقسام» on a card now agrees with what the file opens as; header count from the library total;
`file_id` in the web contract; own-key lookups for owner classes; the old «ملفاتي» wording in two error
texts. Left: two chips when the same existing file is picked twice in one message (cosmetic; after a
reload it is one); the mobile app does not show the re-attach advice.

**@security-reviewer:** no critical or high finding in the feature; no way for one user to reach
another's file, section, summary or file name; migrations, grants, RLS and account purge found sound;
the dev preview is inert in a production build. Fixed: the preview's data route answers the local
machine only and the server is run with `-H 127.0.0.1` (it had been listening on all interfaces while
serving a real user's documents — corrected the moment it was reported); bounds on `/files/match`
inputs; images stripped from the rendered summary; the parity script's session is really read-only;
`NEXT_PUBLIC_MY_FILES_ENABLED` declared as a Docker build argument (without it the UI could never be
turned on from Railway).

**Open, for the owner:**
1. **Logfire records every route's arguments** (`shared/observability.py`, default FastAPI
   instrumentation): the file name sent to `/files/match`, and — outside this feature — the full text of
   every chat message and upload file names. Export is failing today (token 401), so nothing flows yet;
   it starts the moment the token is fixed. Fix: a `request_attributes_mapper` that drops the values.
2. The preview fixture is a real user's documents on local disk (session scratchpad): delete after the
   review, or rebuild it from a demo account.
3. `/files/{id}` gets its own rate-limit bucket per id (as `/workspace/{id}` already does): cost only.
4. Runbook rule for 222: turn no `USER_FILES_*` flag on unless the run printed both `222 OK` lines (a
   half-applied 222 would pass the code's readiness probe).
5. Defence in depth for 223: a composite foreign key (id + owner) from maps and pages to the file.
6. With every flag off, `summary_preview` and `reattach_advised` are still added to attachments on the
   conversation-items response (the owner's own data).
7. The preview's client code is still bundled into a production build (synthetic data only): strip the
   two preview commits before merge if that matters.
