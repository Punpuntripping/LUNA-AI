"""Router context loader — assembles eager input context for ``run_router``.

The router needs four pieces of context every turn:

1. Case metadata + case memories (when ``case_id`` is set)
2. Workspace item summaries — compact ``(item_id, kind, title, summary)``
   dicts used to populate ``DispatchAgent.attached_item_ids``. The full
   ``content_md`` is fetched on demand via the ``read_workspace_item`` tool.
3. Compaction summary — full ``content_md`` of the latest ``convo_context``
   workspace item, when one exists.
4. Recent messages — strictly after ``conversations.compacted_through_message_id``
   (or all messages if the cutoff is NULL), with ``agent_question`` and
   ``agent_answer`` metadata kinds excluded (they are reserved for Tier-2
   pause/resume Q&A audit trail; they are not router prompt material).

This module is the single source of truth for what the router sees per
turn. The orchestrator imports ``load_router_context`` and forwards the
resulting fields to ``run_router``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from supabase import Client as SupabaseClient

from agents.utils.history import messages_to_history
from pydantic_ai.messages import ModelMessage

logger = logging.getLogger(__name__)


_EXCLUDED_MESSAGE_KINDS = {"agent_question", "agent_answer"}


@dataclass
class RouterContext:
    """Bundle returned by ``load_router_context``."""

    case_memory_md: str | None = None
    case_metadata: dict | None = None
    user_preferences: dict | None = None
    workspace_item_summaries: list[dict] = field(default_factory=list)
    compaction_summary_md: str | None = None
    message_history: list[ModelMessage] = field(default_factory=list)


def _load_case_block(
    supabase: SupabaseClient, case_id: str
) -> tuple[dict | None, str | None]:
    """Return (case_metadata, case_memory_md) for ``case_id``."""
    case_metadata: dict | None = None
    try:
        case_row = (
            supabase.table("lawyer_cases")
            .select("case_name, case_type, status, parties, description")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if case_row and getattr(case_row, "data", None):
            case_metadata = case_row.data
    except Exception as e:
        logger.warning("load_router_context: lawyer_cases load failed: %s", e)

    memories: list[dict] = []
    try:
        mem_resp = (
            supabase.table("case_memories")
            .select("content")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        )
        memories = (mem_resp.data if mem_resp and getattr(mem_resp, "data", None) else []) or []
    except Exception as e:
        logger.warning("load_router_context: case_memories load failed: %s", e)

    case_memory_md: str | None = None
    parts: list[str] = []
    if case_metadata:
        parts.append(
            "### معلومات القضية\n\n"
            f"**اسم القضية:** {case_metadata.get('case_name', '')}\n"
            f"**نوع القضية:** {case_metadata.get('case_type', '')}"
        )
    if memories:
        parts.append(
            "### الوقائع والمعلومات المحفوظة\n\n"
            + "\n".join(f"- {m.get('content', '')}" for m in memories)
        )
    if parts:
        case_memory_md = "\n\n".join(parts)
    return case_metadata, case_memory_md


def _load_user_preferences(
    supabase: SupabaseClient, user_id: str
) -> dict | None:
    try:
        prefs_row = (
            supabase.table("user_preferences")
            .select("preferences")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if prefs_row and getattr(prefs_row, "data", None):
            return prefs_row.data.get("preferences")
    except Exception as e:
        logger.warning("load_router_context: user_preferences load failed: %s", e)
    return None


def _load_workspace_item_summaries(
    supabase: SupabaseClient, conversation_id: str
) -> tuple[list[dict], str | None]:
    """Return (summaries, compaction_summary_md).

    Workspace items are filtered to exclude ``convo_context`` from the
    summaries list — that one's full ``content_md`` is returned separately
    as the compaction summary.
    """
    try:
        resp = (
            supabase.table("workspace_items")
            .select("item_id, kind, title, summary, content_md, created_at")
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        )
        rows = (resp.data if resp and getattr(resp, "data", None) else []) or []
    except Exception as e:
        logger.warning(
            "load_router_context: workspace_items load failed for %s: %s",
            conversation_id, e,
        )
        return [], None

    summaries: list[dict] = []
    compaction_summary_md: str | None = None
    latest_compaction_at: str = ""

    for row in rows:
        kind = row.get("kind") or ""
        if kind == "convo_context":
            created = row.get("created_at") or ""
            # Pick the most recent convo_context item by created_at.
            if created >= latest_compaction_at:
                latest_compaction_at = created
                compaction_summary_md = row.get("content_md") or None
            continue
        summaries.append({
            "item_id": row.get("item_id"),
            "kind": kind or "agent_search",
            "title": row.get("title") or "",
            "summary": row.get("summary"),  # may be NULL — renderer handles
        })
    return summaries, compaction_summary_md


def _load_filtered_messages(
    supabase: SupabaseClient, conversation_id: str
) -> list[dict]:
    """Load conversation messages strictly after the compaction cutoff,
    excluding agent_question / agent_answer kinds."""
    cutoff_message_id: str | None = None
    try:
        conv = (
            supabase.table("conversations")
            .select("compacted_through_message_id")
            .eq("conversation_id", conversation_id)
            .maybe_single()
            .execute()
        )
        if conv and getattr(conv, "data", None):
            cutoff_message_id = conv.data.get("compacted_through_message_id")
    except Exception as e:
        logger.warning(
            "load_router_context: conversations.compacted_through_message_id "
            "lookup failed: %s",
            e,
        )

    cutoff_created_at: str | None = None
    if cutoff_message_id:
        try:
            cutoff_row = (
                supabase.table("messages")
                .select("created_at")
                .eq("message_id", cutoff_message_id)
                .maybe_single()
                .execute()
            )
            if cutoff_row and getattr(cutoff_row, "data", None):
                cutoff_created_at = cutoff_row.data.get("created_at")
        except Exception as e:
            logger.warning(
                "load_router_context: cutoff message lookup failed: %s", e
            )

    try:
        q = (
            supabase.table("messages")
            .select("role, content, metadata, created_at")
            .eq("conversation_id", conversation_id)
        )
        if cutoff_created_at:
            q = q.gt("created_at", cutoff_created_at)
        msg_rows = (q.order("created_at", desc=False).execute()).data or []
    except Exception as e:
        logger.warning("load_router_context: messages load failed: %s", e)
        return []

    # Python-side filter on metadata->>kind. Done here (rather than via
    # PostgREST) to remain robust whether or not those rows exist yet
    # (Task 13 introduces the kinds; the filter is forward-compatible).
    filtered: list[dict] = []
    for row in msg_rows:
        metadata = row.get("metadata") or {}
        kind = None
        if isinstance(metadata, dict):
            kind = metadata.get("kind")
        if kind in _EXCLUDED_MESSAGE_KINDS:
            continue
        filtered.append(row)
    return filtered


def load_router_context(
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
) -> RouterContext:
    """Eagerly load everything the router needs for one turn.

    Pure data assembly — no LLM calls. Safe to call from the orchestrator
    just before ``run_router``.
    """
    case_metadata: dict | None = None
    case_memory_md: str | None = None
    if case_id:
        case_metadata, case_memory_md = _load_case_block(supabase, case_id)

    user_preferences = _load_user_preferences(supabase, user_id)

    workspace_item_summaries, compaction_summary_md = _load_workspace_item_summaries(
        supabase, conversation_id
    )

    msg_rows = _load_filtered_messages(supabase, conversation_id)
    message_history = messages_to_history(msg_rows)

    return RouterContext(
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        workspace_item_summaries=workspace_item_summaries,
        compaction_summary_md=compaction_summary_md,
        message_history=message_history,
    )


__all__ = ["RouterContext", "load_router_context"]
