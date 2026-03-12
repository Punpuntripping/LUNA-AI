"""Thin helper for agents to create artifacts without importing backend directly."""
from __future__ import annotations

from typing import Optional

from supabase import Client as SupabaseClient

from backend.app.services.artifact_service import create_artifact as _create


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
) -> dict:
    """Convenience wrapper for agents to create artifacts. Returns the created artifact row."""
    return _create(
        supabase,
        user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family=agent_family,
        artifact_type=artifact_type,
        title=title,
        content_md=content_md,
        is_editable=is_editable,
        metadata=metadata,
    )
