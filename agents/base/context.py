"""Context builder — assembles AgentContext from raw database state."""
from __future__ import annotations

import logging
from typing import Optional

from supabase import Client as SupabaseClient

from shared.types import AgentContext, AgentFamily, ChatMessage, MessageRole

logger = logging.getLogger(__name__)


async def build_agent_context(
    supabase: SupabaseClient,
    question: str,
    user_id: str,
    conversation_id: str,
    case_id: str | None = None,
    agent_family: AgentFamily | None = None,
    modifiers: list[str] | None = None,
) -> AgentContext:
    """
    Build AgentContext from database state.

    Loads:
    - memory_md from artifacts table (if case-linked)
    - user_preferences from user_preferences table
    - user_templates if agent is END_SERVICES
    - conversation_history from messages table
    - case_metadata from lawyer_cases table
    - document_summaries from case_documents table
    """
    ctx = AgentContext(
        question=question,
        conversation_id=conversation_id,
        user_id=user_id,
        case_id=case_id,
        modifiers=modifiers or [],
    )

    # Load conversation history (last 20 messages)
    try:
        msg_result = (
            supabase.table("messages")
            .select("role, content, message_id, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        if msg_result.data:
            ctx.conversation_history = [
                ChatMessage(
                    role=MessageRole(m["role"]),
                    content=m.get("content", ""),
                )
                for m in reversed(msg_result.data)
            ]
    except Exception as e:
        logger.warning("Error loading conversation history: %s", e)

    # Load user preferences
    try:
        prefs_result = (
            supabase.table("user_preferences")
            .select("preferences")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if prefs_result and prefs_result.data:
            ctx.user_preferences = prefs_result.data.get("preferences", {})
    except Exception as e:
        logger.warning("Error loading user preferences: %s", e)

    if not case_id:
        return ctx

    # Case-specific context loading

    # Load memory_md from artifacts table
    try:
        mem_result = (
            supabase.table("artifacts")
            .select("content_md")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .eq("artifact_type", "memory_file")
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if mem_result.data:
            ctx.memory_md = mem_result.data[0].get("content_md")
    except Exception as e:
        logger.warning("Error loading memory_md: %s", e)

    # Load case metadata
    try:
        case_result = (
            supabase.table("lawyer_cases")
            .select("case_name, case_type, description, case_number, court_name, priority")
            .eq("case_id", case_id)
            .maybe_single()
            .execute()
        )
        if case_result and case_result.data:
            ctx.case_metadata = case_result.data
    except Exception as e:
        logger.warning("Error loading case metadata: %s", e)

    # Load document summaries
    try:
        doc_result = (
            supabase.table("case_documents")
            .select("document_name, mime_type, extraction_status")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .eq("extraction_status", "completed")
            .limit(10)
            .execute()
        )
        if doc_result.data:
            ctx.document_summaries = doc_result.data
    except Exception as e:
        logger.warning("Error loading document summaries: %s", e)

    # Load user templates if END_SERVICES agent
    if agent_family == AgentFamily.END_SERVICES:
        try:
            tmpl_result = (
                supabase.table("user_templates")
                .select("template_id, title, description, prompt_template, agent_family")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .is_("deleted_at", "null")
                .execute()
            )
            if tmpl_result.data:
                ctx.user_templates = tmpl_result.data
        except Exception as e:
            logger.warning("Error loading user templates: %s", e)

    return ctx
