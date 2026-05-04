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
from backend.app.services.case_service import get_user_id

logger = logging.getLogger(__name__)


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
                    detail="Luna يحرر هذا الملف الآن، انتظر لحظة",
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
) -> dict:
    """Create a new workspace_item row.

    ``kind`` and ``created_by`` are required and are written directly to the
    new columns. The legacy ``artifact_type`` / ``is_editable`` fields are
    gone; subtype information now lives in ``metadata`` only.

    For ``kind='attachment'`` callers must pass either ``storage_path`` or
    ``document_id``; for every other kind, ``content_md`` is required. The DB
    CHECK constraint (migration 026) enforces this -- the function does not
    duplicate the check, but callers are warned via the docstring.
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
) -> list[dict]:
    """List workspace items for a conversation. Ownership verified via user_id."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("workspace_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing workspace_items by conversation: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب عناصر مساحة العمل",
        )

    return result.data or []


def list_workspace_items_by_case(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
) -> list[dict]:
    """List workspace items for a case. Ownership verified via user_id."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("workspace_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing workspace_items by case: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب عناصر مساحة العمل",
        )

    return result.data or []


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
    """Soft delete (set deleted_at)."""
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

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
]
