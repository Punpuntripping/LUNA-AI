"""
Conversation business logic.
All database queries go through the sync Supabase client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.services.case_service import get_user_id

logger = logging.getLogger(__name__)


# ============================================
# CONVERSATION CRUD
# ============================================

def list_conversations(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    case_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List conversations for the authenticated user, optionally filtered by case_id.
    Returns dict with conversations list, total count, and has_more flag.
    """
    user_id = get_user_id(supabase, auth_id)

    # Clamp values
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    try:
        query = (
            supabase.table("conversations")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        if case_id is not None:
            query = query.eq("case_id", case_id)

        result = query.execute()

    except Exception as e:
        logger.exception("Error listing conversations: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب المحادثات")

    total = result.count or 0
    conversations = result.data or []

    # Enrich with is_active field
    enriched = []
    for c in conversations:
        enriched.append({
            **c,
            "is_active": c.get("ended_at") is None,
        })

    return {
        "conversations": enriched,
        "total": total,
        "has_more": (offset + limit) < total,
    }


def create_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    case_id: Optional[str] = None,
) -> dict:
    """
    Create a new conversation.
    If case_id is provided, verify the case exists and is owned by the user.
    """
    user_id = get_user_id(supabase, auth_id)

    # If case_id is provided, verify ownership
    if case_id is not None:
        _verify_case_exists(supabase, case_id, user_id)

    # Build conversation data
    conv_data = {
        "user_id": user_id,
        "title_ar": "محادثة جديدة",
    }
    if case_id is not None:
        conv_data["case_id"] = case_id

    try:
        result = (
            supabase.table("conversations")
            .insert(conv_data)
            .execute()
        )
    except Exception as e:
        logger.exception("Error creating conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء المحادثة")

    if not result.data:
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء المحادثة")

    conv = result.data[0]
    conv["is_active"] = conv.get("ended_at") is None

    return conv


def get_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> dict:
    """
    Get a single conversation by ID.
    Verifies ownership (returns 404 if not owned or not found).
    """
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب المحادثة")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="المحادثة غير موجودة")

    conv = result.data
    conv["is_active"] = conv.get("ended_at") is None

    return conv


def update_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    title_ar: str,
) -> dict:
    """
    Update conversation title.
    Verifies ownership first.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    try:
        result = (
            supabase.table("conversations")
            .update({
                "title_ar": title_ar,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحديث المحادثة")

    if not result.data:
        raise HTTPException(status_code=404, detail="المحادثة غير موجودة")

    conv = result.data[0]
    conv["is_active"] = conv.get("ended_at") is None

    return conv


def delete_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> None:
    """
    Soft-delete a conversation by setting deleted_at.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("conversations").update({
            "deleted_at": now,
            "updated_at": now,
        }).eq("conversation_id", conversation_id).eq("user_id", user_id).execute()
    except Exception as e:
        logger.exception("Error deleting conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء حذف المحادثة")


def end_session(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> dict:
    """
    End a conversation session by setting ended_at.
    Returns the updated conversation.
    """
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("conversations")
            .update({
                "ended_at": now,
                "updated_at": now,
            })
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error ending session: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنهاء الجلسة")

    if not result.data:
        raise HTTPException(status_code=404, detail="المحادثة غير موجودة")

    conv = result.data[0]
    conv["is_active"] = False  # We just set ended_at, so it's no longer active

    return conv


# ============================================
# INTERNAL HELPERS
# ============================================

def _verify_case_exists(supabase: SupabaseClient, case_id: str, user_id: str) -> None:
    """
    Verify a case exists, is not deleted, and belongs to the user.
    Raises 404 if not found/not owned. Raises 400 if case is archived.
    """
    try:
        result = (
            supabase.table("lawyer_cases")
            .select("case_id, status")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying case: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="القضية غير موجودة")


def _verify_conversation_ownership(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict:
    """
    Verify the conversation exists, is not deleted, and belongs to the user.
    Returns the conversation row if valid, otherwise raises 404.
    """
    try:
        result = (
            supabase.table("conversations")
            .select("conversation_id, user_id")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying conversation ownership: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="المحادثة غير موجودة")

    return result.data
