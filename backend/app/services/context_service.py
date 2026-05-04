"""
Context builder for AI pipeline.
Assembles the context bundle (messages, memories, document summaries)
that gets passed to the RAG pipeline.
"""
from __future__ import annotations

import logging
from typing import Optional

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


def build_context(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict:
    """
    Build context bundle for AI pipeline.

    General mode: { mode: "general", messages: [...] }
    Case mode: adds case metadata, memories (top 15), document summaries.
    """
    # Fetch conversation to determine mode
    try:
        conv_result = (
            supabase.table("conversations")
            .select("conversation_id, case_id, user_id")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching conversation for context: %s", e)
        return {"mode": "general", "messages": []}

    if conv_result is None or conv_result.data is None:
        return {"mode": "general", "messages": []}

    conv = conv_result.data
    case_id = conv.get("case_id")

    # Fetch recent messages (last 20 for context window)
    try:
        msg_result = (
            supabase.table("messages")
            .select("role, content")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        messages = list(reversed(msg_result.data or []))
    except Exception as e:
        logger.exception("Error fetching messages for context: %s", e)
        messages = []

    # Load user preferences (for both general and case modes)
    user_preferences = {}
    try:
        prefs_result = (
            supabase.table("user_preferences")
            .select("preferences")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if prefs_result and prefs_result.data:
            user_preferences = prefs_result.data.get("preferences", {})
    except Exception:
        pass

    if not case_id:
        return {
            "mode": "general",
            "messages": messages,
            "user_preferences": user_preferences,
        }

    # Case mode — load additional context
    context: dict = {
        "mode": "case",
        "case_id": case_id,
        "messages": messages,
        "user_preferences": user_preferences,
    }

    # Load case metadata
    try:
        case_result = (
            supabase.table("lawyer_cases")
            .select("case_name, case_type, description")
            .eq("case_id", case_id)
            .maybe_single()
            .execute()
        )
        if case_result and case_result.data:
            context["case_metadata"] = case_result.data
    except Exception:
        pass

    # Load top 15 memories
    try:
        mem_result = (
            supabase.table("case_memories")
            .select("memory_type, content_ar, confidence_score")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("confidence_score", desc=True)
            .limit(15)
            .execute()
        )
        context["memories"] = mem_result.data or []
    except Exception:
        context["memories"] = []

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
        context["document_summaries"] = doc_result.data or []
    except Exception:
        context["document_summaries"] = []

    # Load memory_md from workspace_items (post-migration 026). The legacy
    # ``artifact_type='memory_file'`` filter becomes a metadata jsonb subtype
    # filter via PostgREST ``->>`` syntax.
    try:
        memory_result = (
            supabase.table("workspace_items")
            .select("content_md")
            .eq("user_id", user_id)
            .eq("case_id", case_id)
            .eq("metadata->>subtype", "memory_file")
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if memory_result.data:
            context["memory_md"] = memory_result.data[0].get("content_md")
    except Exception:
        pass

    return context
