"""
Memory business logic.
CRUD for case memories (manual creation; auto-extraction is future).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.services.case_service import get_user_id

logger = logging.getLogger(__name__)

_VALID_MEMORY_TYPES = {"fact", "document_reference", "strategy", "deadline", "party_info"}


def _verify_case_ownership(supabase: SupabaseClient, case_id: str, user_id: str) -> None:
    """Verify case exists and belongs to user."""
    try:
        result = (
            supabase.table("lawyer_cases")
            .select("case_id")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying case ownership: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="القضية غير موجودة")


def _verify_memory_ownership(supabase: SupabaseClient, memory_id: str, user_id: str) -> dict:
    """Verify memory exists and belongs to user's case. Returns memory row."""
    try:
        result = (
            supabase.table("case_memories")
            .select("*, lawyer_cases!inner(lawyer_user_id)")
            .eq("memory_id", memory_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying memory ownership: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="الذاكرة غير موجودة")

    if result.data.get("lawyer_cases", {}).get("lawyer_user_id") != user_id:
        raise HTTPException(status_code=404, detail="الذاكرة غير موجودة")

    return result.data


def list_memories(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    memory_type: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """List memories for a case with optional type filter."""
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)

    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    try:
        query = (
            supabase.table("case_memories")
            .select("*", count="exact")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        if memory_type and memory_type != "all":
            if memory_type not in _VALID_MEMORY_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"نوع الذاكرة غير صالح. الأنواع المسموحة: {', '.join(_VALID_MEMORY_TYPES)}",
                )
            query = query.eq("memory_type", memory_type)

        result = query.execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing memories: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب الذاكرة")

    return {
        "memories": result.data or [],
        "total": result.count or 0,
    }


def create_memory(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    memory_type: str,
    content_ar: str,
) -> dict:
    """Create a new memory for a case."""
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)

    if memory_type not in _VALID_MEMORY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"نوع الذاكرة غير صالح. الأنواع المسموحة: {', '.join(_VALID_MEMORY_TYPES)}",
        )

    try:
        result = (
            supabase.table("case_memories")
            .insert({
                "case_id": case_id,
                "memory_type": memory_type,
                "content_ar": content_ar,
            })
            .execute()
        )
    except Exception as e:
        logger.exception("Error creating memory: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء الذاكرة")

    if not result.data:
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء الذاكرة")

    return result.data[0]


def update_memory(
    supabase: SupabaseClient,
    auth_id: str,
    memory_id: str,
    *,
    content_ar: Optional[str] = None,
    memory_type: Optional[str] = None,
) -> dict:
    """Update a memory's content or type."""
    user_id = get_user_id(supabase, auth_id)
    _verify_memory_ownership(supabase, memory_id, user_id)

    update_data: dict = {}
    if content_ar is not None:
        update_data["content_ar"] = content_ar
    if memory_type is not None:
        if memory_type not in _VALID_MEMORY_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"نوع الذاكرة غير صالح. الأنواع المسموحة: {', '.join(_VALID_MEMORY_TYPES)}",
            )
        update_data["memory_type"] = memory_type

    if not update_data:
        raise HTTPException(status_code=400, detail="لم يتم تقديم أي بيانات للتحديث")

    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("case_memories")
            .update(update_data)
            .eq("memory_id", memory_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating memory: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحديث الذاكرة")

    if not result.data:
        raise HTTPException(status_code=404, detail="الذاكرة غير موجودة")

    return result.data[0]


def delete_memory(
    supabase: SupabaseClient,
    auth_id: str,
    memory_id: str,
) -> None:
    """Soft-delete a memory."""
    user_id = get_user_id(supabase, auth_id)
    _verify_memory_ownership(supabase, memory_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("case_memories").update({
            "deleted_at": now,
            "updated_at": now,
        }).eq("memory_id", memory_id).execute()
    except Exception as e:
        logger.exception("Error deleting memory: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء حذف الذاكرة")
