"""
User preferences and templates business logic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.case_service import get_user_id
from shared.types import AgentFamily

logger = logging.getLogger(__name__)

_VALID_AGENT_FAMILIES = {e.value for e in AgentFamily}


# ============================================
# USER PREFERENCES
# ============================================

def get_preferences(
    supabase: SupabaseClient,
    auth_id: str,
) -> dict:
    """Get user preferences. Returns default {} if none exist."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("user_preferences")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching preferences: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء جلب الإعدادات")

    if result is None or result.data is None:
        return {"user_id": user_id, "preferences": {}}

    return result.data


def update_preferences(
    supabase: SupabaseClient,
    auth_id: str,
    preferences: dict,
) -> dict:
    """Upsert user preferences (merge with existing JSONB)."""
    user_id = get_user_id(supabase, auth_id)

    # Try to get existing
    try:
        existing = (
            supabase.table("user_preferences")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error checking existing preferences: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")

    if existing is not None and existing.data is not None:
        # Merge with existing preferences
        merged = {**existing.data.get("preferences", {}), **preferences}
        try:
            result = (
                supabase.table("user_preferences")
                .update({"preferences": merged})
                .eq("user_id", user_id)
                .execute()
            )
        except Exception as e:
            logger.exception("Error updating preferences: %s", e)
            raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")
    else:
        # Insert new preferences row
        try:
            result = (
                supabase.table("user_preferences")
                .insert({"user_id": user_id, "preferences": preferences})
                .execute()
            )
        except Exception as e:
            logger.exception("Error inserting preferences: %s", e)
            raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")

    return result.data[0]


# ============================================
# USER TEMPLATES
# ============================================

def list_templates(
    supabase: SupabaseClient,
    auth_id: str,
) -> list[dict]:
    """List user's active templates (deleted_at IS NULL)."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("user_templates")
            .select("*")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing templates: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب القوالب")

    return result.data or []


def create_template(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    title: str,
    description: str = "",
    prompt_template: str,
    agent_family: str = "end_services",
) -> dict:
    """Create a new user template."""
    user_id = get_user_id(supabase, auth_id)

    if agent_family not in _VALID_AGENT_FAMILIES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.TEMPLATE_INVALID_AGENT,
            detail=f"عائلة الوكيل غير صالحة. القيم المسموحة: {', '.join(_VALID_AGENT_FAMILIES)}",
        )

    try:
        result = (
            supabase.table("user_templates")
            .insert({
                "user_id": user_id,
                "title": title,
                "description": description,
                "prompt_template": prompt_template,
                "agent_family": agent_family,
            })
            .execute()
        )
    except Exception as e:
        logger.exception("Error creating template: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء القالب")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء القالب")

    return result.data[0]


def update_template(
    supabase: SupabaseClient,
    auth_id: str,
    template_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    prompt_template: Optional[str] = None,
    agent_family: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> dict:
    """Update template fields. Ownership verified."""
    user_id = get_user_id(supabase, auth_id)

    update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if title is not None:
        update_data["title"] = title
    if description is not None:
        update_data["description"] = description
    if prompt_template is not None:
        update_data["prompt_template"] = prompt_template
    if agent_family is not None:
        if agent_family not in _VALID_AGENT_FAMILIES:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.TEMPLATE_INVALID_AGENT,
                detail=f"عائلة الوكيل غير صالحة. القيم المسموحة: {', '.join(_VALID_AGENT_FAMILIES)}",
            )
        update_data["agent_family"] = agent_family
    if is_active is not None:
        update_data["is_active"] = is_active

    if len(update_data) == 1:
        raise LunaHTTPException(status_code=400, code=ErrorCode.NO_UPDATE_DATA, detail="لم يتم تقديم أي بيانات للتحديث")

    try:
        result = (
            supabase.table("user_templates")
            .update(update_data)
            .eq("template_id", template_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating template: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث القالب")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.TEMPLATE_NOT_FOUND, detail="القالب غير موجود")

    return result.data[0]


def delete_template(
    supabase: SupabaseClient,
    auth_id: str,
    template_id: str,
) -> None:
    """Soft delete template."""
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("user_templates")
            .update({"deleted_at": now, "updated_at": now})
            .eq("template_id", template_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error deleting template: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء حذف القالب")

    if not result.data:
        raise LunaHTTPException(status_code=404, code=ErrorCode.TEMPLATE_NOT_FOUND, detail="القالب غير موجود")
