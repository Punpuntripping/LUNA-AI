# Upload Reliability — Plan

**Status:** Draft, awaiting kickoff
**Date:** 2026-05-23
**Owner:** mhfallath
**Scope decision:** Case documents **and** chat attachments (one migration, same fix)
**Library decision:** `tus-js-client` (headless, ~8 KB)
**Size cap decision:** 50 MB (unchanged)

---

## 1. Goal & non-goals

### Goal
Make PDF/image upload reliable on Luna by moving bytes **off** the Railway request path. Browser uploads directly to Supabase Storage over the TUS resumable protocol; FastAPI only mints short-lived tokens and finalizes metadata. Result: no timeouts, no memory spikes, resume-on-blip, live progress.

### Non-goals
- Not raising the 50 MB cap (deferred).
- Not changing OCR (`agents/memory/ocr_extractor/`) — it stays as the async post-processor; we just feed it more reliably.
- Not migrating download paths — signed-URL download already works and is unrelated.
- Not adding antivirus scanning (defer to later wave).

---

## 2. Current state — root cause map

### Path A: Case documents
`UploadDropzone.tsx` → `documentsApi.upload(caseId, file)` (multipart) → `POST /cases/{id}/documents` → `document_service.upload_document` reads file into RAM (`b"".join(chunks)`, up to 50 MB per request) → `shared.storage.client.upload_file` pushes to Supabase Storage → inserts `case_documents` row with `extraction_status='pending'`.

### Path B: Chat attachments
`ChatInput.tsx` collects `pendingFiles` into `chat-store`. When the message is sent, `use-chat.ts` calls `workspaceApi.uploadAttachment(conversationId, file)` (multipart) → `POST /conversations/{id}/workspace/attachments/upload` → analogous service path → inserts `workspace_items` row with `kind='attachment'`.

### Why it fails (concrete)
| Failure | Cause |
|---|---|
| Hangs / 502 on 30–50 MB PDFs | Railway proxy + uvicorn keep-alive timeout < end-to-end double-hop time |
| Memory spikes on backend | Full file buffered into a single `bytes` object before upload to Supabase |
| Network blip = restart from 0 | No resumability — single HTTP request, no chunks |
| "Spinner forever" UX | No upload progress events — `fetch` gives binary 0/100 % |
| Chat blocks during upload | `use-chat.ts` `await`s the upload before the message even sends |
| Silent OCR failures | `extraction_status='pending'` has no visible retry/observability loop |

---

## 3. Target architecture (the "ChatGPT pattern")

Three phases, one round trip per phase:

```
┌──────────┐  1. init        ┌──────────┐
│ Browser  │ ───────────────▶│ FastAPI  │  creates row, status='uploading'
│          │ ◀───────────────│          │  returns {item_id, storage_path, upload_url, token}
│          │                  └──────────┘
│          │  2. PATCH chunks (TUS, 6 MB each, auto-retry)
│          │ ────────────────────────────────▶ Supabase Storage
│          │ ◀──── progress events ──────────
│          │
│          │  3. finalize    ┌──────────┐
│          │ ───────────────▶│ FastAPI  │  HEAD object, verify size+magic
│          │ ◀───────────────│          │  status='uploaded', enqueue OCR
└──────────┘                  └──────────┘
```

Bytes never traverse Railway. Backend is small JSON in/out.

---

## 4. Phased migration

### Phase 1 — Backend init/finalize routes (1 day)
New endpoints alongside existing ones (additive, no breakage):

- `POST /cases/{case_id}/documents/init` → body `{filename, mime_type, size_bytes}` → returns `{document_id, storage_path, upload_url, upload_token, expires_at}`. Creates `case_documents` row with `extraction_status='uploading'`.
- `POST /documents/{document_id}/finalize` → server runs `client.storage.from_(bucket).list(...)` + downloads first 16 bytes, verifies magic+size, flips `extraction_status='pending'`, enqueues OCR via existing path.
- `POST /documents/{document_id}/cancel` → soft-delete row, best-effort delete storage object (for user-cancelled uploads).
- Mirror set for attachments: `POST /conversations/{id}/workspace/attachments/init` + `/finalize` + `/cancel`.

The upload URL is the Supabase TUS endpoint: `https://<project>.storage.supabase.co/storage/v1/upload/resumable`. The token is the user's Supabase JWT (we already have it server-side via `get_current_user`) — no new auth machinery.

### Phase 2 — Frontend TUS client (1 day)
- Add `tus-js-client` to `frontend/package.json`.
- New module `frontend/lib/upload-client.ts` — single `uploadFile(url, file, opts)` wrapper around tus-js-client with chunk size 6 MB, exponential backoff `[0, 1000, 3000, 5000, 10000]`, `removeFingerprintOnSuccess: true`, progress callback, abort handle.
- New hook `frontend/hooks/use-resumable-upload.ts` — orchestrates init → tus → finalize, exposes `{start, cancel, progress, status, error}`. Used by both `UploadDropzone` and `ChatInput` flows.
- Rewrite `UploadDropzone.tsx` to call the new hook, show `% / N MB · ETA · [cancel]`.
- Rewrite chat-attachment flow in `use-chat.ts` / `chat-store.ts` so each `PendingFile` carries its own upload state (`status: 'queued' | 'uploading' | 'uploaded' | 'failed'`, `progress: 0..1`, `itemId`, `cancel`). Uploads kick off the moment the file is added — the send button is disabled until all attachments are `uploaded`, but the user can keep typing.

### Phase 3 — Robustness & ops (½ day)
- Logfire spans: `upload.init`, `upload.tus.chunk`, `upload.finalize`, `upload.failed`. Attributes: `mime_type`, `size_bytes`, `case_id`, `duration_ms`, `retry_count`.
- Reconciler job: a daily APScheduler tick (alongside `attachment_cleanup.py`) that finds rows stuck in `extraction_status='uploading'` for > 24 h and either calls finalize (if object exists) or hard-deletes the orphan row.
- Bucket policy: set `file_size_limit=52428800` (50 MB) and `allowed_mime_types=['application/pdf','image/png','image/jpeg']` on the documents bucket. Defense in depth — the cap is now enforced by Supabase, not just FastAPI.

### Phase 4 — Cutover (½ day)
- Once new flow is verified in prod, mark the legacy `POST /cases/{id}/documents` (multipart) as deprecated in the route docstring. Leave it for 1 sprint, then delete along with `_MAGIC_BYTES` validation and the chunked-read loop in `document_service.upload_document`.

---

## 5. File manifest

### New files
| File | Purpose |
|---|---|
| `frontend/lib/upload-client.ts` | tus-js-client wrapper |
| `frontend/hooks/use-resumable-upload.ts` | init → tus → finalize hook |
| `frontend/components/chat/AttachmentUploadCard.tsx` | per-file progress card in `FilePreview` |
| `backend/app/services/upload_session_service.py` | init/finalize/cancel logic shared by docs + attachments |
| `backend/app/services/upload_reconciler.py` | sweep stuck `uploading` rows |
| `agents_reports/upload_reliability_validation.md` | post-deploy validation report |

### Modified files
| File | Change |
|---|---|
| `backend/app/api/documents.py` | Add `/init` `/finalize` `/cancel`; deprecate old `POST /cases/{id}/documents` |
| `backend/app/api/workspace.py` | Add `/attachments/init` `/finalize` `/cancel` |
| `backend/app/services/document_service.py` | Extract OCR-enqueue into a function the finalizer can call |
| `backend/app/services/workspace_service.py` | Same — split byte-write path from row-create path |
| `backend/app/main.py` | Register `upload_reconciler` with APScheduler |
| `backend/app/models/responses.py` | New `UploadInitResponse`, `UploadFinalizeResponse` |
| `frontend/lib/api.ts` | `documentsApi.initUpload/finalizeUpload/cancelUpload`; same for `workspaceApi` |
| `frontend/hooks/use-documents.ts` | Replace `useUploadDocument` mutation with the new hook |
| `frontend/hooks/use-chat.ts` | Stop uploading in `send`; require all pending files to be `uploaded` first |
| `frontend/stores/chat-store.ts` | `PendingFile` gains `status`, `progress`, `itemId`, `cancel` |
| `frontend/components/chat/ChatInput.tsx` | Start upload on file-select; disable send while any file `uploading` |
| `frontend/components/chat/FilePreview.tsx` | Render per-file progress + retry/cancel |
| `frontend/components/documents/UploadDropzone.tsx` | Switch to new hook; real progress bar; queue (≤5 files) |
| `frontend/package.json` | Add `tus-js-client` |
| `shared/storage/client.py` | Add `head_object(bucket, path) -> {size, content_type}` helper |

---

## 6. API contract (exact)

### `POST /cases/{case_id}/documents/init`
**Request**
```json
{ "filename": "contract.pdf", "mime_type": "application/pdf", "size_bytes": 12582912 }
```
**Response 201**
```json
{
  "document_id": "uuid",
  "storage_path": "cases/{case_id}/{file_id}_contract.pdf",
  "upload_url": "https://<project>.storage.supabase.co/storage/v1/upload/resumable",
  "upload_token": "<short-lived JWT scoped to this path>",
  "expires_at": "2026-05-24T10:00:00Z"
}
```
**Errors:** `400` invalid mime/size, `403` not your case, `409` filename collision (rare; client retries with new UUID prefix).

### `POST /documents/{document_id}/finalize`
**Response 200** — full `DocumentResponse`, `extraction_status='pending'`.
**Errors:** `404` storage object missing → returns `409 UPLOAD_NOT_COMPLETE` so client can retry the TUS upload.

### `POST /documents/{document_id}/cancel`
**Response 200** `{ "success": true }`. Idempotent.

Mirror endpoints for `/conversations/{id}/workspace/attachments/{init,finalize,cancel}` using `workspace_items`.

---

## 7. Database changes

**None required.**

- `case_documents.extraction_status` already supports `pending`. We extend the meaning: `uploading` (init done, bytes not yet confirmed) → `pending` (finalize done, awaiting OCR) → `extracting` → `ready` / `failed`. The enum currently allows free text per Wave 6A — verify and add `uploading` to the enum if it's a CHECK constraint.
- `workspace_items` already has nullable `storage_path` and a `metadata` JSONB; we'll add `metadata.upload_status` for the same lifecycle. No migration needed.

I'll confirm the enum constraint shape via `mcp__supabase__list_tables` during execution and add a one-line migration only if necessary.

---

## 8. Storage bucket configuration

Both buckets (`documents` for case docs, plus whichever attachment bucket is in use) need:

```
file_size_limit:        52428800
allowed_mime_types:     ['application/pdf','image/png','image/jpeg']
```

And a storage RLS policy ensuring the user can only upload to their own paths. Existing case-ownership check stays in `/init` — we just add belt-and-braces at the storage layer so a leaked token can't write outside its prefix.

**Drafted:** `shared/db/migrations/045_storage_resumable_uploads.sql` (bucket config + INSERT/UPDATE policies on `storage.objects`, scoped to `cases/{case_id}/...` when the caller owns the case and `general/{user_id}/...` when the path matches the caller's `user_id`). SELECT/DELETE intentionally left service-role-only since downloads use backend signed URLs and deletes go through backend cleanup. Awaiting review before `mcp__supabase__apply_migration`.

---

## 9. Success criteria

- [ ] 50 MB PDF uploads succeed on a throttled connection (Chrome DevTools "Slow 3G") that previously timed out
- [ ] Killing Wi-Fi mid-upload then reconnecting resumes from last chunk (no restart from 0)
- [ ] Backend memory per request < 5 MB regardless of file size (verify via Railway metrics)
- [ ] Upload progress bar shows `%` / `N MB / total MB` / ETA, updating ≥ 2 Hz
- [ ] Chat: user can type and send subsequent text messages while a 40 MB attachment uploads in the background
- [ ] Logfire shows `upload.finalize` span for every successful upload; failures show `upload.failed` with `retry_count`
- [ ] Reconciler removes orphan `uploading` rows older than 24 h
- [ ] Existing case documents still download via the unchanged `/documents/{id}/download` route
- [ ] No regression in OCR — `extraction_status` still flows to `ready` for new uploads

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Supabase TUS endpoint requires a specific JWT scope we don't have | Verify with a one-off curl during Phase 1 before writing the route; fall back to service-role-minted short-lived token if user JWT insufficient |
| `tus-js-client` browser-storage fingerprint conflicts with Luna's "no localStorage for auth" rule | tus-js-client uses localStorage for the *fingerprint URL map*, not credentials — acceptable. Set `removeFingerprintOnSuccess: true` so it's transient. |
| RLS policy too strict, blocks legitimate uploads | Test in a Supabase preview branch first (or in dev with permissive policy, then tighten in a follow-up migration) |
| Old multipart route used by a script we don't know about | `grep` and `Logfire` query before removing in Phase 4 |
| User cancels mid-upload, leaves orphan in storage | Cancel endpoint deletes the object; reconciler is the safety net for browser crashes |

---

## 11. **Things I need from you (manual ops) — STATUS**

| # | Item | Status |
|---|---|---|
| 1 | TUS endpoint reachable | ✅ verified `OPTIONS 200`, `POST` returns `400 "Invalid Compact JWS"` (endpoint alive, correctly rejecting un-JWT'd POST) |
| 2 | Bucket name(s) | ✅ both docs and chat attachments share bucket `documents` (chat path goes through `backend/app/api/workspace.py:387` using `STORAGE_BUCKET_DOCUMENTS`) |
| 3 | Storage CORS | ✅ already `Access-Control-Allow-Origin: *` on the TUS endpoint — no dashboard change needed |
| 4 | Bucket size + MIME limits | ✅ drafted as SQL in `shared/db/migrations/045_storage_resumable_uploads.sql` (50 MB cap, PDF/PNG/JPEG only) — awaiting your approval before apply |
| 5 | Storage RLS policy | ✅ drafted in the same migration (INSERT + UPDATE on `storage.objects`, scoped to user-owned `cases/{case_id}` and `general/{user_id}` prefixes) — awaiting your approval before apply |
| 6 | Deploy + smoke session | ⏸ deferred until Phase 1+2 merged |
| 7 | Test user pinned | ⚠️ candidate **`test@luna.ai`** (auth_id `3ca14d11-efb6-4c83-9e49-3d14e6176453`, 20 active cases — most realistic data). Fallback: `testluna@test.com` (7 cases). I need the password from you — or say "create a fresh one" and I'll add a new account via the auth API and you whitelist it. |
| 8 | Deprecation window | ✅ confirmed by your "do all 7" — going with **7 days post-deploy**, then delete old multipart route in Phase 4 |

**Net outstanding from you, in order:**

1. **Review the migration** `shared/db/migrations/045_storage_resumable_uploads.sql` (items #4 + #5). It does two things: locks the `documents` bucket to 50 MB + PDF/PNG/JPEG, and adds two RLS policies on `storage.objects` for INSERT and UPDATE scoped to the user's own case/general prefixes. If you OK it, I apply via `mcp__supabase__apply_migration`.
2. **Test user** (item #7) — either give me the password for `test@luna.ai`, or say "make a new one".
3. **Be reachable for ~30 min** when Phase 1+2 are deployed (item #6).

---

## 12. Out of scope (intentionally)

- Antivirus / ClamAV scanning — separate plan, post-launch.
- Server-side image thumbnail generation — keep frontend `URL.createObjectURL` previews for now.
- Background OCR retry UI — `extraction_status='failed'` currently shows nothing in the UI; that's a separate workspace-status epic.
- Raising the 50 MB cap or adding chunked OCR for huge PDFs.

---

## 13. Estimated effort

| Phase | Work | ETA |
|---|---|---|
| 1 | Backend init/finalize/cancel for docs + attachments | 1 day |
| 2 | Frontend `tus-js-client` integration, two UI surfaces | 1 day |
| 3 | Logfire spans, reconciler, bucket policy | ½ day |
| 4 | Deprecation cleanup | ½ day (after 7-day soak) |
| **Total** | | **~3 dev days + 1 soak week** |

---

## 14. Agent assignments

| Step | Agent |
|---|---|
| DB enum check / RLS migration | `@sql-migration` |
| Backend init/finalize routes + service | `@fastapi-backend` |
| Storage helper additions | `@shared-foundation` |
| Frontend TUS client + hooks + UI | `@nextjs-frontend` |
| Contract verification between layers | `@integration-lead` |
| RLS verification | `@rls-auditor` |
| Post-deploy smoke | `@smoke-tester` |
| Final report | `agents_reports/upload_reliability_validation.md` |
