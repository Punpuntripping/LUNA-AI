# Plan — OCR Extraction Step (Mistral `mistral-ocr-latest`)

## Goal

When a conversation contains an un-extracted PDF / PNG / JPG attachment, run Mistral
OCR on it **before the router runs**, so the router and any dispatched agent can see
the document's actual text. Extraction fills the attachment's own `workspace_items`
row (`content_md`). Each file is extracted exactly once. Usage is quota-limited
per user.

This is a **plain module** under `agents/memory/` — not a pydantic-AI agent (no LLM
reasoning of its own; it just calls the Mistral OCR API).

## Turn flow (Option A — sequential, in-process)

`agents/orchestrator.py :: handle_message` runs, in order:

```
pause check
→ memory pre-hook (existing: resummarize_dirty_items, compact_conversation)
→ OCR step           ← NEW: run_ocr_extraction()
→ inline summarize   ← NEW: summarize_workspace_item() per freshly-OCR'd item
→ _route()  (router)
```

Both new steps are best-effort: a failure is logged and the turn continues.

## Scope

- **In scope:** conversation `workspace_items` with `kind='attachment'`,
  `storage_path` set (the conversation-upload path), mime ∈
  {`application/pdf`, `image/png`, `image/jpeg`}.
- **Out of scope:** `case_documents` / case-scoped uploads; the summarizer's
  attachment-specific prompt/flow (→ separate plan `summarizer_attachment_seed.md`).

## Data shape

- The OCR step writes extracted text into the **existing attachment row's**
  `content_md`. No new workspace item is created. (`workspace_content_shape`
  CHECK already allows `content_md` on `kind='attachment'` as long as
  `storage_path`/`document_id` is set — verified live.)
- Idempotency marker: `metadata.ocr_status` on the attachment row.
  Values — `done` · `empty` · `failed` · `skipped_too_large` ·
  `skipped_quota` · `skipped_unsupported`.
  Any non-null value means "never attempt again."
- On success, `metadata` also gets `ocr_pages` (int — the number of pages Mistral
  **actually extracted**, ≤ 30) and `ocr_chars` (int).

## Limits & quota (module constants)

| Constant | Value | Behavior |
|----------|-------|----------|
| `OCR_MAX_FILE_BYTES` | 50 MB | over → mark `skipped_too_large` |
| `OCR_MAX_PAGES` | 30 | passed to Mistral as a page cap — the first 30 pages are extracted, the rest ignored. Never rejected. |
| `OCR_USER_QUOTA` | 100 (lifetime, per user) | over → mark `skipped_quota` |

Quota is a **lifetime total per user** — counted as `agent_runs` rows with
`user_id = X AND agent_family='memory' AND subtype='ocr_extraction' AND status='ok'`.
`agent_family='extraction'` is **dead** — not used. OCR runs are recorded under
`agent_family='memory'`.

## File manifest

### New files

| File | Purpose |
|------|---------|
| `agents/memory/ocr_extractor/__init__.py` | Package exports (`run_ocr_extraction`). |
| `agents/memory/ocr_extractor/models.py` | `OcrDocumentResult`, `OcrExtractionStats` dataclasses. |
| `agents/memory/ocr_extractor/mistral_ocr.py` | Mistral OCR API wrapper — `ocr_document(file_url, mime_type) -> OcrDocumentResult`. Direct SDK call, like the project's embeddings/rerankers (OCR is not a chat model — bypasses the tier/FallbackModel system). |
| `agents/memory/ocr_extractor/runner.py` | Entry point `run_ocr_extraction(supabase, conversation_id, user_id) -> list[str]` — detect → eligibility → quota → extract → persist → record. Returns item_ids whose `content_md` was filled. |
| `agents/memory/summarize.py` | Reusable `summarize_workspace_item(supabase, item_id, *, force=False) -> bool`. The summarize-and-persist core, extracted so both the existing webhook AND the new inline OCR path call one function. This is the "add await to the summarizer" piece. |

### Modified files

| File | Change |
|------|--------|
| `shared/config.py` | Add `MISTRAL_OCR_MODEL: str = "mistral-ocr-latest"`. (`MISTRAL_API_KEY` already exists.) |
| `backend/requirements.txt` | Add `mistralai` (OCR SDK). |
| `agents/orchestrator.py` | `handle_message`: insert the OCR step + inline-summarize loop after the memory pre-hook, before `_route()` (see below). |
| `agents/memory/__init__.py` | Export `run_ocr_extraction` and `summarize_workspace_item`. |
| `backend/app/api/internal_webhooks.py` | Refactor `POST /internal/summarize-workspace-item` to call the extracted `summarize_workspace_item()` — behavior unchanged, just DRY. |

### No DB migration needed

`metadata` is `jsonb` (no schema change for `ocr_status`); `agent_family='memory'`
already exists in the enum; `subtype` is free text; `content_md` on an attachment
is already allowed by the CHECK constraint.

## Component detail

### `mistral_ocr.ocr_document(file_url, mime_type)`

1. `Mistral(api_key=settings.MISTRAL_API_KEY)`.
2. `client.ocr.process_async(...)` — the file is passed **by URL** (a Supabase
   Storage signed URL); Mistral fetches it server-side, so no download/upload
   round-trip is needed:
   - PDF → `document={"type": "document_url", "document_url": <signed url>}`
   - Image → `document={"type": "image_url", "image_url": <signed url>}`
3. Pass the page cap: `pages=list(range(OCR_MAX_PAGES))` so Mistral only ever
   processes the first 30 pages. Mistral processes whatever pages exist within
   that range (a 5-page doc yields 5 pages; a 40-page doc yields 30).
4. `page_count = len(response.pages)` — **the number of pages actually
   extracted**. Join `response.pages[].markdown` → one text blob. Return
   `OcrDocumentResult(text, page_count, model)`.
5. Raises on API failure — the runner catches and marks `failed`.
   *(Exact SDK method names / `pages` param shape to be confirmed against the
   installed `mistralai` version at implementation. If Mistral rejects
   out-of-range page indices, fall back to no `pages` arg + slice
   `response.pages[:OCR_MAX_PAGES]`.)*

### `runner.run_ocr_extraction(supabase, conversation_id, user_id)`

1. Load attachment rows: `workspace_items` where `conversation_id=X`,
   `kind='attachment'`, `deleted_at IS NULL`. Filter in Python to those with
   `metadata.ocr_status` unset (never attempted).
2. For each candidate:
   - **Unsupported mime** → mark `skipped_unsupported`, continue.
   - **Quota:** count prior OCR `agent_runs`; if `>= OCR_USER_QUOTA` → mark
     `skipped_quota`, continue.
   - **Size:** `metadata.file_size_bytes > OCR_MAX_FILE_BYTES` → `skipped_too_large`.
   - Generate a Supabase Storage **signed URL** for `storage_path`
     (`shared.storage.client.get_signed_url`, ~1 h expiry).
   - Call `ocr_document(signed_url, mime_type)` (caps at 30 pages internally).
     - Exception → mark `failed` (store error in `metadata.ocr_error`), continue.
     - Empty text → mark `empty`, continue.
   - **Persist:** `UPDATE workspace_items SET content_md=<text>,
     metadata = metadata || {ocr_status:'done', ocr_pages:<extracted count>,
     ocr_chars}`.
   - **Record:** insert `agent_runs` row — `agent_family='memory'`,
     `subtype='ocr_extraction'`, `output_item_id=<attachment item_id>`,
     `model_used='mistral-ocr-latest'`, `per_phase_stats={'pages': <extracted count>}`,
     `cost_usd ≈ pages / 1000` (Mistral OCR ≈ $1 / 1000 pages), `status='ok'`.
   - Append item_id to the return list.
3. Return the list of item_ids whose `content_md` was filled.

The **page count is the number Mistral actually extracted** (`len(response.pages)`,
≤ 30) — recorded on both `metadata.ocr_pages` and `agent_runs.per_phase_stats`,
and it drives `cost_usd`.

### Orchestrator wiring (`agents/orchestrator.py :: handle_message`)

After the existing memory pre-hook, before `_route(...)`:

```python
# 1b. OCR memory step — extract text from new PDF/image attachments so the
#     router (and any dispatched agent) can see document content.
ocr_item_ids: list[str] = []
try:
    ocr_item_ids = await run_ocr_extraction(supabase, conversation_id, user_id)
except Exception:
    logger.warning("OCR extraction step failed", exc_info=True)

# 1c. Summarize the freshly-OCR'd attachments inline (awaited) before routing.
for _item_id in ocr_item_ids:
    try:
        await summarize_workspace_item(supabase, _item_id)
    except Exception:
        logger.warning("inline summarize failed for %s", _item_id, exc_info=True)
```

`summarize_workspace_item` keeps the existing **300-char floor** — content under
300 chars is not summarized. The summarizer here runs with its *current* generic
prompt; the attachment-specific flow is the separate seed plan.

## SSE (optional polish)

`handle_message` may yield a `{"type":"status","text":"جارٍ استخراج النص من الملفات..."}`
event when `run_ocr_extraction` has candidates, so the user sees progress. Not
required for v1.

## Edge cases

- Mistral API down / error → `ocr_status='failed'`, turn continues, never retried
  (until the marker is manually cleared).
- Scanned / image-only PDF → Mistral still OCRs the page images; a genuinely empty
  result → `ocr_status='empty'`.
- Document longer than 30 pages → first 30 pages extracted, `ocr_pages=30`; not
  rejected.
- Multiple new attachments in one turn → each handled independently; quota applied
  per file in order.
- OCR text < 300 chars → stored in `content_md`, but the inline summarizer skips it.
- `MISTRAL_API_KEY` unset → runner no-ops with a warning (dev-safe).

## Success criteria

1. Upload a text PDF in a general conversation, send a message → the attachment
   row's `content_md` is populated; `metadata.ocr_status='done'`,
   `metadata.ocr_pages` = pages extracted.
2. The attachment gets a `summary` from the inline summarizer (if ≥ 300 chars).
3. The router / dispatched agent can reference the document's content.
4. A second message does **not** re-OCR the same file.
5. A 40-page PDF → only the first 30 pages are extracted; `metadata.ocr_pages=30`.
6. A user past 100 lifetime extractions → `ocr_status='skipped_quota'`.
7. A Mistral failure → `ocr_status='failed'`; the chat turn still completes normally.
8. Each successful extraction appears in `agent_runs`
   (`agent_family='memory'`, `subtype='ocr_extraction'`), with the extracted page
   count in `per_phase_stats`.

## Resolved decisions

- **Page handling** — no pre-count, no `pypdf`. Mistral is given a page cap
  (`pages=0..29`); the page count stored (`ocr_pages`) is the number of pages
  Mistral **actually extracted** (`len(response.pages)`). Over-long documents are
  capped at 30 extracted pages, never rejected.
- **File passed by URL** — a Supabase Storage signed URL is handed to Mistral; no
  download into the backend and no Mistral Files API upload.
