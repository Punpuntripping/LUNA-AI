# Design 5 — Storage + Upload Integrity

## Verified library facts (installed `supabase==2.28.0`, `storage3==2.28.0`)

- `storage3`'s `download()` does **not** expose a headers parameter — no public way to send `Range` through supabase-py. Its internal `_request()` accepts headers but is private API; do not depend on it.
- `storage3` already carries a default httpx timeout of **20s** (`storage3/constants.py: DEFAULT_TIMEOUT = 20`), and `ClientOptions` exposes `storage_client_timeout` / `postgrest_client_timeout`. The audit's "no storage timeout" is partially stale: a hung connection dies at 20s today. We still tighten explicitly so it survives library upgrades and is visible in code.
- `workspace_item_references` (migration 049, lines 40-51): FK column is `wi_id` → `workspace_items(item_id)` with `ON DELETE CASCADE`. **The cascade never fires because delete_workspace_item is a soft delete** — rows must be hard-deleted explicitly.
- The reconciler (`upload_reconciler.py:389-488`) is idempotent: it only acts on rows whose `upload_status == 'uploading'`, and both outcomes (promote→`ready`, cancel→soft-delete) flip that status. `cancel_storage_object` never raises; `finalize_*` has an idempotent fast path. Double-run safe.

---

## Fix 1 — Range-request magic bytes

**Anchor:** `shared/storage/client.py:233-252` (`download_head_bytes`), called from `upload_session_service.py:227`.

Use a raw httpx GET against the storage REST endpoint with service-role auth. Supabase storage-api supports byte-range on authenticated object GETs — verify once during implementation:
`curl -s -D- -o NUL -H "Range: bytes=0-15" -H "Authorization: Bearer $SERVICE_KEY" "$SUPABASE_URL/storage/v1/object/documents/<some-path>"`

```python
import httpx
from urllib.parse import quote

STORAGE_HEAD_TIMEOUT = 5.0  # seconds — 16 bytes should never take longer

def download_head_bytes(bucket: str, path: str, n: int = 16,
                        supabase: SupabaseClient | None = None) -> bytes:
    """Read the first ``n`` bytes of an object via an HTTP Range request.
    Falls back to a bounded streamed read if the server rejects ranges
    (416/501) or replies 200 with the whole body."""
    settings = get_settings()
    base = settings.SUPABASE_URL.rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{quote(path)}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Range": f"bytes=0-{n - 1}",
    }
    try:
        with httpx.Client(timeout=STORAGE_HEAD_TIMEOUT) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (200, 206):
                    # 206 = ranged body; 200 = server ignored Range — either
                    # way read at most n bytes and bail out of the stream.
                    buf = b""
                    for chunk in resp.iter_bytes(chunk_size=n):
                        buf += chunk
                        if len(buf) >= n:
                            break
                    return buf[:n]
                if resp.status_code in (416, 501):
                    raise _RangeUnsupported(resp.status_code)
                resp.read()
                resp.raise_for_status()
    except _RangeUnsupported as exc:
        logger.warning("download_head_bytes: range unsupported (%s) for %s/%s — "
                       "falling back to full download", exc.status, bucket, path)
        return _download_head_bytes_legacy(bucket, path, n, supabase=supabase)
```

`_download_head_bytes_legacy` is the current body renamed; `_RangeUnsupported` is module-private. Other exceptions propagate — caller (`upload_session_service.py:230-244`) already converts failures to `UPLOAD_NOT_COMPLETE` (client retries), right for timeouts too. The streamed fallback inside the 200 branch means even a misbehaving server cannot force a 50 MB buffer into RAM.

## Fix 2 — Explicit storage timeouts

**Anchor:** `shared/db/client.py:48-62`. **Coordinate with design 1 (foundation owns this file):** foundation's `_harden_sessions` builds the storage client with `httpx.Timeout(connect=5, read=60, write=60, pool=5)`; the `ClientOptions(storage_client_timeout=60, postgrest_client_timeout=...)` fallback is belt-and-suspenders. **Value: 60s** (not 10) — must cover the worst-case legacy 50 MB upload through storage3's single shared timeout. If legacy upload is later deprecated, drop to 10s. Mirror in `get_supabase_anon_client` and async factories. Import path caveat: `supabase.lib.client_options` is semi-public; guard with try/except falling back to `from supabase import ClientOptions`.

## Fix 3 — DELETE /workspace/{item_id} cleanup

**Anchor:** `workspace_service.py:369-400` (`delete_workspace_item`); good pattern at `:561-631` (`cancel_attachment_upload`, best-effort storage delete at 629-631).

The current UPDATE already returns the full row representation — no extra pre-fetch needed; read `storage_path` and `kind` from the returned row:

```python
def delete_workspace_item(supabase, auth_id, item_id) -> None:
    """Soft delete (set deleted_at), then best-effort cleanup of the storage
    object (attachments) and workspace_item_references rows. Cleanup failure
    never fails the request — the soft-delete is the user-visible contract."""
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (supabase.table("workspace_items")
            .update({"deleted_at": now, "updated_at": now})
            .eq("item_id", item_id).eq("user_id", user_id)
            .is_("deleted_at", "null").execute())
    except Exception as e:
        logger.exception("Error deleting workspace_item: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR,
                                detail="حدث خطأ أثناء حذف العنصر")
    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.ARTIFACT_NOT_FOUND,
                                detail="العنصر غير موجود")
    row = result.data[0]

    # --- best-effort cleanup: NEVER raises past this point -----------------
    storage_path = row.get("storage_path")
    if storage_path:
        settings = get_settings()
        upload_session_service.cancel_storage_object(
            supabase, bucket=settings.STORAGE_BUCKET_DOCUMENTS, storage_path=storage_path)

    # References rows: FK wi_id has ON DELETE CASCADE (migration 049) but that
    # only fires on hard delete; soft-delete leaks them otherwise. Safe to
    # drop: refs only exist to render the WI; there is no un-delete endpoint.
    try:
        supabase.table("workspace_item_references").delete().eq("wi_id", item_id).execute()
    except Exception as e:  # noqa: BLE001 — best effort
        logger.warning("delete_workspace_item: reference cleanup failed for %s: %s", item_id, e)
```

`cancel_storage_object` (`upload_session_service.py:270-294`) already logs WARNING and never raises — exactly the required contract. Out of scope but flagged: `retrieval_artifacts` backrefs — leave for the sweeper (Fix 6); they're rows, not bytes, possibly useful for telemetry.

## Fix 4 — Legacy upload orphan fix

**Anchors:** `document_service.py:119-230` (`upload_document`), `documents.py:52-63` (handler), workspace twin at `workspace.py:351-446`.

**Decision: insert-DB-row-first with the `upload_status` marker (not marker-only).**

- Marker-only does *not* fix the orphan: the failure mode is "storage write succeeded, DB insert failed" — no row for the reconciler to find. The marker only helps if the row exists before the bytes do.
- Insert-first exactly mirrors the resumable flow (`init_document_upload` → bytes → promote), so the existing reconciler covers the legacy path with **zero reconciler changes**. Failure modes collapse to: (a) insert fails → nothing written anywhere; (b) storage write fails → best-effort soft-delete the row, reconciler as backstop; (c) promote fails → reconciler promotes within 24h.

New service shape (sync, takes bytes):

```python
def upload_document_bytes(supabase, auth_id, case_id, *, file_bytes, filename,
                          content_type, conversation_id=None) -> dict:
    """Legacy single-shot upload, reordered insert-first so the daily
    reconciler covers the crash window (mirrors the resumable flow)."""
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)
    # ... MIME / size / magic checks unchanged (operate on file_bytes) ...

    # 1. Insert the row FIRST, marked uploading (reconciler-visible).
    doc_data = {
        "case_id": case_id, "document_name": filename, "mime_type": content_type,
        "file_size_bytes": len(file_bytes), "storage_path": storage_path,
        "extraction_status": "pending",
        "extracted_data": {
            "upload_status": "uploading",
            "declared_size_bytes": len(file_bytes),
            "declared_mime_type": content_type,
            "upload_init_at": now_iso,
            "legacy_single_shot": True,
        },
    }
    result = supabase.table("case_documents").insert(doc_data).execute()
    doc = result.data[0]; document_id = doc["document_id"]

    # 2. Storage write. On failure: best-effort cancel (soft-delete row);
    #    reconciler is the backstop if even that fails.
    try:
        upload_file(bucket, storage_path, file_bytes, content_type, supabase=supabase)
    except Exception as e:
        logger.exception("Storage upload failed: %s", e)
        try:
            cancel_document_upload(supabase, auth_id, document_id)
        except Exception:  # noqa: BLE001
            logger.warning("legacy upload: cancel after storage failure also "
                           "failed for %s — reconciler will sweep", document_id)
        raise LunaHTTPException(status_code=500, code=ErrorCode.DOC_UPLOAD_FAILED,
                                detail="حدث خطأ أثناء رفع الملف")

    # 3. Promote to ready. On failure: leave as 'uploading' — reconciler
    #    verifies the (good) bytes within 24h and promotes.
    extracted = dict(doc["extracted_data"])
    extracted["upload_status"] = "ready"
    extracted["upload_finalized_at"] = datetime.now(timezone.utc).isoformat()
    update = (supabase.table("case_documents")
              .update({"extracted_data": extracted, "updated_at": extracted["upload_finalized_at"]})
              .eq("document_id", document_id).execute())
    write_audit_log(...)  # unchanged
    return update.data[0] if update.data else doc
```

**Threading split** (coordinate with design 1's `run_db`): `UploadFile.read` is async (Starlette offloads internally), so the read belongs in the handler; everything else is sync supabase work in a thread:

```python
@router.post("/cases/{case_id}/documents", response_model=DocumentResponse)
async def upload_document(case_id: str, file: UploadFile = File(...), ...):
    validate_uuid(case_id, "معرف القضية")
    # Async chunked read — never blocks the loop, enforces the 50 MB cap.
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_FILE_SIZE:
            raise LunaHTTPException(400, ErrorCode.DOC_TOO_LARGE, ...)
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    # All sync Supabase/storage round-trips off the event loop.
    doc = await asyncio.to_thread(
        document_service.upload_document_bytes,
        supabase, user.auth_id, case_id,
        file_bytes=file_bytes,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream")
    return doc
```

Apply the identical pattern to the workspace twin `upload_workspace_attachment` (`workspace.py:382-398` currently calls `file.file.read()` sync on the loop) — its insert-first version writes `metadata.upload_status` exactly like `init_attachment_upload` (`workspace_service.py:447-457`), again giving free reconciler coverage.

Visible-window note: between insert and promote, the doc row exists with `upload_status='uploading'` and could briefly appear in `GET /cases/{id}/documents`. Window is milliseconds in the happy path; acceptable, or filter `uploading` rows in `list_documents` if product cares.

## Fix 5 — Reconciler catch-up on startup

**Anchor:** `backend/app/main.py:114-139`.

**Decision: always run once on startup with a 60s jitter delay; no `job_state` table.** The reconciler is verified idempotent and cheap — two narrow SELECTs acting on a handful of stuck rows. A `job_state` table buys nothing except skipping a near-free query per deploy, at the cost of a migration and one more thing that can lie. Railway restarts a few times a day worst case; extra sweeps are noise. The 60s delay lets the app warm; jitter avoids thundering-herd if replicas ever exist.

```python
    # 4b. Startup catch-up sweep. The 03:15 cron silently skips a day if the
    #     process restarts across it; the reconciler is idempotent and cheap,
    #     so just run it once shortly after every boot.
    import random
    from apscheduler.triggers.date import DateTrigger

    scheduler.add_job(
        _run_upload_reconciler,   # same wrapper as the cron job (to_thread + swallow)
        trigger=DateTrigger(run_date=datetime.now(timezone.utc)
                            + timedelta(seconds=60 + random.uniform(0, 30))),
        id="upload_reconciler_startup",
        replace_existing=True)
```

Worst-case race: startup sweep and 03:15 cron overlap — both act on the same `uploading` row; finalize is idempotent (`document_service.py:424-427` fast path) and `cancel_storage_object` swallows already-deleted errors. Safe.

## Fix 6 — Orphan sweeper (backlog sketch only)

Weekly APScheduler cron (Sunday 04:00 UTC), `backend/app/services/storage_orphan_sweeper.py`:

1. **Collect DB truth set:** page through `case_documents.storage_path` (ALL rows incl. soft-deleted) and `workspace_items.storage_path WHERE storage_path IS NOT NULL` — `range()` batches of 1000 into a Python `set`.
2. **List bucket objects:** use `list_v2(SearchV2Options{limit, cursor, with_delimiter: False})` (POST `/object/list-v2`, cursor pagination, flat recursive listing); loop on `cursor` until exhausted. Fallback: recursive `list()` walk if storage-api predates list-v2.
3. **Delete rule:** object key not in DB set **and** older than 48h (protects in-flight resumable uploads; 48h > the 24h reconciler window). Batch `remove()` in chunks of 100.
4. **Safety rails for first rollout:** dry-run mode behind a settings flag; Logfire span with `listed/orphaned/deleted` counts; hard cap (refuse to delete if orphans > 20% of listed objects — likely a truth-set query failure, not real orphans). **Abort the sweep on any truth-set query exception** — an empty truth set + a working list would delete everything.

---

## Risks

- **Range-request compat:** verify against the live project once (curl above). Degrades gracefully: 200 → bounded streamed read; 416/501 → legacy full download; timeout → `UPLOAD_NOT_COMPLETE`, client retries.
- **`SyncClientOptions` import path:** semi-public; guard import.
- **Insert-first window:** `uploading` row briefly visible in lists.
- **Reconciler double-run:** verified safe.
- **References hard-delete makes soft-delete irreversible** for agent_search items. No restore endpoint exists; document in docstring.
- **Storage timeout 60s** is a hang ceiling, not a latency target; drop to 10s once legacy upload is deprecated.

## Verification

1. **Mid-flight kill (legacy upload):** start a 40 MB upload, kill network after the DB insert log but before "Uploaded file to". Expect: 500 to client, row soft-deleted or left `uploading`; run reconciler → row cancelled, no storage object. Repeat killing between storage write and promote → reconciler promotes to `ready` and doc is downloadable.
2. **Delete attachment, check bucket:** upload via resumable flow, `DELETE /api/v1/workspace/{item_id}`, then `info(storage_path)` → 404, and `SELECT count(*) FROM workspace_item_references WHERE wi_id=...` → 0. Delete an agent_search item (no storage_path) → 204, refs gone, no storage call.
3. **Range read:** finalize a real PDF with httpx DEBUG logging — assert `Range: bytes=0-3` request, 206 response, `len(body)==4`; finalize latency for 50 MB drops from seconds to <100 ms.
4. **Startup catch-up:** seed fake `uploading` row backdated 25h with no storage object, restart backend, wait ~90s → reconcile span with `deleted=1`.
5. **Timeout:** blackhole `SUPABASE_URL` in scratch env, call `head_object` — raises within bounded time, not never.

## Critical files
- `shared/storage/client.py` (Fix 1)
- `shared/db/client.py` (Fix 2 — **coordinate with design 1, foundation owns the file**)
- `backend/app/services/workspace_service.py` (Fix 3)
- `backend/app/services/document_service.py` + `backend/app/api/documents.py` + `backend/app/api/workspace.py` (Fix 4)
- `backend/app/main.py` (Fix 5)
