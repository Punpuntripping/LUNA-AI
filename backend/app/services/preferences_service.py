"""
User preferences business logic.
"""
from __future__ import annotations

import logging

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.case_service import get_user_id
from shared.types import DetailLevel

logger = logging.getLogger(__name__)

_VALID_DETAIL_LEVELS: set[str] = {"low", "medium", "high"}


# ============================================
# DETAIL LEVEL HELPER (agent-facing)
# ============================================

def get_detail_level(supabase: SupabaseClient, user_id: str) -> DetailLevel:
    """Read ``detail_level`` from a user's preferences JSONB, default ``"medium"``.

    Called by agents (not routes), so takes the resolved ``user_id`` (not the
    Supabase ``auth_id``). Swallows all errors and returns the default to keep
    the chat dispatch path resilient — a broken preferences row must not take
    down deep_search.
    """
    try:
        res = (
            supabase.table("user_preferences")
            .select("preferences")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        return "medium"

    rows = getattr(res, "data", None) or []
    if not rows:
        return "medium"
    prefs = rows[0].get("preferences") or {}
    val = prefs.get("detail_level")
    if val in _VALID_DETAIL_LEVELS:
        return val  # type: ignore[return-value]
    return "medium"


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
