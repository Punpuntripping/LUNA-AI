"""
Memory markdown service — manages the special memory.md artifact per case.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.services.artifact_service import create_artifact

logger = logging.getLogger(__name__)


def get_or_create_memory_md(
    supabase: SupabaseClient,
    user_id: str,
    case_id: str,
) -> dict:
    """Get existing memory.md artifact or create empty one for a case."""
    try:
        result = (
            supabase.table("artifacts")
            .select("*")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .eq("artifact_type", "memory_file")
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching memory.md: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب ذاكرة القضية")

    if result is not None and result.data is not None:
        return result.data

    # Create new memory.md artifact
    return create_artifact(
        supabase,
        user_id,
        case_id=case_id,
        agent_family="memory",
        artifact_type="memory_file",
        title="ذاكرة القضية",
        content_md="",
        is_editable=True,
    )


def update_memory_md(
    supabase: SupabaseClient,
    user_id: str,
    case_id: str,
    content_md: str,
) -> dict:
    """Update memory.md content. Creates if doesn't exist."""
    existing = get_or_create_memory_md(supabase, user_id, case_id)

    try:
        result = (
            supabase.table("artifacts")
            .update({"content_md": content_md})
            .eq("artifact_id", existing["artifact_id"])
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating memory.md: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحديث ذاكرة القضية")

    if not result.data:
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحديث ذاكرة القضية")

    return result.data[0]
