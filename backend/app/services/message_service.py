"""
Message business logic.
Orchestrates the full message pipeline: save → context → stream → update.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import HTTPException
from supabase import Client as SupabaseClient

from backend.app.services.case_service import get_user_id
from backend.app.services.context_service import build_context
from agents.router.router import route_and_execute

logger = logging.getLogger(__name__)


def _verify_conversation_ownership(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict:
    """Verify conversation exists and belongs to user. Returns conversation row."""
    try:
        result = (
            supabase.table("conversations")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying conversation: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="المحادثة غير موجودة")

    return result.data


def list_messages(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    limit: int = 50,
    before: Optional[str] = None,
) -> dict:
    """Paginated message list with ownership check. Newest first."""
    user_id = get_user_id(supabase, auth_id)
    _verify_conversation_ownership(supabase, conversation_id, user_id)

    limit = max(1, min(limit, 100))

    try:
        query = (
            supabase.table("messages")
            .select("*", count="exact")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(limit + 1)  # Fetch one extra to check has_more
        )

        if before:
            # Cursor: get messages created before the cursor message
            cursor_result = (
                supabase.table("messages")
                .select("created_at")
                .eq("message_id", before)
                .maybe_single()
                .execute()
            )
            if cursor_result and cursor_result.data:
                query = query.lt("created_at", cursor_result.data["created_at"])

        result = query.execute()
    except Exception as e:
        logger.exception("Error listing messages: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب الرسائل")

    messages = result.data or []
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    # Load attachments for each message
    message_ids = [m["message_id"] for m in messages]
    attachments_map: dict[str, list] = {}

    if message_ids:
        try:
            att_result = (
                supabase.table("message_attachments")
                .select("*, case_documents(document_name, mime_type, file_size_bytes)")
                .in_("message_id", message_ids)
                .execute()
            )
            for att in (att_result.data or []):
                mid = att["message_id"]
                doc = att.get("case_documents", {}) or {}
                attachments_map.setdefault(mid, []).append({
                    "id": att["id"],
                    "document_id": att["document_id"],
                    "attachment_type": att.get("attachment_type", "file"),
                    "filename": doc.get("document_name", ""),
                    "file_size": doc.get("file_size_bytes"),
                })
        except Exception as e:
            logger.warning("Error loading attachments: %s", e)

    # Enrich messages with attachments
    enriched = []
    for m in messages:
        enriched.append({
            "message_id": m["message_id"],
            "conversation_id": m["conversation_id"],
            "role": m["role"],
            "content": m.get("content", ""),
            "model": m.get("model"),
            "attachments": attachments_map.get(m["message_id"], []),
            "created_at": m["created_at"],
        })

    return {
        "messages": enriched,
        "has_more": has_more,
    }


async def send_message(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    content: str,
    agent_family: str | None = None,
    modifiers: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Main message pipeline. Yields SSE-formatted strings.

    1. Verify conversation ownership
    2. Save user message to DB (BEFORE AI call — crash-safe)
    3. Create assistant message placeholder
    4. Yield message_start event
    5. Call RAG pipeline → yield token events
    6. Yield citations event
    7. Update assistant message with full content
    8. Update conversation metadata
    9. Yield done event
    """
    user_id = get_user_id(supabase, auth_id)
    conv = _verify_conversation_ownership(supabase, conversation_id, user_id)

    # 1. Save user message BEFORE AI call (Absolute Rule #7)
    user_msg_id = str(uuid.uuid4())
    try:
        supabase.table("messages").insert({
            "message_id": user_msg_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": content,
        }).execute()
    except Exception as e:
        logger.exception("Error saving user message: %s", e)
        yield _sse_event("error", {"detail": "حدث خطأ أثناء حفظ الرسالة"})
        return

    # 2. Create assistant message placeholder
    assistant_msg_id = str(uuid.uuid4())
    try:
        supabase.table("messages").insert({
            "message_id": assistant_msg_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "",
            "model": "mock-model",
        }).execute()
    except Exception as e:
        logger.exception("Error creating assistant placeholder: %s", e)
        yield _sse_event("error", {"detail": "حدث خطأ داخلي"})
        return

    # 3. Yield message_start
    yield _sse_event("message_start", {
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "conversation_id": conversation_id,
    })

    # 4. Build context and stream from agent router
    context = build_context(supabase, conversation_id, user_id)
    full_content = ""
    citations = []

    try:
        async for event in route_and_execute(
            question=content,
            context=context,
            user_id=user_id,
            conversation_id=conversation_id,
            supabase=supabase,
            case_id=conv.get("case_id"),
            explicit_agent=agent_family,
            modifiers=modifiers,
        ):
            event_type = event.get("type")

            if event_type == "token":
                text = event.get("text", "")
                full_content += text
                yield _sse_event("token", {"text": text})

            elif event_type == "citations":
                citations = event.get("articles", [])
                yield _sse_event("citations", {"articles": citations})

            elif event_type == "artifact_created":
                yield _sse_event("artifact_created", {
                    "artifact_id": event["artifact_id"],
                    "artifact_type": event["artifact_type"],
                    "title": event["title"],
                })

            elif event_type == "agent_selected":
                yield _sse_event("agent_selected", {
                    "agent_family": event["agent_family"],
                })

            elif event_type == "done":
                usage = event.get("usage", {})

                # 5. Update assistant message with full content
                try:
                    update_data: dict = {"content": full_content}
                    if usage:
                        update_data["prompt_tokens"] = usage.get("prompt_tokens", 0)
                        update_data["completion_tokens"] = usage.get("completion_tokens", 0)
                    if citations:
                        update_data["metadata"] = {"citations": citations}

                    supabase.table("messages").update(
                        update_data
                    ).eq("message_id", assistant_msg_id).execute()
                except Exception as e:
                    logger.exception("Error updating assistant message: %s", e)

                # 6. Update conversation metadata
                try:
                    now = datetime.now(timezone.utc).isoformat()

                    conv_update: dict = {
                        "updated_at": now,
                    }
                    current_count = conv.get("message_count", 0)
                    conv_update["message_count"] = current_count + 2  # user + assistant

                    # Auto-title from first message
                    if current_count == 0:
                        title = content[:60].strip()
                        if len(content) > 60:
                            title += "..."
                        conv_update["title_ar"] = title

                    supabase.table("conversations").update(
                        conv_update
                    ).eq("conversation_id", conversation_id).execute()
                except Exception as e:
                    logger.exception("Error updating conversation: %s", e)

                # 7. Yield done event
                yield _sse_event("done", {
                    "message_id": assistant_msg_id,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    },
                })

    except Exception as e:
        logger.exception("Error in agent pipeline: %s", e)
        yield _sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"})


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
