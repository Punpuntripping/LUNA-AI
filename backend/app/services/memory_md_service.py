"""
Memory markdown service — manages the special memory.md artifact per case.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.workspace_service import create_workspace_item

logger = logging.getLogger(__name__)


def get_or_create_memory_md(
    supabase: SupabaseClient,
    user_id: str,
    case_id: str,
) -> dict:
    """Get existing memory.md artifact or create empty one for a case."""
    try:
        # Post-026: ``artifact_type`` is dropped; the legacy value lives in
        # ``metadata.subtype``. PostgREST exposes jsonb fields via ``->>``
        # syntax in the column path of ``.eq()``.
        result = (
            supabase.table("workspace_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .eq("metadata->>subtype", "memory_file")
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching memory.md: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب ذاكرة القضية")

    if result is not None and result.data is not None:
        row = dict(result.data)
        # Alias item_id -> artifact_id for any legacy callers that still read
        # the old field name. Safe no-op if already present.
        if "artifact_id" not in row and "item_id" in row:
            row["artifact_id"] = row["item_id"]
        return row

    # Create new memory.md as a workspace_item with kind=agent_writing and
    # metadata.subtype="memory_file" (post-026 schema).
    row = create_workspace_item(
        supabase,
        user_id,
        kind="agent_writing",
        created_by="agent",
        title="ذاكرة القضية",
        case_id=case_id,
        agent_family="memory",
        content_md="",
        metadata={"subtype": "memory_file"},
    )
    row = dict(row)
    if "artifact_id" not in row and "item_id" in row:
        row["artifact_id"] = row["item_id"]
    return row


def update_memory_md(
    supabase: SupabaseClient,
    user_id: str,
    case_id: str,
    content_md: str,
) -> dict:
    """Update memory.md content. Creates if doesn't exist."""
    existing = get_or_create_memory_md(supabase, user_id, case_id)
    # ``existing`` was aliased above so both ``item_id`` and ``artifact_id`` are
    # readable. Prefer ``item_id`` (the real post-026 column).
    item_id = existing.get("item_id") or existing.get("artifact_id")

    try:
        result = (
            supabase.table("workspace_items")
            .update({"content_md": content_md})
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating memory.md: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث ذاكرة القضية")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء تحديث ذاكرة القضية")

    row = dict(result.data[0])
    if "artifact_id" not in row and "item_id" in row:
        row["artifact_id"] = row["item_id"]
    return row
