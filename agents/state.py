"""Task state management — DB operations and state dataclass."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    """In-memory representation of an active task."""
    task_id: str
    task_type: str              # "deep_search" | "end_services" | "extraction"
    agent_family: str           # Same as task_type for now
    artifact_id: str | None     # Set after first TaskContinue or loaded from existing
    current_artifact: str       # Latest artifact markdown
    history: list[dict] = field(default_factory=list)  # Serialized messages (task-scoped)


def get_active_task(supabase: SupabaseClient, conversation_id: str) -> TaskInfo | None:
    """Load active task for a conversation from DB. Returns None if no active task."""
    try:
        result = (
            supabase.table("task_state")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            return None

        row = result.data
        # Load current artifact content if artifact_id is set
        current_artifact = ""
        if row.get("artifact_id"):
            try:
                art_result = (
                    supabase.table("artifacts")
                    .select("content_md")
                    .eq("artifact_id", row["artifact_id"])
                    .maybe_single()
                    .execute()
                )
                if art_result and art_result.data:
                    current_artifact = art_result.data.get("content_md", "")
            except Exception as e:
                logger.warning("Error loading artifact for task %s: %s", row["task_id"], e)

        return TaskInfo(
            task_id=row["task_id"],
            task_type=row["agent_family"],
            agent_family=row["agent_family"],
            artifact_id=row.get("artifact_id"),
            current_artifact=current_artifact,
            history=row.get("history_json") or [],
        )
    except Exception as e:
        logger.warning("Error loading active task for conversation %s: %s", conversation_id, e)
        return None


def create_task(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    agent_family: str,
    briefing: str,
    artifact_id: str | None = None,
) -> TaskInfo:
    """Insert new task_state row. Load existing artifact if artifact_id provided."""
    row_data = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "agent_family": agent_family,
        "briefing": briefing,
        "status": "active",
    }
    if artifact_id:
        row_data["artifact_id"] = artifact_id

    result = (
        supabase.table("task_state")
        .insert(row_data)
        .execute()
    )
    row = result.data[0]

    # Load existing artifact content if editing
    current_artifact = ""
    if artifact_id:
        try:
            art_result = (
                supabase.table("artifacts")
                .select("content_md")
                .eq("artifact_id", artifact_id)
                .maybe_single()
                .execute()
            )
            if art_result and art_result.data:
                current_artifact = art_result.data.get("content_md", "")
        except Exception as e:
            logger.warning("Error loading artifact %s for new task: %s", artifact_id, e)

    return TaskInfo(
        task_id=row["task_id"],
        task_type=agent_family,
        agent_family=agent_family,
        artifact_id=artifact_id,
        current_artifact=current_artifact,
        history=[],
    )


def update_task_history(supabase: SupabaseClient, task_id: str, history: list[dict]) -> None:
    """Persist task message history to DB."""
    try:
        (
            supabase.table("task_state")
            .update({"history_json": history})
            .eq("task_id", task_id)
            .execute()
        )
    except Exception as e:
        logger.warning("Error updating task history for %s: %s", task_id, e)


def update_task_artifact(supabase: SupabaseClient, task_id: str, artifact_id: str) -> None:
    """Link artifact to task."""
    try:
        (
            supabase.table("task_state")
            .update({"artifact_id": artifact_id})
            .eq("task_id", task_id)
            .execute()
        )
    except Exception as e:
        logger.warning("Error updating task artifact for %s: %s", task_id, e)


def complete_task(
    supabase: SupabaseClient,
    task_id: str,
    summary: str,
    status: str = "completed",
) -> None:
    """Mark task as completed/abandoned, set ended_at, persist summary."""
    try:
        (
            supabase.table("task_state")
            .update({
                "status": status,
                "summary": summary,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("task_id", task_id)
            .execute()
        )
    except Exception as e:
        logger.warning("Error completing task %s: %s", task_id, e)
