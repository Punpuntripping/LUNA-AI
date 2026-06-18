"""
Workspace business logic (post-migration 026).

This module replaces ``backend.app.services.artifact_service`` and targets
the post-rename schema:

    table:   ``workspace_items``
    PK:      ``item_id``
    columns added: ``kind``, ``created_by``, ``storage_path``, ``document_id``,
                   ``is_visible``, ``locked_by_agent_until``
    columns dropped: ``artifact_type``, ``is_editable``

Permission semantics are deterministic from ``kind`` -- see ``USER_EDITABLE``.
Lock semantics use the real ``locked_by_agent_until`` column (Cut-1's
``metadata.locked_until`` stopgap is no longer consulted here).

All database queries go through the sync Supabase client. All error messages
are Arabic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services import upload_session_service
from backend.app.services.case_service import get_user_id
from shared.config import get_settings
from shared.storage.client import build_storage_path, upload_file

logger = logging.getLogger(__name__)

# Content validation rules for the legacy single-shot attachment upload —
# mirror the case_documents rules so workspace attachments behave identically.
_ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
_MAGIC_BYTES = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
}
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ============================================
# PERMISSION TABLE -- single source of truth
# ============================================

# Kinds the end-user is allowed to edit. ``agent_writing`` is editable but
# subject to the lock check (see ``_assert_writable``).
USER_EDITABLE: set[str] = {"note", "agent_writing"}

# Kinds for which we check ``locked_by_agent_until`` before accepting an edit.
AGENT_LOCK_APPLIES: set[str] = {"agent_writing"}


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO8601 string returned by Supabase. ``None`` on failure."""
    if not value:
        return None
    try:
        # Supabase emits e.g. ``2026-05-01T12:34:56.789012+00:00``.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _assert_writable(item: dict) -> None:
    """Raise if the user is not allowed to mutate this item right now.

    Two layers:
        1. Kind must be in ``USER_EDITABLE``.
        2. For ``agent_writing``, the lock must not be currently held by the
           agent (``locked_by_agent_until > now()``).
    """
    kind = item.get("kind")
    if kind not in USER_EDITABLE:
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.ARTIFACT_NOT_EDITABLE,
            detail="هذا العنصر غير قابل للتعديل",
        )

    if kind in AGENT_LOCK_APPLIES:
        locked_raw = item.get("locked_by_agent_until")
        if locked_raw:
            locked_dt = _parse_iso(locked_raw)
            if locked_dt and datetime.now(timezone.utc) < locked_dt:
                raise LunaHTTPException(
                    status_code=409,
                    code=ErrorCode.ARTIFACT_NOT_EDITABLE,
                    detail="ريحان يحرر هذا الملف الآن، انتظر لحظة",
                )


# ============================================
# WORKSPACE ITEM CRUD
# ============================================


def create_workspace_item(
    supabase: SupabaseClient,
    user_id: str,
    *,
    kind: str,
    created_by: str,
    title: str,
    conversation_id: Optional[str] = None,
    case_id: Optional[str] = None,
    message_id: Optional[str] = None,
    agent_family: Optional[str] = None,
    content_md: Optional[str] = None,
    storage_path: Optional[str] = None,
    document_id: Optional[str] = None,
    is_visible: bool = True,
    metadata: Optional[dict] = None,
    describe_query: Optional[str] = None,
) -> dict:
    """Create a new workspace_item row.

    ``kind`` and ``created_by`` are required and are written directly to the
    new columns. The legacy ``artifact_type`` / ``is_editable`` fields are
    gone; subtype information now lives in ``metadata`` only.

    For ``kind='attachment'`` callers must pass either ``storage_path`` or
    ``document_id``; for every other kind, ``content_md`` is required. The DB
    CHECK constraint (migration 026) enforces this -- the function does not
    duplicate the check, but callers are warned via the docstring.

    ``describe_query`` is the router-emitted description of the user's query
    (typically 50–150 words). Persisted to the ``workspace_items.describe_query``
    column (migration 038). NULL for user-authored kinds (note, attachment).
    """
    payload: dict = {
        "user_id": user_id,
        "kind": kind,
        "created_by": created_by,
        "title": title,
        "is_visible": is_visible,
        "metadata": metadata or {},
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if case_id is not None:
        payload["case_id"] = case_id
    if message_id is not None:
        payload["message_id"] = message_id
    if agent_family is not None:
        payload["agent_family"] = agent_family
    if content_md is not None:
        payload["content_md"] = content_md
    if storage_path is not None:
        payload["storage_path"] = storage_path
    if document_id is not None:
        payload["document_id"] = document_id
    if describe_query is not None:
        payload["describe_query"] = describe_query

    try:
        result = supabase.table("workspace_items").insert(payload).execute()
    except Exception as e:
        logger.exception("Error creating workspace_item: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إنشاء العنصر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إنشاء العنصر",
        )

    return result.data[0]


def list_workspace_items_by_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List workspace items for a conversation. Ownership verified via user_id.

    Returns ``(items, total)`` where ``total`` is the true non-deleted count
    via ``count="exact"`` so clients can detect truncation. ``limit`` defaults
    to 100 (preserving the previous one-page behaviour for normal users) and is
    clamped to ``[1, 200]``; ``offset`` is clamped to ``>= 0``.
    """
    user_id = get_user_id(supabase, auth_id)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        result = (
            supabase.table("workspace_items")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing workspace_items by conversation: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب عناصر مساحة العمل",
        )

    return result.data or [], result.count or 0


def list_workspace_items_by_case(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List workspace items for a case. Ownership verified via user_id.

    Returns ``(items, total)`` — see
    ``list_workspace_items_by_conversation`` for the pagination contract.
    """
    user_id = get_user_id(supabase, auth_id)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        result = (
            supabase.table("workspace_items")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing workspace_items by case: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب عناصر مساحة العمل",
        )

    return result.data or [], result.count or 0


def get_workspace_item(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
) -> dict:
    """Get single workspace_item. Returns 404 if not found or not owned."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("workspace_items")
            .select("*")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching workspace_item: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب العنصر",
        )

    if result is None or result.data is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="العنصر غير موجود",
        )

    return result.data


def update_workspace_item(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
    *,
    content_md: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Update item content/title. Permission keyed on ``kind``.

    Raises:
        403 if ``kind`` is not user-editable (e.g. ``agent_search``,
            ``attachment``, ``convo_context``, ``references``).
        409 if ``kind == 'agent_writing'`` and the agent currently holds the
            lock (``locked_by_agent_until > now()``).
    """
    user_id = get_user_id(supabase, auth_id)

    existing = get_workspace_item(supabase, auth_id, item_id)
    _assert_writable(existing)

    update_data: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if content_md is not None:
        update_data["content_md"] = content_md
    if title is not None:
        update_data["title"] = title

    if len(update_data) == 1:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.NO_UPDATE_DATA,
            detail="لم يتم تقديم أي بيانات للتحديث",
        )

    try:
        result = (
            supabase.table("workspace_items")
            .update(update_data)
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating workspace_item: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث العنصر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="العنصر غير موجود",
        )

    return result.data[0]


def update_visibility(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
    *,
    is_visible: bool,
) -> dict:
    """Toggle ``is_visible`` for any kind. Bypasses the kind-permission check
    because visibility is a user UX flag, not content mutation."""
    user_id = get_user_id(supabase, auth_id)

    # Existence + ownership check (raises 404 if not owned).
    get_workspace_item(supabase, auth_id, item_id)

    update_data = {
        "is_visible": is_visible,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = (
            supabase.table("workspace_items")
            .update(update_data)
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating workspace_item visibility: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء تحديث العنصر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="العنصر غير موجود",
        )

    return result.data[0]


def delete_workspace_item(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
) -> None:
    """Soft delete (set ``deleted_at``), then best-effort cleanup of the
    storage object (attachments) and ``workspace_item_references`` rows.

    Cleanup failure NEVER fails the request — the soft-delete is the
    user-visible contract. Note: the references hard-delete makes this
    soft-delete effectively irreversible for agent_search items, and there is
    no restore endpoint; the rows only exist to render the WI.
    """
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

    # The UPDATE returns the full row representation, so read storage_path off
    # it directly — no extra pre-fetch needed.
    try:
        result = (
            supabase.table("workspace_items")
            .update({"deleted_at": now, "updated_at": now})
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error deleting workspace_item: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء حذف العنصر",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="العنصر غير موجود",
        )

    row = result.data[0]

    # --- best-effort cleanup: NEVER raises past this point ------------------
    storage_path = row.get("storage_path")
    if storage_path:
        # cancel_storage_object logs WARNING and never raises.
        settings = get_settings()
        upload_session_service.cancel_storage_object(
            supabase,
            bucket=settings.STORAGE_BUCKET_DOCUMENTS,
            storage_path=storage_path,
        )

    # References rows: FK wi_id → workspace_items(item_id) has ON DELETE
    # CASCADE (migration 049) but that only fires on a HARD delete; this is a
    # soft delete, so the refs would leak otherwise. Safe to drop — refs only
    # exist to render the WI and there is no un-delete endpoint.
    try:
        (
            supabase.table("workspace_item_references")
            .delete()
            .eq("wi_id", item_id)
            .execute()
        )
    except Exception as e:  # noqa: BLE001 — best effort
        logger.warning(
            "delete_workspace_item: reference cleanup failed for %s: %s",
            item_id,
            e,
        )


# ============================================
# RESUMABLE ATTACHMENT UPLOAD — init / finalize / cancel
# ============================================
#
# Mirrors the case-document flow in ``document_service`` but writes into
# ``workspace_items`` with ``kind='attachment'``. Upload state lives in the
# ``metadata`` JSONB (``metadata.upload_status`` ∈ {'uploading','ready','cancelled'}).
# See ``.claude/plans/upload_reliability.md`` for the full design.


def upload_attachment_bytes(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> dict:
    """Legacy single-shot chat-attachment upload, reordered insert-first so the
    daily reconciler covers the crash window (mirrors ``init_attachment_upload``
    → finalize).

    Takes already-read bytes — the async chunked read + 50 MB cap lives in the
    route handler. All content validation (MIME, size, empty, magic bytes) is
    preserved here and operates on ``file_bytes``.

    Ordering: insert the ``kind='attachment'`` row with
    ``metadata.upload_status='uploading'`` FIRST (reconciler-visible), then
    write to storage (best-effort cancel on failure), then promote the marker
    to ``'ready'``. The marker field paths match upload_reconciler exactly:
    ``metadata.upload_status`` on a ``kind='attachment'`` row.
    """
    from backend.app.services.message_service import verify_conversation_ownership

    user_id = get_user_id(supabase, auth_id)
    verify_conversation_ownership(supabase, conversation_id, user_id)

    content_type = content_type or "application/octet-stream"
    if content_type not in _ALLOWED_MIME_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_INVALID_TYPE,
            detail="نوع الملف غير مسموح. الأنواع المسموحة: PDF, PNG, JPG",
        )

    file_size = len(file_bytes)
    if file_size == 0:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_EMPTY,
            detail="الملف فارغ",
        )
    if file_size > _MAX_FILE_SIZE:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_TOO_LARGE,
            detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
        )

    expected_magic = _MAGIC_BYTES.get(content_type)
    if expected_magic and not file_bytes[: len(expected_magic)].startswith(expected_magic):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_MAGIC_MISMATCH,
            detail="محتوى الملف لا يتطابق مع نوعه المعلن",
        )

    filename = filename or "attachment"
    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS
    # Per-conversation prefix (general/{user_id}/convos/{conversation_id}/...).
    storage_path = build_storage_path(None, user_id, conversation_id, filename)

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Insert the row FIRST, marked uploading (reconciler-visible).
    metadata = {
        "filename": filename,
        "mime_type": content_type,
        "file_size_bytes": file_size,
        "upload_status": "uploading",
        "declared_size_bytes": file_size,
        "declared_mime_type": content_type,
        "upload_init_at": now_iso,
        "legacy_single_shot": True,
    }
    row = create_workspace_item(
        supabase,
        user_id,
        kind="attachment",
        created_by="user",
        title=filename,
        conversation_id=conversation_id,
        storage_path=storage_path,
        metadata=metadata,
    )
    item_id = row["item_id"]

    # 2. Storage write. On failure: best-effort cancel (soft-delete row);
    #    the reconciler is the backstop if even that fails.
    try:
        upload_file(bucket, storage_path, file_bytes, content_type, supabase=supabase)
    except (LunaHTTPException,):
        raise
    except Exception as e:
        logger.exception("Workspace attachment upload failed: %s", e)
        try:
            cancel_attachment_upload(supabase, auth_id, item_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "legacy attachment upload: cancel after storage failure also "
                "failed for %s — reconciler will sweep",
                item_id,
            )
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.DOC_UPLOAD_FAILED,
            detail="حدث خطأ أثناء رفع الملف",
        )

    # 3. Promote the marker to ready. On failure: leave 'uploading' — the
    #    reconciler verifies the (good) bytes within 24 h and promotes.
    new_meta = dict(row.get("metadata") or {})
    new_meta["upload_status"] = "ready"
    new_meta["upload_finalized_at"] = datetime.now(timezone.utc).isoformat()

    try:
        update = (
            supabase.table("workspace_items")
            .update({"metadata": new_meta, "updated_at": new_meta["upload_finalized_at"]})
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.warning(
            "legacy attachment upload: promote-to-ready failed for %s: %s — "
            "reconciler will promote",
            item_id,
            e,
        )
        update = None

    if update is not None and update.data:
        return update.data[0]
    return {**row, "metadata": new_meta}


def init_attachment_upload(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    page_count: int | None = None,
) -> dict:
    """Phase 1 of the resumable flow for chat attachments. Reserves a
    ``workspace_items`` row with ``metadata.upload_status='uploading'`` and
    returns the TUS URL the browser will PATCH bytes to.

    Returns:
        ``{"item_id": str, "storage_path": str, "bucket": str,
           "upload_url": str, "expires_at": datetime}``
    """
    # Late import keeps the module import-graph acyclic; message_service
    # owns the canonical conversation-ownership check.
    from backend.app.services.message_service import verify_conversation_ownership

    user_id = get_user_id(supabase, auth_id)
    verify_conversation_ownership(supabase, conversation_id, user_id)

    session = upload_session_service.init_upload(
        supabase,
        user_id=user_id,
        case_id=None,
        conversation_id=conversation_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    metadata = {
        "filename": filename,
        "mime_type": mime_type,
        "file_size_bytes": size_bytes,
        "upload_status": "uploading",
        "declared_size_bytes": size_bytes,
        "declared_mime_type": mime_type,
        "upload_url_expires_at": session["expires_at"].isoformat(),
        "upload_init_at": now_iso,
    }
    # Client-reported page estimate (PDF parsed in-browser; image → 1). Read by
    # the OCR quota gate's upfront projection only; the post-OCR pass overwrites
    # metadata.ocr_pages with the authoritative Mistral count. Clamp defensively
    # against a spoofed body — a lie only buys the same bounded one-message
    # overage the gate already tolerates, since settle bills the real count.
    if page_count is not None:
        try:
            metadata["page_count"] = max(1, min(int(page_count), 10_000))
        except (TypeError, ValueError):
            pass

    row = create_workspace_item(
        supabase,
        user_id,
        kind="attachment",
        created_by="user",
        title=filename,
        conversation_id=conversation_id,
        storage_path=session["path"],
        metadata=metadata,
    )

    return {
        "item_id": row["item_id"],
        "storage_path": session["path"],
        "bucket": session["bucket"],
        "upload_url": session["upload_url"],
        "expires_at": session["expires_at"],
    }


def finalize_attachment_upload(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
) -> dict:
    """Phase 3: confirm the bytes landed for a chat attachment. Flips
    ``metadata.upload_status`` to ``'ready'``. Idempotent on already-ready
    items.
    """
    user_id = get_user_id(supabase, auth_id)
    item = get_workspace_item(supabase, auth_id, item_id)

    if item.get("kind") != "attachment":
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_INVALID_STATE,
            detail="هذا العنصر ليس مرفقاً",
        )

    metadata = dict(item.get("metadata") or {})
    upload_status = metadata.get("upload_status")

    if upload_status in (None, "ready"):
        # Idempotent fast path.
        return item

    if upload_status != "uploading":
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_INVALID_STATE,
            detail="حالة الرفع غير صالحة لإتمام العملية",
        )

    storage_path = item.get("storage_path")
    if not storage_path:
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_INVALID_STATE,
            detail="مسار الملف غير محدد",
        )

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    expected_size = metadata.get("declared_size_bytes") or metadata.get(
        "file_size_bytes"
    )
    expected_mime = metadata.get("declared_mime_type") or metadata.get(
        "mime_type"
    )

    upload_session_service.verify_finalize(
        supabase,
        bucket=bucket,
        storage_path=storage_path,
        expected_size=int(expected_size or 0),
        expected_mime=expected_mime or "application/octet-stream",
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    metadata["upload_status"] = "ready"
    metadata["upload_finalized_at"] = now_iso

    try:
        result = (
            supabase.table("workspace_items")
            .update({"metadata": metadata, "updated_at": now_iso})
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error finalizing attachment upload: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إتمام رفع الملف",
        )

    return result.data[0] if result.data else {**item, "metadata": metadata}


def cancel_attachment_upload(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
) -> None:
    """Phase 3 alternative: user aborted. Soft-delete the workspace_items
    row, mark ``metadata.upload_status='cancelled'``, and best-effort wipe
    the partial storage object. Idempotent — already-deleted rows succeed
    silently."""
    user_id = get_user_id(supabase, auth_id)

    # Inline ownership lookup (bypasses get_workspace_item's deleted_at
    # filter so cancel is idempotent).
    try:
        result = (
            supabase.table("workspace_items")
            .select("*")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching attachment for cancel: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ داخلي",
        )

    if result is None or result.data is None:
        return  # already gone — success per idempotency contract

    item = result.data
    if item.get("kind") != "attachment":
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_INVALID_STATE,
            detail="هذا العنصر ليس مرفقاً",
        )
    if item.get("deleted_at"):
        return  # already soft-deleted

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS
    storage_path = item.get("storage_path")

    now_iso = datetime.now(timezone.utc).isoformat()
    metadata = dict(item.get("metadata") or {})
    metadata["upload_status"] = "cancelled"
    metadata["upload_cancelled_at"] = now_iso

    try:
        supabase.table("workspace_items").update(
            {
                "deleted_at": now_iso,
                "updated_at": now_iso,
                "metadata": metadata,
            }
        ).eq("item_id", item_id).eq("user_id", user_id).execute()
    except Exception as e:
        logger.exception("Error soft-deleting cancelled attachment: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إلغاء الرفع",
        )

    upload_session_service.cancel_storage_object(
        supabase, bucket=bucket, storage_path=storage_path
    )


__all__ = [
    "USER_EDITABLE",
    "AGENT_LOCK_APPLIES",
    "create_workspace_item",
    "list_workspace_items_by_conversation",
    "list_workspace_items_by_case",
    "get_workspace_item",
    "update_workspace_item",
    "update_visibility",
    "delete_workspace_item",
    "upload_attachment_bytes",
    "init_attachment_upload",
    "finalize_attachment_upload",
    "cancel_attachment_upload",
]
