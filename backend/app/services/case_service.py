"""
Case business logic.
All database queries go through the sync Supabase client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.audit_service import write_audit_log
from shared.types import CaseType, CaseStatus, CasePriority

logger = logging.getLogger(__name__)

# Valid enum values for validation
_VALID_CASE_TYPES = {e.value for e in CaseType}
_VALID_CASE_STATUSES = {e.value for e in CaseStatus}
_VALID_PRIORITIES = {e.value for e in CasePriority}


# ============================================
# HELPERS
# ============================================

def get_user_id(supabase: SupabaseClient, auth_id: str) -> str:
    """
    Look up the internal user_id from the Supabase auth_id.
    AuthUser.auth_id maps to users.auth_id, NOT users.user_id.

    Raises:
        HTTPException 401: if user profile not found (should not happen for authenticated users).
    """
    try:
        result = (
            supabase.table("users")
            .select("user_id")
            .eq("auth_id", auth_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error looking up user_id for auth_id=%s: %s", auth_id, e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=401, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود")

    return result.data["user_id"]


def _validate_case_type(case_type: str) -> str:
    """Validate case_type against allowed enum values."""
    if case_type not in _VALID_CASE_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.CASE_INVALID_TYPE,
            detail=f"نوع القضية غير صالح. الأنواع المسموحة: {', '.join(_VALID_CASE_TYPES)}",
        )
    return case_type


def _validate_priority(priority: str) -> str:
    """Validate priority against allowed enum values."""
    if priority not in _VALID_PRIORITIES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.CASE_INVALID_PRIORITY,
            detail=f"الأولوية غير صالحة. القيم المسموحة: {', '.join(_VALID_PRIORITIES)}",
        )
    return priority


def _validate_status(status: str) -> str:
    """Validate status against allowed enum values."""
    if status not in _VALID_CASE_STATUSES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.CASE_INVALID_STATUS,
            detail=f"حالة القضية غير صالحة. القيم المسموحة: {', '.join(_VALID_CASE_STATUSES)}",
        )
    return status


# ============================================
# CASE CRUD
# ============================================

def list_cases(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """
    List cases for the authenticated user with pagination.
    Returns dict with cases list, total count, page, and per_page.
    """
    user_id = get_user_id(supabase, auth_id)

    # Clamp pagination values
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    offset = (page - 1) * per_page

    try:
        # Build the query
        query = (
            supabase.table("lawyer_cases")
            .select("*", count="exact")
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + per_page - 1)
        )

        if status:
            _validate_status(status)
            query = query.eq("status", status)

        result = query.execute()

    except LunaHTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing cases: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب القضايا")

    total = result.count or 0
    cases = result.data or []

    # Batch-fetch conversation + document counts in ONE round-trip (RPC
    # case_counts, migration 065) instead of the old 1+2N per-case loop.
    counts: dict[str, dict] = {}
    if cases:
        try:
            counts_result = supabase.rpc(
                "case_counts",
                {"p_case_ids": [c["case_id"] for c in cases]},
            ).execute()
            counts = {row["case_id"]: row for row in (counts_result.data or [])}
        except Exception as e:
            logger.exception("Error fetching case counts: %s", e)
            raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب القضايا")

    enriched = [
        {
            **case,
            "conversation_count": counts.get(case["case_id"], {}).get("conversation_count", 0),
            "document_count": counts.get(case["case_id"], {}).get("document_count", 0),
        }
        for case in cases
    ]

    return {
        "cases": enriched,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


def create_case(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    case_name: str,
    case_type: str = "عام",
    description: Optional[str] = None,
    case_number: Optional[str] = None,
    court_name: Optional[str] = None,
    priority: str = "medium",
) -> dict:
    """
    Create a new case and an initial conversation for it.
    Returns dict with case data and first_conversation_id.
    """
    user_id = get_user_id(supabase, auth_id)
    _validate_case_type(case_type)
    _validate_priority(priority)

    # Atomic insert of the case + its first conversation in one transaction
    # (RPC, migration 065). A failure between the two writes can no longer
    # leave an orphaned case with zero conversations.
    try:
        result = supabase.rpc(
            "create_case_with_conversation",
            {
                "p_user_id": user_id,
                "p_case_name": case_name,
                "p_case_type": case_type,
                "p_description": description,
                "p_case_number": case_number,
                "p_court_name": court_name,
                "p_priority": priority,
            },
        ).execute()
    except Exception as e:
        logger.exception("Error creating case: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء القضية")

    payload = result.data
    if not payload or not payload.get("case"):
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء القضية")

    case = payload["case"]
    first_conversation_id = payload.get("first_conversation_id")

    # Audit log stays OUTSIDE the transaction (best-effort by design).
    write_audit_log(
        supabase,
        user_id=user_id,
        action="create",
        resource_type="case",
        resource_id=case["case_id"],
    )

    return {
        "case": {
            **case,
            "conversation_count": 1,
            "document_count": 0,
        },
        "first_conversation_id": first_conversation_id,
    }


def get_case_detail(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
) -> dict:
    """
    Get full case detail with conversations and stats.
    Verifies ownership (returns 404 if not owned or not found).
    """
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("lawyer_cases")
            .select("*")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching case detail: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب تفاصيل القضية")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")

    case = result.data

    # Fetch conversations for this case
    try:
        conv_result = (
            supabase.table("conversations")
            .select("*")
            .eq("case_id", case_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        # A detail page with fabricated stats is worse than a retryable error.
        logger.exception("Error fetching case conversations: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب تفاصيل القضية")

    conv_result_data = conv_result.data or []

    # Build stats. Document count comes from the batched case_counts RPC
    # (migration 065); conversation count is exact from the fetched rows.
    total_conversations = len(conv_result_data)
    counts = _case_counts(supabase, case_id)
    total_documents = counts.get("document_count", 0)
    total_memories = _count_memories(supabase, case_id)

    # Enrich conversations with is_active field
    conversations = []
    for c in conv_result_data:
        conversations.append({
            **c,
            "is_active": c.get("ended_at") is None,
        })

    return {
        "case": {
            **case,
            "conversation_count": total_conversations,
            "document_count": total_documents,
        },
        "conversations": conversations,
        "stats": {
            "total_conversations": total_conversations,
            "total_documents": total_documents,
            "total_memories": total_memories,
        },
    }


def update_case(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    case_name: Optional[str] = None,
    case_type: Optional[str] = None,
    description: Optional[str] = None,
    case_number: Optional[str] = None,
    court_name: Optional[str] = None,
    priority: Optional[str] = None,
) -> dict:
    """
    Update case fields. Only non-None fields are updated.
    Verifies ownership first.
    """
    user_id = get_user_id(supabase, auth_id)

    # Verify ownership
    _verify_case_ownership(supabase, case_id, user_id)

    # Build update payload from non-None fields
    update_data = {}
    if case_name is not None:
        update_data["case_name"] = case_name
    if case_type is not None:
        _validate_case_type(case_type)
        update_data["case_type"] = case_type
    if description is not None:
        update_data["description"] = description
    if case_number is not None:
        update_data["case_number"] = case_number
    if court_name is not None:
        update_data["court_name"] = court_name
    if priority is not None:
        _validate_priority(priority)
        update_data["priority"] = priority

    if not update_data:
        raise LunaHTTPException(status_code=400, code=ErrorCode.NO_UPDATE_DATA, detail="لم يتم تقديم أي بيانات للتحديث")

    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("lawyer_cases")
            .update(update_data)
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating case: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث القضية")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")

    case = result.data[0]

    counts = _case_counts(supabase, case_id)

    return {
        **case,
        "conversation_count": counts.get("conversation_count", 0),
        "document_count": counts.get("document_count", 0),
    }


def update_case_status(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    status: str,
) -> dict:
    """
    Update a case's status (active, closed, archived).
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)
    _validate_status(status)

    try:
        result = (
            supabase.table("lawyer_cases")
            .update({
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating case status: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث حالة القضية")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")

    case = result.data[0]

    counts = _case_counts(supabase, case_id)

    return {
        **case,
        "conversation_count": counts.get("conversation_count", 0),
        "document_count": counts.get("document_count", 0),
    }


def delete_case(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
) -> None:
    """
    Soft-delete a case by setting deleted_at.
    Also soft-deletes all conversations under this case.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)

    # Atomic cascade soft-delete (RPC soft_delete_case_cascade, migration
    # 065). The case + all its live conversations are soft-deleted in one
    # transaction, closing the verify→delete TOCTOU window. The RPC returns
    # -1 when the case was not found/owned (e.g. deleted concurrently between
    # the ownership check and this call) → map to 404.
    try:
        result = supabase.rpc(
            "soft_delete_case_cascade",
            {"p_case_id": case_id, "p_user_id": user_id},
        ).execute()
    except Exception as e:
        logger.exception("Error deleting case: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء حذف القضية")

    # PostgREST returns the scalar INT either bare or list-wrapped depending
    # on the function shape — normalize defensively before the -1 check.
    data = result.data
    if isinstance(data, list):
        data = data[0] if data else None
    if data == -1:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")

    write_audit_log(
        supabase,
        user_id=user_id,
        action="delete",
        resource_type="case",
        resource_id=case_id,
    )


# ============================================
# INTERNAL HELPERS
# ============================================

def _verify_case_ownership(supabase: SupabaseClient, case_id: str, user_id: str) -> dict:
    """
    Verify the case exists, is not deleted, and belongs to the user.
    Returns the case row if valid, otherwise raises 404.
    """
    try:
        result = (
            supabase.table("lawyer_cases")
            .select("case_id, lawyer_user_id")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying case ownership: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")

    return result.data


def _case_counts(supabase: SupabaseClient, case_id: str) -> dict:
    """Fetch the non-deleted conversation + document counts for one case.

    Uses the batched ``case_counts`` RPC (migration 065) — one round-trip.
    Returns a dict with ``conversation_count`` and ``document_count`` keys.
    On RPC failure, propagates a 500 (no more silent fake-zero counts).
    """
    try:
        result = supabase.rpc("case_counts", {"p_case_ids": [case_id]}).execute()
    except Exception as e:
        logger.exception("Error fetching case counts: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب تفاصيل القضية")

    rows = result.data or []
    if rows:
        return rows[0]
    return {"conversation_count": 0, "document_count": 0}


def _count_memories(supabase: SupabaseClient, case_id: str) -> int:
    """Count non-deleted memories for a case.

    Propagates errors as a 500 (no silent fake-zero) so a detail page never
    serves a fabricated memory stat as truth.
    """
    try:
        result = (
            supabase.table("case_memories")
            .select("memory_id", count="exact")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error counting memories for case_id=%s: %s", case_id, e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب تفاصيل القضية")

    return result.count or 0
