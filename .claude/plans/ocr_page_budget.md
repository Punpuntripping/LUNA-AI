# OCR page budget — read what the allowance allows, never refuse a big file

**Status:** BUILT 2026-11-16 on `feat/ocr-page-budget` (worktree `LUNA_AI_ocr_budget`, base
`origin/master` a4953c7). Uncommitted, not deployed. Decisions D1–D9 are the owner's (2026-11-16 /reflect);
§10 lists what the build changed from this plan and what the real-file test found.

---

## 1. Why

khalid007255 (2026-11-15 to 11-16) sent a 606-page, 39 MB PDF:

| When (Riyadh) | Plan | Result |
|---|---|---|
| 21:37 ×2 | lawyer trial (50 pages/month) | refused: `ocr` 606 / 50 |
| 21:40 | — | **paid 19.90 SAR for Pro** |
| 21:42 – 22:37 ×6, and 11-16 13:19 | Pro (150) | refused: `ocr` 606 / 150 |

Two defects:

1. **The gate counts pages OCR never reads.** `_estimate_ocr_pages` projects the whole file (606), but
   `mistral_ocr.OCR_MAX_PAGES = 30` means OCR only ever reads and bills the first 30. That read would
   have fit the trial. The block was wrong, and so was the upgrade it pushed him into.
   This is the same failure as the 2026-08-16 incident in migration 137 (paid twice, still OCR-blocked).
2. **The 30-page cap is silent.** Pages 31+ of every long file are dropped, and neither the user nor any
   agent is told.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | **No fixed page cap.** Remove `OCR_MAX_PAGES`. The only hard ceilings are Mistral's per-document limits and our existing 50 MB skip (`OCR_MAX_FILE_BYTES`). |
| D2 | **Per-file cap = ⌊⅔ × the plan's TOTAL monthly OCR allowance⌋** (`ocr_pages_monthly`), not ⅔ of what's left. Pro 150 → 100 · basic/lawyer 50 → 33 · Max 1000 → 666 · free 3 → 2. The user can reach the limit, just never in one request. ⅔ rather than 0.66 so that 150 → 100, as in the owner's example. |
| D3 | **7-day exception:** if the plan is not expired and `expires_at − now ≤ 7 days`, drop the ⅔ cap; the file may use everything left. (Every paid purchase, renewals included, zeroes the OCR meter — migration 137 — so those pages would be lost anyway.) |
| D4 | **Floor of 1:** cap = max(1, ⌊⅔ × total⌋). With ≥ 1 page left, at least 1 page is read. |
| D5 | **Several files in one message:** smallest page count first. Each file takes min(its pages, cap, remaining), then remaining goes down. |
| D6 | **Which pages, when the file is bigger than its budget N:** `last = ⌊N/5⌋`, `middle = ⌊N/5⌋`, `first = N − last − middle`. First block = pages 1..first. Last block = the final `last` pages. Middle block = one solid run, **centred in the pages between the first and last blocks**, so it never overlaps either. For small N this becomes "the first N pages". |
| D7 | **Skipped pages are never read.** No later fetch. |
| D8 | **Warnings:** (a) inside the extracted text, a header plus a marker at every gap, so every agent that reads `content_md` knows; (b) to the user after sending; (c) a preview on the composer card before sending. |
| D9 | **The gate blocks on OCR only when nothing can be read** (remaining = 0). That keeps today's `quota_exceeded` banner. Asking the user what to do at that point (choose pages / upgrade / automatic) belongs to a later `ocr_agent` (§7). |

### Worked examples

| Case | Read | Pages |
|---|---|---|
| Pro, 0 used, 606 pages | 100 | 1–60, 314–333, 587–606 |
| Same, plan ends in 5 days | 150 | 1–90, 319–348, 577–606 |
| Pro, 120 used (30 left), 606 pages | 30 | 1–18, 307–312, 601–606 |
| Pro, 150 used | 0 | **gate blocks** (today's banner) |
| Lawyer trial (50), 606 pages | 33 | 1–21, 308–313, 601–606 |
| Free (3), 10-page file | 2 | 1–2 |
| Max (1000), 606 pages | 606 | all |
| Pro, 0 used: image + 20-page + 606-page | 1 + 20 + 100 | the image, the 20-page file in full, then 1–60 / 314–333 / 587–606 of the big file; 29 left |
| `dev` (limit NULL = unlimited) | all | all |

Middle start = `first + 1 + ⌊(gap − middle) / 2⌋`, where `gap = total − first − last`.
Because total > N, gap ≥ middle + 1, so the blocks never touch.

## 3. The pure function (one rule, three callers)

**NEW `shared/quota/ocr_budget.py`.** No I/O. It's used by the gate (backend), the OCR runner (agents)
and, mirrored in TypeScript, the composer preview.

```python
@dataclass(frozen=True)
class FilePlan:
    item_id: str
    total_pages: int
    pages: tuple[int, ...]          # 1-based, ascending; == all pages when not partial
    @property
    def partial(self) -> bool: ...
    def ranges(self) -> list[tuple[int, int]]: ...   # [(1, 60), (314, 333), (587, 606)]

def per_file_cap(total_allowance: int | None, expires_at, is_expired: bool, now) -> int | None
def pick_pages(total_pages: int, n: int) -> tuple[int, ...]          # D6
def plan_files(files: list[tuple[str, int]], *, total_allowance, used, expires_at,
               is_expired, now) -> list[FilePlan]                     # D2–D5
```

- Images count as 1 page.
- Unknown page count counts as 1 (the existing floor). The real count is learned when OCR runs.
- Input comes from the `get_user_quota_state` row: `ocr_pages_monthly`, `ocr_pages`, `expires_at`, `is_expired`.
- Add a small public async reader to `shared/quota` (the runner needs one RPC read; `_quota_state` is private).

## 4. Changes, file by file (paths and lines as on `origin/master`)

1. **`shared/quota/ocr_budget.py`** — NEW (§3) + tests.
2. **`shared/quota/__init__.py` `check()` (:965, OCR block :1102).** Add `ocr_page_counts: list[int] | None`.
   When it's given, compute the plan from the same RPC row `check()` already reads (no second read).
   Block only if any attachment needs OCR and remaining ≤ 0. `est_ocr_pages` stays for compatibility.
3. **`backend/app/services/message_service.py`.** `_estimate_ocr_pages` (:547) becomes `_attachment_page_counts`,
   returning a list. Non-`attachment` kinds are dropped, as today. Pass the list as `ocr_page_counts` (:797).
4. **`agents/memory/ocr_extractor/mistral_ocr.py`.**
   - Delete `OCR_MAX_PAGES`. `ocr_document(url, mime, pages: list[int] | None)`; `None` = the whole file.
     Mistral's `pages` takes 0-based indices.
   - `_page_numbers` already maps Mistral's `index` back to real page numbers, so split reads keep
     their true numbers.
   - NEW `join_pages_with_gaps()`: header + gap markers (§5). The offsets include the marker text, so
     `attachment_pages.char_start` still fits `content_md` (the segmenter's `coerce_pages` checks this).
   - **Full reads keep using `join_pages` unchanged**, so their stored text is byte-identical to today.
   - Timeout: today it's 60 s per call. Size it by page count, or split the page list into concurrent
     chunks. **Measure first** with a real 600-page scan (§6).
5. **`agents/memory/ocr_extractor/runner.py`.** Before the loop: one quota read, then `plan_files` over the
   candidates (smallest first). Each item is OCR'd with its plan's pages.
   - New metadata: `ocr_pages` (pages read — billing unchanged, it's still `len(pages)`), `ocr_page_total`,
     `ocr_page_ranges` (e.g. `[[1,60],[314,333],[587,606]]`), `ocr_partial: true`.
   - Remaining 0 at OCR time (another conversation spent it after the gate): mark `skipped_quota`.
   - Quota read fails: leave `ocr_status` unset, so the file retries next turn and no budget is spent
     blindly.
   - `OcrExtractionStats` gains `partial: list[...]` (title, read, total, ranges) for the status line.
6. **`agents/orchestrator.py`.** Emit the partial-read status line (§5b) in **both** OCR paths: the inline
   step (:2715) and the helper-frame `_run_pre_router_helpers` (:2975). The helper frame is ON for everyone.
7. **Attachment summarizer prompt** (`SYSTEM_PROMPT_ATTACHMENT_AR`, `agents/memory/summarize.py`): one line
   saying that when the text opens with the partial-read notice, the summary must say so. The router
   reads summaries, not the full text.
8. **`frontend/components/workspace/AttachmentRenderer.tsx`.** A banner above the extracted text when
   `ocr_partial` is set (the lasting record of what was read). Add `skipped_quota` copy to `OcrStatusNote`.
9. **Composer preview.**
   - `use-resumable-upload.ts` keeps the page count it already computes (:159) on the `PendingFile`.
   - NEW `frontend/lib/ocr-budget.ts` mirrors §3 (same examples as tests). It reads `useUsage()`:
     `ocr.monthly` used/limit and `plan.expires_at`.
   - `ChatInput` plans all pending files together (D5).
   - `AttachmentUploadCard` shows a short line. The card is only 160 px wide, so the full ranges go in the
     tooltip.
   - The preview can be slightly off if another conversation spends pages first. Accepted.

## 5. Copy — DRAFT, for owner approval (Latin digits only)

**(a) In `content_md`.** The `〔[نظام] …〕` bracket style is the one the history markers already use.
- Header: `〔[نظام] قُرئت 100 صفحة فقط من أصل 606 صفحة في هذا الملف بسبب حدّ رصيد القراءة لدى المستخدم. الصفحات المقروءة: 1–60، 314–333، 587–606. لم تُقرأ بقية الصفحات، فلا يُبنى على محتواها ولا يُفترض.〕`
- At each gap, and after the last block if it doesn't reach the end: `〔[نظام] الصفحات 61–313 (253 صفحة) لم تُقرأ.〕`

**(b) Status line after sending:** `قرأنا 100 صفحة من أصل 606 من «اسم الملف» حسب رصيدك: 1–60، 314–333، 587–606. بقية الصفحات لم تُقرأ.`

**(c) Composer card:** `سيُقرأ 100 من 606 صفحة` (tooltip: the ranges plus «حسب رصيدك»).

**Attachment banner:** `قُرئت 100 صفحة من أصل 606 حسب رصيدك: 1–60، 314–333، 587–606`

## 6. Checks during the build

1. **Mistral OCR limits:** max pages and size per document (believed ≈ 1,000 pages / 50 MB — confirm),
   and throughput, which sets the timeout. Test with a real 600-page scanned PDF.
2. **Helper-frame OCR ceiling.** A large read (Max: up to 666 pages) may run past it. The router then
   answers without the text, and the text lands next turn. Measure, then raise the ceiling or accept.
3. **Do `status` lines persist after reload?** If not, the attachment banner (§4.8) is the lasting record.
4. **The 6 `free` subscriptions that have an `expires_at`** — confirm D3 does nothing odd for them.

## 7. Out of scope (later)

- **`ocr_agent`:** when the allowance can't cover a file, ask the user — choose pages / ترقية الباقة /
  automatic pick — with the budget (total, remaining) and the plan that would cover the file. Owner
  direction, 2026-11-16; needs its own plan.
- Reading skipped pages later (D7).
- Mobile composer preview.

## 8. Tests

- `ocr_budget`: every row in §2's table; N = 1, 2, 3, 5; a 1-page file; unlimited; exactly 7 days;
  an expired plan.
- `join_pages_with_gaps`: `coerce_pages` accepts the offsets; a full read is byte-identical to `join_pages`.
- Runner (fake supabase): the planned pages reach Mistral; metadata; ledger `pages_used` = pages read;
  `skipped_quota`; quota read failure leaves the file unmarked.
- Gate: 606 pages on Pro with 150 left → passes; 0 left → blocks.
- `frontend/lib/ocr-budget.ts`: the same table.
- Note: `tests/` dirs are gitignored — `git add -f`.

## 9. Ship order

1. **Backend + agents** (§4.1–4.7). This alone fixes the wrong block and the silent cap. A deploy
   restarts the backend and kills in-flight searches — ship off-peak.
2. **Frontend** (§4.8–4.9). It ignores the new metadata keys until then, so the order is safe.
3. **Khalid:** after step 1, re-sending the file works on his Pro plan (100 of 606 pages).

## 10. As built (2026-11-16) — changes from the plan and test results

**Mistral facts found while building**
- OCR refuses a document over **1,000 pages even when only a few pages are asked for**
  (`document_parser_too_many_pages`), and documents over 50 MB (its docs). The file store accepts
  512 MB, but OCR still applies both limits.
- A call took **45–60 s whatever its size** (1 page 47 s, 30 pages 57 s); parallel calls overlap
  (4 × 10 pages = 60 s wall). The old flat 60 s timeout sat at the edge → now 120 s + 1 s/page (cap 600).
- With a page list, `index` comes back as the ORIGINAL 0-based page (asked 0,1,9,19 → got 0,1,9,19),
  so `_page_numbers` numbers URL-path partial reads correctly.

**Server-side cutting (owner: "split and test")** — `agents/memory/ocr_extractor/big_file.py`, pypdf 7.1.0.
A PDF is cut when it is read in part, its page count is unknown, or it is over 1,000 pages / 50 MB:
download → real page count (re-picks the pages if the client's count was wrong) → cut the chosen pages
(≤ 50 pages / 20 MB per cut, 8 in parallel) → each cut to Mistral as a `data:` URL → pages mapped back.
Two things made cuts huge on the test file, both dropped (never drawn):
- `/PieceInfo` (InDesign/Illustrator private data): 10 pages 25–41 MB → 0.4 MB.
- `/Link` annotations: a link copies its TARGET page into the cut — a 27-link contents page was 41 MB → 0.08 MB.
  Other annotations (stamps, form fields, notes) are kept. A full read within Mistral's limits still goes
  by URL exactly as before; if pypdf cannot open a PDF, a file within the limits falls back to the URL path.

**Other changes from the plan**
- **No pages left at OCR time:** the file is NOT marked `skipped_quota`; it is left without `ocr_status`
  so it is read on a later turn once pages return (marking it would have lost it for good).
- **Quota read fails:** nothing read, nothing marked (retried next turn).
- **Gate counts** leave out files already attempted (`ocr_status` set — OCR never reads them again) and
  non-attachment kinds. `_estimate_ocr_pages` kept as a sum wrapper for the eval script.
- **Web client** no longer sends `page_count = 1` when pdfjs cannot parse a PDF; it sends nothing, and the
  server counts the pages itself (planning for 1 page would have read only page 1).
- **Locked user** (no plan) → zero budget.

**Follow-up 2026-11-16 (after the first live test failed):** xl0rch (Pro) sent the 202 MB file with
«اش تقول الدعوى 410654». The plan was right — 100 pages (1–60, 602–621, 1164–1183; two 50-page cuts) —
but neither Mistral call returned within 170 s from production → TimeoutError, file `failed`, 0 pages
billed; the router answered from the judgments library instead (410654أ is in the corpus; 1 unlock).
Case 410654 is on page 81 — outside the planned pages anyway (→ query-aware page choice is the next design
question). Changes:
- OCR call timeout and both OCR waits (helper `OCR_CEILING_S`, inline `wait_for`) → **10 minutes**.
- **`status` lines never reach the client** (message_service drops them by design, 2026-10-26), so the
  notices shipped in d8072dc were invisible. New SSE event **`ocr_progress`** (`extracting` → web shows
  «ريحان يستخرج المستندات» instead of «ريحان يفكّر»; `done` + `notices`), notices persisted as
  `messages.metadata.ocr_notices` and rendered above the reply. Failure / still-running notices too.
- Owner wording: «استخراج» not «قراءة» everywhere (composer «سنستخرج 100 من 606 صفحة», notices,
  banner, and the agent-facing markers «استُخرجت … / لم تُستخرج»).

**Text-layer fallback (owner 2026-11-16, built, NOT committed/deployed):** Mistral multi-page OCR stalled
for both keys that evening (1 page OK; 10+ pages hung; its gateway dropped calls at ~240 s). When Mistral
fails, the planned pages are read from the PDF's own text layer — `agents/memory/ocr_extractor/text_layer.py`
(pypdf `extract_text` + NFKC, tatweel, diacritic spacing, bracket mirroring when flipped, Arabic-Indic digit
runs reversed when the document's years say so — decided once per file from 20 sampled pages).
- Cutting path: per-cut fallback; the first outage-type error (timeout, connection, 5xx, 429) sends the rest
  of the file straight to the text layer; one Mistral deadline per file (`MISTRAL_DEADLINE_S` 480 s) so the
  fallback lands inside the 10-minute wait. Whole-file URL path: `text_layer_document`. Mistral per-call
  timeout 540 s.
- **Free:** only Mistral pages are billed (`OcrDocumentResult.billed_pages`); a file read wholly from the
  text layer writes no ledger row. Image pages with no text layer are "lost" (marked, told to the user).
- Markers: header + «من طبقة نص الملف: الصفحات …» before each text-layer run, «تعذّر استخراج الصفحات …» for
  lost pages; budget gaps keep «لم تُستخرج». Metadata `ocr_text_layer_ranges`, `ocr_lost_ranges`.
- Quality (artifact https://claude.ai/artifact/J2iUn9MLWMXBQZ36Kmeg7u): words 93–96% vs Mistral and exact
  where Mistral misread (يحوز→يجوز); dates' parts scatter in the line; tables flatten.
- Real file with Mistral forced down: 96/100 pages from the text layer in 13 s, 4 image pages lost, 0 billed.
- Reviewed by an independent agent; its 4 fixes applied (per-file deadline + breaker, per-file digit
  decision, error logging, digits count toward "usable").

**Real-file test** — `مدونة السوابق القضائية التأمينية.pdf` (202 MB, 1,183 pages), full runner with real
Mistral; only the DB, the storage download and the quota read faked (Pro: 150 pages, none used):
100 pages read (1–60, 602–621, 1164–1183) in 85–101 s, billed 100 pages ($0.10), 100 page rows all fit
`content_md`, 0 empty pages, 185k chars, header + both gap markers in place, notice text correct.

**Tests:** `agents/memory/ocr_extractor/tests/` 136 pass (new: `test_page_budget.py`,
`test_partial_reads.py`); `backend/tests/test_ocr_page_budget_gate.py` (new, whitelisted in .gitignore);
backend suite 3,262 pass — 5 `test_payments.py` failures are pre-existing on master (expect no
`claim_x_first_purchase` RPC). Frontend `tsc` + eslint clean.

**Still open**
- **Chat attachments up to 250 MB (owner OK 2026-11-16), BUILT:** composer `ChatInput.MAX_FILE_SIZE`,
  `upload_session_service.ATTACHMENT_MAX_FILE_SIZE` (passed by `init_attachment_upload`),
  `UploadInitRequest.size_bytes` outer bound, migration **217** (bucket `file_size_limit` → 250 MB,
  APPLIED to prod 2026-11-16 after the owner raised the dashboard's global limit; a live 60 MB probe
  upload to `documents/_size_probe/` succeeded and was deleted). Case documents stay 50 MB (`MAX_FILE_SIZE`, default of `init_upload`); so do the
  legacy multipart route (`workspace.py:848`, buffers in memory — used by the workspace panel's add-file
  menu) and mobile (`mobile/src/attachments/limits.ts`). Uploads go browser → Supabase by TUS and
  finalize reads only a HEAD + 16 bytes, so no request carries 250 MB through the backend.
  The dashboard's global limit lives at Storage → Configuration → Settings → "Global file size limit"
  (not under Project Settings); the bucket limit cannot exceed it.
  `big_file.MAX_FILES_AT_ONCE = 2` bounds memory (cutting peaked ~280–440 MB on the 202 MB file).
- Lock file: `pypdf==7.1.0` added as one line by hand (zero dependencies on 3.11), not re-resolved.
- The free-week composer offer (`ChatInput.maybeOfferFreeWeek`) still assumes a file past the trial's
  pages "would still be refused" — now it is read in part. Left as is.
- Mobile composer preview not built.

## 11. Text layer past the budget (owner, 2026-11-23) — BUILT, not deployed

Owner: "if a user has consumed the OCR, then we move to copy and paste (the fallback)… use that also to
extract the rest of the pages", plus "give a warning to the agents that work after that — don't trust
dates or any numbers", and "make the warning reach each segmenter, this is the most reliable way".
Flag `OCR_TEXT_LAYER_OVERFLOW_ENABLED` (default **on**; false = §10 behaviour exactly).

**Extraction** (`big_file.py`, `runner.py`)
- PDFs only. The budget still picks the OCR pages (D2–D6 unchanged, billed as before); every other page is
  read from the PDF's text layer (`overflow_pages`), free, in the same download, alongside the Mistral cuts.
- No pages left: the PDF goes through the cutting path with no OCR pages and is read from its text layer
  alone. Images still wait (no `ocr_status`), as before.
- Ceiling `TEXT_LAYER_MAX_PAGES = 1000` per file, OCR + text layer together (the largest read the pipeline
  already handled; a 10,000-page PDF would outrun the 10-min wait and fan out into ~120 segmenter calls).
  Past it: «لم تُستخرج الصفحات … نستخرج من الملف الواحد 1000 صفحة على الأكثر».
- Blank pages past the budget (no text layer) are budget gaps («لم تُستخرج»), never "lost".
- No pages left and nothing in the text layer (a scan) or pypdf cannot open it: left WITHOUT `ocr_status`
  (OCR reads it once pages return) and marked `ocr_no_text_layer` so later turns do not download it again.
- Metadata: `ocr_text_layer_ranges` = every text-layer page (both reasons — the segmenter reads it);
  `ocr_overflow_ranges` = the ones past the budget. `ocr_partial` only when pages are still unread.

**Gate** (`message_service._attachment_page_counts(for_gate=True)`): a send carrying a new PDF is never
refused for OCR pages (`[]` to the gate). Images alone — or a PDF already marked `ocr_no_text_layer` —
at 0 pages left are refused as before.

**Warnings to the agents**
- `content_md`: own header per reason — `overflow_header` (past the budget) / `text_layer_header` (Mistral
  failed), both ending in `partial.TEXT_LAYER_CAUTION` (dates, numbers, amounts, article/case numbers,
  order of events: not to be relied on without saying so or the user confirming).
- Segmenter (`attachment_segmenter/text_layer.py`): every call whose OWN paragraphs include text-layer
  pages gets a «Pages taken from the PDF's text layer» block naming those paragraphs (single, each split
  part, bundles — real pages per member); and every stored section overlapping those pages is stamped by
  code with `〔[نظام] نص الصفحات … في هذا المقطع مستخرج من طبقة النص … لا بـOCR: …〕` at the top of its
  text — so deep search (core) and the writer (core + supporting) read it, trimmed or not. Maps made before
  this change are not redone.
- Summarizer (`SYSTEM_PROMPT_ATTACHMENT_AR`): one bullet — name the pages, never state their dates /
  numbers / amounts as facts. The router reads summaries.

**User copy (draft — owner to approve)**: notices in `runner._overflow_notices`; composer card
(`AttachmentUploadCard.tsx`) «100 من 606 صفحة بـOCR، والبقية من نص الملف» / «نفد رصيد OCR: سنستخرج نص الملف».

**Tests**: `ocr_extractor/tests/test_text_layer_overflow.py` (new, in CI), `attachment_segmenter/tests/
test_text_layer_notice.py` (new), gate cases in `backend/tests/test_ocr_page_budget_gate.py` (now in CI).
