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


def get_privacy_masking(supabase: SupabaseClient, user_id: str) -> bool:
    """Read ``privacy_masking`` from a user's preferences JSONB, default ``True``.

    Identifier masking (وضع السرية) is privacy-by-default: only an explicit
    ``false`` disables it (any other value — missing key, malformed row, null —
    resolves to ``True``). Mirrors :func:`get_detail_level`'s resilience: called
    by the pipeline (not routes), takes the resolved ``user_id`` (not the auth_id),
    and swallows every error so a broken preferences row can never abort a turn.

    The global env kill-switch ``settings.PRIVACY_MASKING_ENABLED`` gates the
    feature server-side and is combined with this per-user flag by
    ``masking_service.build_turn_codec``.
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
        return True

    rows = getattr(res, "data", None) or []
    if not rows:
        return True
    prefs = rows[0].get("preferences") or {}
    # Only an explicit boolean False disables; everything else stays ON.
    return prefs.get("privacy_masking") is not False


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
    """Atomically merge a partial preferences patch (RPC merge_preferences).

    The frontend sends PARTIAL patches; the shallow merge (patch keys win)
    happens inside one INSERT..ON CONFLICT statement (migration 066), so two
    concurrent partial PATCHes can no longer silently drop one another.
    """
    user_id = get_user_id(supabase, auth_id)

    try:
        result = supabase.rpc(
            "merge_preferences",
            {"p_user_id": user_id, "p_patch": preferences},
        ).execute()
    except Exception as e:
        logger.exception("Error merging preferences: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED, detail="حدث خطأ أثناء تحديث الإعدادات")

    # The RPC returns {"user_id": ..., "preferences": ...} matching
    # PreferencesResponse — no contract change.
    return result.data
