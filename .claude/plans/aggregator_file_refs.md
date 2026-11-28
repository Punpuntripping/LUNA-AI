# Aggregator — citing the user's file sections («أقسام من مستنداتك»)

**Status:** BUILT + REVIEWED, then REWORKED 2026-11-27 (D10, D11: tags use the file's WI number; table modelled on `workspace_item_references`; D12: written `[F3·S7]`, F + that WI number, the ready tag shown on each label). Uncommitted, in worktree `C:\Programming\LUNA_AI_file_refs` (branch `feat/aggregator-file-refs`, moved onto origin/master 454e6c8). Owner: "start building the plan" (accepts D7). Migration **235** written, NOT applied (still free on prod, checked 2026-11-27). Not deployed. Build notes §9. Next: offline run → owner review → commit / apply 235 / deploy with OK.
**Scope v1:** ordinary deep search only (the aggregator). The extended merger comes next, in its own step.
**Rule:** nothing goes live without the owner's OK (test account first).

---

## 1. Goal

Let the deep-search aggregator point at the exact part of the user's own file it took a fact or quote from. The user should see it the way they see a library reference: a small chip in the text that opens the source. Two requirements:

1. **Naming.** A file ref must never be mistaken for a library ref `[n]`, by the user or by any code.
2. **Display.** Reuse the «الخيوط» look: a section number inside a square.

---

## 2. Today (origin/master)

| What | Where | Fact |
|---|---|---|
| Sections reach the aggregator | `agents/utils/attachment_delivery.py:205-246` `render_sections` | Kept sections in document order, each under `〔what — owner، قدّمه …〕`. The section's `seq` is known (`Section.seq`) but **not shown**. |
| Document heading | `agents/deep_search_v4/shared/case_documents.py:154` `_heading` | `### WI-3 · judgment · title`. The planner's `planner_brief` names documents by these `WI-N` aliases. `WI-3` is the file's fixed alias in the conversation (`workspace_items.wi_seq`, unique per conversation, kept after a soft delete). |
| Which documents carry text | `case_documents.py:291` `build_case_document_blocks_segmented` | Core sections (a file over 5k, or a bundle of small files); full text for a short file (≤ 5k, unbundled); full text treated as ONE section when there is no usable map; **summary only** for documents beyond the caps. |
| Prompt | `aggregator/prompts.py:102-110` | Files are facts, «never a `[n]` citation»; name a file in words («الحكم المرفق»), never `WI-N`; quote critical findings exactly. |
| Library refs | `aggregator/postvalidator.py:48` `_CITATION_RE`, `frontend/lib/markdown/remark-citations.ts` | Digits only: `[4]`, `[4,17]`, `[٤]`. |
| Library refs table | `workspace_item_references` (049/050) | `ref_pk`, `wi_id`, `n` (the `[n]` number, `UNIQUE (wi_id, n)`), `ref_id`, `item_id` (the library source), `domain` (CHECK: 7 library corpora), `relevance`, `used`. |
| Chip look | `frontend/components/chat/CitationMarker.tsx` | `[4]`: tinted `bg-primary/10`, brackets. A tap opens the report and scrolls to the reference card. |
| «الخيوط» square | `frontend/components/files/angles/AngleMapDiagram.tsx:138-151` | `min-w-7 rounded-md border bg-card px-1.5 text-xs font-semibold tabular-nums leading-6` + a kind colour; shows `seq`. A tap opens `AngleSectionDialog`. |
| Section popup | `frontend/components/files/angles/AngleSectionDialog.tsx` | Title = `what`, «القسم 7», pages, side, owner, «قدّمه», then the section text. |
| Section text for a file | `GET /files/{item_id}` (`backend/app/api/files.py`) → `FileSection[]` (`frontend/lib/contracts/files.ts:79`) | `seq`, `what`, `page_from/to`, `owner`, `brought_by`, `content_md`. Per user; another user's id gets 404. Serves hidden files too. |
| Chat summary | `agents/memory/artifact_summarizer/prompts.py:72,167` | Writes no `[n]`. Citations live only in the report. |

---

## 3. Owner decisions (2026-11-27)

- **D1:** the chip shows the **section number only**. The document is named in the popup.
- **D2:** **neutral** colour (no side colours, no angle-kind colours).
- **D3:** **rely on the aggregator.** No code check of quotes, and code never moves, removes or rewrites a file ref the aggregator wrote.
- **D4:** **ordinary deep search first**, then the extended merger.
- **D5 (Q1):** **option A, no change to the aggregator's output format.** "We don't want to add work to the aggregator." The tags live inside `synthesis_md`; code reads them from the text.
- **D6 (Q2):** **option T, a new table** `workspace_item_file_refs` (migration + RLS).
- **D7 (Q3), accepted 2026-11-27 ("start building the plan"):** the owner asked how a file that is no longer available could still be shown. Answer (§4.5): store **where** the section sits in the file's text, not a copy. None of our cleanups removes that text while its conversation exists, so the popup can always cut the section back out.
- **D8 (Q4):** a whole-document ref `[F2]` = a file icon in the same square. **Summary-only documents cannot be cited.**
- **D9 (Q5):** **yes** to the «من مستنداتك» list under «المراجع».
- **D10 (after the first build):** "it's important to see who links to what, so I would recommend other table that inspire from workspace_item_Ref". Keep the separate table, modelled on `workspace_item_references`: `ref_pk`, `wi_id` (the report — who links), `n`, `ref_id`, `item_id` (the attachment — what it links to), plus `wi_seq` and `section_seq`. The WI refs table itself stays library-only (its domain CHECK, `n` = the `[n]` number, and its many readers).
- **D11 (after the first build):** "I think you don't understand the WI logic refs." The tag names the file by its **fixed WI number in the conversation**, the same `WI-3` the aggregator already sees in the heading and the planner and writer already use. The per-answer D numbering is gone.
- **D12 (2026-11-27, owner "go"):** the tag is written **`[F3·S7]` / `[F3]`: `F` + the file's WI number** (WI-3 → F3), not a separate file count. Each section's label carries its ready tag, `〔F3·S7 · …〕`, and a document shown whole gets one `〔F3〕` line over its text, so the aggregator copies the tag rather than combining two numbers. The "never write `WI-N`" rule is absolute again (no exception inside tags), and `F` can only mean a file (WI numbers also name reports). A separate count (F1 = the first file) was rejected: a fourth numbering beside WI-N, «المستند n» and «مرفق رقم n», plus a rule for deleted files.

---

## 4. Design

### 4.1 The ref token and its numbers

- The aggregator writes **`[F3·S7]`** = section 7 of the conversation's file WI-3, and **`[F3]`** = WI-3 as a whole (D12).
- The number after `F` = `workspace_items.wi_seq` of the attachment: fixed for the conversation, the same number as in `### WI-3 · …`. No mapping to keep per answer.
- `S` = `attachment_segments.seq`: the **same number** the «الخيوط» squares show for that file.
- Canonical form `F3.S7` / `F3`. Accepted spellings (one grammar, `shared/workspace/file_refs.py`, mirrored exactly in `frontend/lib/contracts/file-refs.ts`): any case, `F3` / `F-3` / `F 3` / `F_3`, the angle map's separators before `S` (`·`, `.`, `-`, a space, `:`, `/`, `_`, `|`, `,`) plus `،`, leading zeros, Arabic-Indic digits, and several refs in one bracket (`[F3·S7، F3·S9]`). The old D and WI forms are never matched.
- **No overlap with `[n]`:** the letters mean `_CITATION_RE`, `remark-citations.ts` and `artifact_builder`'s cited-count regex never match it. Library citation checks are untouched.
- **Which refs are possible:**
  - `[F·S]` only for a section the aggregator was shown (core, kept, possibly trimmed).
  - `[F]` for a document shown as full text with no sections (short file, or no usable map).
  - **Never** for a document the aggregator saw only as a summary (D8), and never for a document with no WI number. Code resolves only the documents shown with text, so such a tag gets no row and is hidden.
- One tag per section. Several sections side by side: `[F3·S7][F3·S9]`. A file tag and a library number never share a bracket.

### 4.2 What the aggregator sees (aggregator only)

The expanders' input stays byte-identical. For the aggregator recipient only:

- Heading: unchanged, `### WI-3 · judgment · title`. The tag's number is the document's WI number.
- Section label: opens with the ready tag, `〔F3·S7 · what — owner، قدّمه …〕` (D12).
- A document shown whole (no sections) gets ONE label line over its text, `〔F3〕`; its ref is `[F3]`.
- A summary-only document gets no label and is not resolvable (D8).
- Pinned official articles (`〔من المكتبة النظامية، لا من المستند — …〕`, `ATTACHMENT_PIN_DELIVERY_ENABLED`) are library text printed under a section, not the user's words. They get no tag and must never carry a file tag.

Implementation: a `file_wi: int | None` parameter on `render_sections` (the document's WI number → each kept section's label opens with `F{wi}·S{seq}`), a `file_tag` on the full-text body (the `〔F3〕` line); the builders take a `file_ref_docs` list, passed only for the aggregator recipient from `_build_case_document_lists` (`deep_search_v4/orchestrator.py`), never under «بحث موسّع». Each document shown with text and a WI number is appended as `FileRefDoc(wi_seq, item_id, sections=((seq, char_start, char_end), …))`; the list rides `FullLoopDeps.case_document_file_refs` → `AggregatorInput.file_ref_docs`.

### 4.3 Prompt (aggregator, behind the flag)

These rules are inserted into the `case_document` bullets of `_SHARED_ROLE_AR` (and a line after the binding citation rules) only when the turn listed at least one document. With the flag off, the prompt is byte-identical to today.

- After a sentence or quote that rests on the user's file, **copy the tag shown on the label** of the part it came from, in square brackets: `«لم يقدّم المدعى عليه ما يثبت السداد» [F3·S7]`, or `[F4]` for a document shown whole. Copy, never build; only tags that appear on a label. Cite the section that holds the words.
- **`WI-N` is never written anywhere** (D12 removed the v2 exception for tags). The existing rule stays: name the document in words («أسباب الحكم المرفق»).
- File tags are for the user's documents only. Library rules still come from `<references>` with `[n]`. A file is never a legal authority.
- Never tag a document given only as a summary; never put a file tag on pinned library text, on the summary, or on `<conversation_memory>`.
- Quote exactly (unchanged rule).
- `FILE_REFS_PROMPT_VERSION = "file-refs-v3"` (recorded on the aggregator span).

### 4.4 Output format: unchanged (D5, option A)

`AggregatorLLMOutput` (`models.py:213`) keeps `synthesis_md`, `used_refs`, `gaps` and `confidence` as they are, including the `synthesis_md` description. The aggregator's only new job is writing the tags in its text.

After the run, code reads the tags from `synthesis_md` (the shared grammar) and builds `AggregatorOutput.file_refs` (code-side only, excluded from every dump): one `FileRef(n, ref_id, item_id, wi_seq, section_seq, char_start, char_end)` per distinct ref that resolves to a delivered document/section, in order of first mention. Unresolved tags: see §4.6.

Consequence of A: code doesn't know which «…» belongs to which chip, so **the popup opens the section at its top, with no highlight.** (Option C would have added that; rejected to keep work off the aggregator.)

### 4.5 Storage: new table (D6, D10) and the "file no longer available" answer (D7)

Migration `235_workspace_item_file_refs.sql` (235 free on prod and in every worktree, checked 2026-11-27):

```sql
CREATE TABLE public.workspace_item_file_refs (
  ref_pk       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  wi_id        uuid NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE, -- the report: who links
  n            int  NOT NULL CHECK (n > 0),          -- order of first mention (the «من مستنداتك» list order)
  ref_id       text NOT NULL CHECK (ref_id ~ '^F[1-9][0-9]*(\.S[1-9][0-9]*)?$'),  -- 'F3.S7' | 'F3' (3 = wi_seq)
  item_id      uuid NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE, -- the attachment: what it links to
  wi_seq       int  NOT NULL CHECK (wi_seq > 0),     -- the attachment's WI number in the conversation
  section_seq  int  CHECK (section_seq IS NULL OR section_seq > 0),  -- NULL = whole document
  char_start   int,                                  -- where the section sits in the file's text
  char_end     int,
  page_from    int,
  page_to      int,
  doc_title    text NOT NULL DEFAULT '',
  what         text NOT NULL DEFAULT '',
  owner        text NOT NULL DEFAULT '',
  brought_by   text NOT NULL DEFAULT '',
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT workspace_item_file_refs_wi_id_n_key UNIQUE (wi_id, n),
  CONSTRAINT workspace_item_file_refs_wi_id_ref_id_key UNIQUE (wi_id, ref_id)
);
CREATE INDEX idx_workspace_item_file_refs_item ON public.workspace_item_file_refs (item_id);  -- which reports cite this file
-- RLS ON + the four owner policies of 049 (owner through the REPORT); REVOKE ALL from
-- anon/authenticated, GRANT DML to service_role (the 195 stance); self-test block.
```

`workspace_item_references` stays library-only. It is keyed by `n` (the `[n]` number), its domain CHECK lists the 7 library corpora, and its `/references/{n}/source` route stays as it is.

**Why the section can always be shown (D7).** We store the section's **position in the file's text** (`char_start`/`char_end`, the same offsets `attachment_segments` holds into the attachment's text), not a copy of the text. The file's text isn't lost in any of these cases:

- **«حذف من ملفاتي»** only stamps `library_hidden_at` (`files.py`: "a stamp, never a delete"). The text stays.
- **The daily retention sweep** purges the original PDF but keeps the row and its text (`attachment_cleanup.py`, fixed 2026-08-10).
- **Re-segmenting** (a new map, as on 2026-11-20) can renumber sections, but never touches the text. The stored positions still cut out the same passage the answer cited, even if «الخيوط» now numbers it differently.
- **Deleting the conversation** deletes the report too, so there is no chip left to tap.

How the popup reads it: a new route on the report, not the «الملفات» route, so it doesn't depend on the file still being listed there. If `attachment_segments` still has that `seq` at the same positions, the route returns that row's stored text (a seg-v5.2 page-break join drops the letterhead between its halves). Otherwise, or when that row's text is empty, it returns the slice of the file's text at the stored positions. The text is read through the same `shared.files` overlay the segmenter uses (`attachment_segmenter.runner._load_items`), so a file attached from «الملفات» reads its file's text. Only an attachment the user deleted on purpose (`workspace_items.deleted_at` set) gets «لم يعد هذا الملف متاحًا».

**The user's text fixes (master 9aa2e80, migration 234).** The attachment agent never rewrites `content_md`; fixes are an overlay (`shared/attachments/text_corrections.py`, `shared/attachments/overlay.py`) applied to the section text when it is loaded, and positions stay on the ORIGINAL text. So the stored `char_start`/`char_end` stay valid, and the section route applies the same active fixes to the text it serves (as `attachment_sections.wait_and_load_sections` does), so the popup shows what the aggregator read.

### 4.6 Display (web)

**Data:** two new routes beside the references routes, owner-checked through the report:
- `GET /workspace/{item_id}/file-refs`: the report's rows, for the chips and the list.
- `GET /workspace/{item_id}/file-refs/{ref_id}/section`: the popup's header fields plus the text (§4.5).

Hook `useWorkspaceItemFileRefs(itemId)`, shaped like `use-workspace-item-references`.

**Contract (fixed 2026-11-27, D10/D11 field names, D12 ref form; backend and web build against it):**

```
GET /workspace/{item_id}/file-refs
  200 { "file_refs": [ FileRefRow, … ] }      ordered by n; [] when none
  404 when the item is missing or not the caller's (same check as /references)
  Never gated or metered: citation lists are in the never-gated class, and this is the user's own file.

FileRefRow = {
  "n": 1,                       order of first mention in synthesis_md, 1-based
  "ref_id": "F3.S7" | "F3", canonical form, dot, no brackets (3 = wi_seq)
  "wi_seq": 3,                  the file's WI number in the conversation
  "section_seq": 7 | null,      null = whole document
  "page_from": 3 | null, "page_to": 4 | null,
  "doc_title": "…", "what": "…", "owner": "…", "brought_by": "…",
  "available": true             false = the attachment was deleted on purpose (deleted_at)
}

GET /workspace/{item_id}/file-refs/{ref_id}/section     {ref_id} in any form the parser accepts
  200 FileRefRow fields + { "content_md": "…", "files_item_id": "<uuid>" | null }
      files_item_id = the id /files/{id} («الملفات») opens; null when the file is not listed there
  404 unknown item / not the caller's / unknown ref
  410 { "detail": "لم يعد هذا الملف متاحًا" } when the attachment is deleted
```

**One text form** wherever a tag becomes plain text (unfold, the writer's sources, artifact_analyzer, copy, export): `(القسم 7 من {doc_title})`; a whole document `({doc_title})`. A tag with no row is removed.

**The chip:** a remark plugin `remark-file-refs.ts`, registered beside `remarkCitations`, turns `[F3·S7]` into `<FileRefChip>`. It uses the «الخيوط» square classes with **no colour class**. A whole-document ref `[F3]` shows a small file icon in the same square (D8).

```
… ونصّت أسباب الحكم المرفق على أن «المدعى عليه لم يقدّم ما يثبت السداد» ┌─┐ ، وهذا يخالف المادة 77 [3].
                                                                     │7│
                                                                     └─┘
   file chip: bordered square, number only, no brackets, neutral     library chip: tinted, bracketed
```

**The popup:** tapping the chip opens the `AngleSectionDialog` layout, fed from the section route:

```
┌───────────────────────────────────────────────┐
│ أسباب الحكم                               ✕  │
│ الحكم المرفق · القسم 7 · ص 3–4                │
│ صاحب القسم: المحكمة                            │
│ ┌───────────────────────────────────────────┐ │
│ │ وحيث إن المدعى عليه لم يقدّم ما يثبت السداد │ │
│ │ رغم إمهاله …                                │ │
│ └───────────────────────────────────────────┘ │
│                [ افتح الملف ]                  │
└───────────────────────────────────────────────┘
   opens at the section's top, no highlight (D5)
   «افتح الملف» = the same green «افتح …» button used everywhere (opens the file in «الملفات»);
   hidden when the file is no longer listed there
```

- **Attachment deleted on purpose** (`deleted_at` set): the popup says «لم يعد هذا الملف متاحًا». Every other case still shows the text (§4.5).
- **Unresolved tag** (a WI number not shown with text, a section that doesn't exist, a summary-only document): no row in the table, so no chip is drawn and the tag is hidden. Display only; the stored answer is unchanged.
- **«من مستنداتك» list (D9):** under «المراجع» in the report, one line per cited ref in `n` order: `7 · أسباب الحكم — الحكم المرفق (ص 3–4)`; a whole document shows the file icon and its title. Tapping a line opens the same popup.
- In the report viewer a tag with no row is hidden, also while the rows load. Elsewhere (chat, streaming) a tag shows as the text it was written as.

### 4.7 Everything else that reads the report body

| Reader | File | v1 change |
|---|---|---|
| Chat summary (artifact_summarizer) | `agents/memory/artifact_summarizer/runner.py` | Code removes file tags from the body it reads (it writes no citations anyway). |
| Chat reply (planner's responder) | `deep_search_v4/planner/runner.py`, `planner/prompts.py` | Code removes file tags from the `synthesis_md` the responder reads. |
| Router / writer reading the report through unfold | `agents/tool_repository/unfold_workspace_item.py` | Each tag → its label from the report's `workspace_item_file_refs` rows, «(القسم 7 من الحكم المرفق)», so the writer never copies a tag into a memo (writer exhibits stay «مرفق رقم n»). |
| The writer reading a report as a source | `agents/writer_planner/walkers.py` (`build_analyzed_items_direct`) | The same label form (added in the rework: this path reads `content_md` directly, not through unfold). |
| artifact_analyzer (prior reports) | `agents/memory/artifact_analyzer/context.py` | The same label form, in its own loader. |
| Share / «حفظ كمدونة» snapshot | `backend/app/services/blog_service.insert_post` | Tags stripped when the public copy is frozen (page, snippet, imported copies). |
| Copy | `frontend/lib/contracts/references-copy.ts` | Tag → «(القسم 7 من الحكم المرفق)». |
| PDF / Word export | web `AgentSearchViewer` `copyContent` → `ExportMenu` | The client builds the export markdown, so it sends the same tag-free text as copy. The backend export is untouched. |
| Public share view | `PublicAnswerView`, `BlogArticleView` | Tags dropped. The popup is per-user anyway (another user gets 404). |
| A report attached to a later turn | `agents/orchestrator.py::_load_attached_items` | **Open:** the router can attach a prior report to the planner / simple search / writer context; that snapshot's `content_md` still carries raw tags. One call (`labelled_report_text` for `kind == "agent_search"`) closes it; not done in the rework because `agents/orchestrator.py` was being changed by the resume fix at the same time. |
| Report editor | `agents/artifact_editor/` | Sees the report with its tags; an edit that keeps a tag keeps its chip. No change in v1. |
| Mobile app | `mobile/` | Must at least hide the tags before the app ships to users; chip + popup later. **Not in this build:** mobile is developed on `feat/mobile-phase4b-iap`; the hiding goes there, before the flag reaches users who use the app. |
| Extended merger | `deep_search_v4/extended_merger/` | **Out of v1** (D4). It still forbids marker numbers. |

Post-processors that match `WI-N` and could touch a tag were checked (rework, 2026-11-27): the alias parsers (router, planner, responder, unfold, edit_artifact, writer_planner, attachment_agent, writer publisher) are anchored `^WI-(\d+)$` on tool arguments, never run over report text; `reply_guard` keeps `WI-N` as allowed; the writer's exhibit guard (`agents/writer/exhibits.py`) runs only on writer documents, and the writer never receives a raw tag (unfold + walkers label them). Tags therefore reach the stored report unchanged.

### 4.8 وضع السرية (masking)

The aggregator reads encoded document text. `synthesis_md` is decoded at publish as today; the tags hold no masked values (a WI number is not masked). The table rows are written from the stored rows (`workspace_items` title and `wi_seq`, `attachment_segments` what/owner/brought_by), which hold real text, not from the aggregator's encoded copies. The section route serves the user's own text, exactly as «الملفات» does.

### 4.9 Flag and rollout

- `AGGREGATOR_FILE_REFS_ENABLED` + `AGGREGATOR_FILE_REFS_USERS` (allowlist; empty = everyone). Off = today's input, prompt and output, byte-identical.
- Order: build in a clean worktree off origin/master (the main checkout has another session's uncommitted aggregator v2 diffs) → tests → offline run on 3–4 real mapped files (local only) → owner reviews the answers → apply 235 → deploy in a quiet window → test account → owner checks real answers → everyone.

---

## 5. Questions

All answered 2026-11-27 (D5–D11). The owner reviews the offline answers before any deploy (§4.9).

---

## 6. Files (v1)

Agents/backend:
- `agents/utils/attachment_delivery.py`: `render_sections(numbered=)`
- `agents/deep_search_v4/shared/case_documents.py`: `FileRefDoc` (keyed by `wi_seq`), section numbering for the aggregator list; headings unchanged
- `agents/deep_search_v4/orchestrator.py`: aggregator recipient only, flag, never «بحث موسّع»
- `agents/deep_search_v4/aggregator/prompts.py`: flagged rules, `FILE_REFS_PROMPT_VERSION = "file-refs-v2"`
- `agents/deep_search_v4/aggregator/models.py` / `deps.py` / `agent.py` / `runner.py`: `FileRef` + `AggregatorOutput.file_refs` (code-side only; `AggregatorLLMOutput` unchanged), the prompt switch
- `agents/deep_search_v4/aggregator/file_refs.py`: tags → `FileRef` (resolve WI → document, S → section with positions)
- `agents/agent_search/publisher.py`: writes `workspace_item_file_refs` beside the library refs (best-effort)
- `shared/workspace/file_refs.py`: the one grammar, the text form, the row loader
- `shared/db/migrations/235_workspace_item_file_refs.sql`: table + RLS (§4.5)
- `backend/app/api/workspace.py` + `backend/app/services/file_refs_service.py`: `GET …/file-refs`, `GET …/file-refs/{ref_id}/section`
- `agents/tool_repository/unfold_workspace_item.py`, `agents/writer_planner/walkers.py`, `agents/memory/artifact_analyzer/context.py`: tag → label
- `agents/memory/artifact_summarizer/runner.py`, `agents/deep_search_v4/planner/runner.py` + `prompts.py`: strip tags from their input
- `backend/app/services/blog_service.py`: strip tags from the frozen share snapshot
- `agents/utils/aggregator_file_ref_flags.py`, `shared/config.py`: flag + allowlist

Web:
- `frontend/lib/markdown/remark-file-refs.ts` + `MarkdownRenderer.tsx` registration (`renderFileRef`)
- `frontend/hooks/use-workspace-item-file-refs.ts`
- `frontend/components/workspace/FileRefChip.tsx`, `FileRefSectionDialog.tsx`, `FileRefList.tsx`
- `frontend/lib/contracts/file-refs.ts`: the grammar (mirror of the Python one), `FileRefRow`, text forms
- `AgentSearchViewer.tsx` / `ArtifactPreview.tsx`: chips + the «من مستنداتك» list
- `references-copy.ts`, `PublicAnswerView.tsx`, `BlogArticleView.tsx`, `lib/api.ts`

---

## 7. Tests

- `render_sections(numbered=True)`: S numbers on kept sections only; pins unnumbered; `numbered=False` byte-identical to today.
- Documents keyed by WI number in delivery order; headings unchanged; whole-text, summary-only and no-WI documents.
- Prompt with flag off is byte-identical for every variant; flag on carries the rules once, the WI-only-in-tags rule, and `file-refs-v2`.
- `file_refs` build: tag variants (`WI-1·S7`, `WI1·S7`, `wi1-s07`, Arabic-Indic digits, several refs in one bracket), unresolved refs (unknown WI, unknown S, `[WI]` on a sectioned document, the old D form) skipped, first-mention order, positions copied from the section row.
- `AggregatorLLMOutput` schema unchanged.
- Library checks unchanged with file tags in the text (`extract_cited_numbers`, cited_count).
- Service: rows written from real stored values with `n` / `ref_id` / `item_id` / `wi_seq`; `wi_seq` taken from the item when missing; no WI number → no row; list shape; section route: same map → the row's text; re-segmented map → the slice at the stored positions; `deleted_at` attachment → 410; a file hidden from «الملفات» still served; routes' ownership.
- Readers: unfold, the writer's sources (walkers), artifact_analyzer label; summarizer and responder input stripped; share snapshot stripped.
- Web: the same grammar cases as Python; plugin ignores `[4]` / `[4,17]` / `[٤]` and the old D form; several refs in one bracket; copy text; tsc + lint.
- New test files whitelisted in `.gitignore` + CI durable list (test dirs are ignored by default).

## 8. Out of scope (v1)

- Extended merger and the extended searches' aggregators (D4: next step).
- Any code check of quotes against the file (D3).
- Mobile chip and popup (only hiding the tags is needed before the app ships).

---

## 9. Build notes (2026-11-27)

Built by two background agents (backend, web) against the §4.6 contract, then reviewed together.

**Checks:**
- Whole CI durable list (with the 3 new suites): 5,934 / 5,934 pass, CI dummy env.
- The new suites after the review fixes: 103 pass.
- Blog / share suites: 212 pass.
- Web: `tsc` clean, lint clean for these files, `tsx --test` 178 / 178.
- `agents/tool_repository/tests/test_edit_artifact_preview_lock.py`: 6 fail on untouched master too (not in CI, not ours).

**Choices made while building (closest to the plan):**
- `AggregatorLLMOutput` is truly unchanged, including the `synthesis_md` description. The rules live only in the flagged prompt, as `FILE_REFS_PROMPT_VERSION` (the aggregator had no version), recorded on its span.
- The rules switch on only when the turn listed at least one document. With the flag on and nothing listed, the turn is byte-identical.
- With segment delivery off, full-text documents are listed too (cited `[F3]`, with its `〔F3〕` label line since D12).
- `[WI]` on a sectioned document and `[WI·S]` on a whole one stay unresolved (§4.1).
- PDF / Word export: the client builds the markdown, so the web sends the same tag-free text as copy (`AgentSearchViewer` `copyContent` → `ExportMenu`). The backend export is untouched.
- Also stripped: the planner's responder input (it writes the chat reply from `synthesis_md`).
- artifact_analyzer doesn't read reports through unfold, so the label is applied in its own loader (`artifact_analyzer/context.py`).
- `files_item_id` = the attachment's item id, given only when «الملفات» is on for the caller, the caller owns the report and the file isn't hidden; otherwise null.
- Migration extras: CHECKs on `ref_id` / `n` / `wi_seq` / `section_seq`, the index on `item_id`, the 195-style client revoke plus a service_role grant, and a self-test block.
- Web, in the report viewer: a tag with no row is hidden even while the rows load. The popup labels the owner «صاحب القسم».

**Review fixes (2026-11-27):**
- One bracket with several refs is accepted on both sides. Before, the web accepted it but the backend didn't, so it would have stored no rows and leaked raw tags into the chat reply and the writer. Both sides also accept `،` before `S`.
- A share / «حفظ كمدونة» snapshot (`blog_service.insert_post`) strips the tags when the copy is frozen, so its public page, snippet and imported copies are clean.
- The section route falls back to the slice at the stored positions when a matching section row has empty text.

**Rework to D10 / D11 (2026-11-27):** the worktree moved onto origin/master 454e6c8 first.
- Tags `[WI-3·S7]` / `[WI-3]` in both grammars (Python and web, the same cases tested on both sides); the D form is no longer matched anywhere.
- Aggregator headings back to today's `### WI-3 · …`; only the sections are numbered; `FileRefDoc` keyed by `wi_seq`; a document with no WI number is not listed.
- Prompt rules rewritten: the tag uses the heading's WI number, and is the only place `WI-N` may appear (the "never `WI-N` in prose" rule stays); `file-refs-v2`.
- Table and API renamed to the `workspace_item_references` pattern: `ref_pk`, `wi_id`, `n`, `ref_id`, `item_id`, `wi_seq`, `section_seq`; `UNIQUE (wi_id, n)` and `UNIQUE (wi_id, ref_id)`; index on `item_id`. Route `…/file-refs/{ref_id}/section`.
- Found while checking every `WI-N` post-processor: the writer read a report chosen as a source straight from `content_md` (`writer_planner/walkers.py`), bypassing unfold's labels. Now labelled the same way.
- Still open: `agents/orchestrator.py::_load_attached_items` (a prior report attached to a later turn keeps raw tags; §4.7).
- Checks: whole CI durable list + the blog suites 6,075 / 6,075 pass; the related agents suites (aggregator, case documents, writer planner, artifact summarizer / analyzer, unfold, planner, publisher) pass except the 6 master-red `test_edit_artifact_preview_lock` tests; web `tsc` clean, lint clean on the touched files, `tsx --test` 185 / 185.

**Not built:** mobile tag hiding (it goes on `feat/mobile-phase4b-iap` before the flag reaches app users); the extended merger (D4).

**D12 — F tags (2026-11-27, owner "go"):**
- Tags `[F3·S7]` / `[F3]` (F + the file's WI number) in both grammars; canonical `F3.S7` / `F3`; the D and WI forms are never matched.
- The aggregator is shown the ready tag: `〔F3·S7 · what — owner〕` on each kept section (`render_sections(file_wi=)`) and one `〔F3〕` line over a whole text (`_render_body(file_tag=)`, both builders). Pins and summary-only documents get none; flag off byte-identical.
- Prompt `file-refs-v3`: copy the label's tag, never build one; the v2 "WI-N allowed inside a tag" exception removed, so the base "never `WI-N`" rule is absolute again.
- Migration 235's CHECK is `^F[1-9][0-9]*(\.S[1-9][0-9]*)?$`; resolution, table columns and routes are unchanged (`FileRefDoc` keyed by `wi_seq`).
