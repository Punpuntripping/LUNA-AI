"""
Artifact business logic.
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
# ARTIFACT CRUD
# ============================================

def create_artifact(
    supabase: SupabaseClient,
    user_id: str,
    *,
    conversation_id: Optional[str] = None,
    case_id: Optional[str] = None,
    agent_family: str,
    artifact_type: str,
    title: str,
    content_md: str = "",
    is_editable: bool = False,
    metadata: Optional[dict] = None,
) -> dict:
    """Create a new artifact. Called by agents during execution (uses user_id directly)."""
    payload = {
        "user_id": user_id,
        "agent_family": agent_family,
        "artifact_type": artifact_type,
        "title": title,
        "content_md": content_md,
        "is_editable": is_editable,
        "metadata": metadata or {},
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if case_id is not None:
        payload["case_id"] = case_id

    try:
        result = supabase.table("artifacts").insert(payload).execute()
    except Exception as e:
        logger.exception("Error creating artifact: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء المستند")

    if not result.data:
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء إنشاء المستند")

    return result.data[0]


def list_artifacts_by_conversation(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
) -> list[dict]:
    """List artifacts for a conversation. Ownership verified via user_id."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("artifacts")
            .select("*")
            .eq("user_id", user_id)
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing artifacts by conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب المستندات")

    return result.data or []


def list_artifacts_by_case(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
) -> list[dict]:
    """List artifacts for a case. Ownership verified via user_id."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("artifacts")
            .select("*")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing artifacts by case: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب المستندات")

    return result.data or []


def get_artifact(
    supabase: SupabaseClient,
    auth_id: str,
    artifact_id: str,
) -> dict:
    """Get single artifact. Returns 404 if not found or not owned."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("artifacts")
            .select("*")
            .eq("artifact_id", artifact_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching artifact: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب المستند")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="المستند غير موجود")

    return result.data


def update_artifact(
    supabase: SupabaseClient,
    auth_id: str,
    artifact_id: str,
    *,
    content_md: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Update artifact content/title. Only allowed if is_editable=True."""
    user_id = get_user_id(supabase, auth_id)

    # Fetch artifact to check editability
    existing = get_artifact(supabase, auth_id, artifact_id)
    if not existing.get("is_editable"):
        raise HTTPException(status_code=403, detail="لا يمكن تعديل هذا المستند")

    update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if content_md is not None:
        update_data["content_md"] = content_md
    if title is not None:
        update_data["title"] = title

    if len(update_data) == 1:
        raise HTTPException(status_code=400, detail="لم يتم تقديم أي بيانات للتحديث")

    try:
        result = (
            supabase.table("artifacts")
            .update(update_data)
            .eq("artifact_id", artifact_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating artifact: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحديث المستند")

    if not result.data:
        raise HTTPException(status_code=404, detail="المستند غير موجود")

    return result.data[0]


def delete_artifact(
    supabase: SupabaseClient,
    auth_id: str,
    artifact_id: str,
) -> None:
    """Soft delete artifact (set deleted_at)."""
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("artifacts")
            .update({"deleted_at": now, "updated_at": now})
            .eq("artifact_id", artifact_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error deleting artifact: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء حذف المستند")

    if not result.data:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
