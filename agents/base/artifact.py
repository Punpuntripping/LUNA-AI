"""Thin helper for agents to create workspace items without importing backend directly.

Wave 8B cleanup: drops the legacy `(artifact_type, is_editable)` shim and writes
post-026 columns (`kind`, `created_by`, `metadata.subtype`) directly. The public
function name stays `create_agent_artifact` for caller compatibility, but the
caller's `artifact_type` becomes `metadata.subtype` and `is_editable` is mapped
to `kind` via the same rule the migration backfill used:

    is_editable=True  -> kind="agent_writing"
    is_editable=False -> kind="agent_search"
"""
from __future__ import annotations

from supabase import Client as SupabaseClient

from backend.app.services.workspace_service import create_workspace_item


async def create_agent_artifact(
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    agent_family: str,
    artifact_type: str,
    title: str,
    content_md: str,
    is_editable: bool = False,
    metadata: dict | None = None,
    message_id: str | None = None,
) -> dict:
    """Convenience wrapper for agents to create workspace items.

    Returns the created row. The returned dict has both ``item_id`` (real
    column post-026) and ``artifact_id`` (alias for legacy callers).
    """
    kind = "agent_writing" if is_editable else "agent_search"
    merged_metadata: dict = dict(metadata or {})
    # Preserve the caller's artifact_type as metadata.subtype so the chip
    # color/icon survives the schema rename. Don't overwrite a subtype the
    # caller already set explicitly.
    merged_metadata.setdefault("subtype", artifact_type)

    row = create_workspace_item(
        supabase,
        user_id,
        kind=kind,
        created_by="agent",
        title=title,
        conversation_id=conversation_id,
        case_id=case_id,
        message_id=message_id,
        agent_family=agent_family,
        content_md=content_md,
        metadata=merged_metadata,
    )

    # Alias so any legacy reader of `artifact_id` keeps working.
    row = dict(row)
    if "artifact_id" not in row and "item_id" in row:
        row["artifact_id"] = row["item_id"]
    return row
