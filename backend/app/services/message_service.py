"""
Message business logic.
Orchestrates the full message pipeline: save → context → stream → update.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import HTTPException, Request
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.audit_service import write_audit_log
from backend.app.services.case_service import get_user_id
from agents.orchestrator import handle_message

logger = logging.getLogger(__name__)


def _reference_to_citation(ref: dict) -> dict:
    """Map a deep_search_v4 ``Reference`` dict to the frontend ``Citation`` shape.

    The frontend Citation contract is locked: ``{ article_id, law_name,
    article_number, relevance_score }``. v4 emits richer ``Reference`` dicts;
    we project them onto the legacy shape so existing UI keeps rendering.
    """
    article_num_raw = ref.get("article_num")
    if isinstance(article_num_raw, str) and article_num_raw.isdigit():
        article_number = int(article_num_raw)
    elif isinstance(article_num_raw, int):
        article_number = article_num_raw
    else:
        article_number = 0

    relevance_score = 0.9 if ref.get("relevance") == "high" else 0.6

    return {
        "article_id": ref.get("ref_id", ""),
        "law_name": ref.get("regulation_title", ""),
        "article_number": article_number,
        "relevance_score": relevance_score,
    }


def verify_conversation_ownership(
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
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CONV_NOT_FOUND, detail="المحادثة غير موجودة")

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
    verify_conversation_ownership(supabase, conversation_id, user_id)

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
        raise LunaHTTPException(status_code=500, code=ErrorCode.MSG_LIST_FAILED, detail="حدث خطأ أثناء جلب الرسائل")

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
            "metadata": m.get("metadata") or {},
            "created_at": m["created_at"],
        })

    return {
        "messages": enriched,
        "has_more": has_more,
    }


async def send_message_stream(
    supabase: SupabaseClient,
    *,
    user_id: str,
    conversation_id: str,
    conv: dict,
    content: str,
    request: Request,
    agent_family: str | None = None,
    attachment_ids: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Main message pipeline. Yields SSE-formatted strings.

    Ownership is verified by the caller BEFORE this generator runs.

    1. Save user message to DB (BEFORE AI call — crash-safe)
    2. Create assistant message placeholder
    3. Yield message_start event
    4. Call RAG pipeline → yield token events
    5. Yield citations event
    6. Update assistant message with full content
    7. Update conversation metadata
    8. Yield done event
    """

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

    write_audit_log(
        supabase,
        user_id=user_id,
        action="create",
        resource_type="message",
        resource_id=user_msg_id,
    )

    # 1b. Link attachments to user message (if any)
    if attachment_ids:
        try:
            rows = [
                {"message_id": user_msg_id, "document_id": doc_id}
                for doc_id in attachment_ids
            ]
            supabase.table("message_attachments").insert(rows).execute()
        except Exception as e:
            logger.warning("Error linking attachments: %s", e)

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

    # 4. Stream from orchestrator (with heartbeat + disconnect detection)
    full_content = ""
    citations = []
    # Set to True when the orchestrator ends the stream with an agent_question
    # event (run is paused, not finished).  In this case we skip inserting the
    # empty assistant placeholder row that would otherwise be written on 'done'.
    paused = False

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def heartbeat_producer() -> None:
        """Send heartbeat every 15s to keep Railway proxy alive."""
        try:
            while True:
                await asyncio.sleep(15)
                await queue.put(_sse_event("heartbeat", {}))
        except asyncio.CancelledError:
            pass

    async def pipeline_producer() -> None:
        """Run agent pipeline and put SSE events on the queue."""
        nonlocal full_content, citations, paused
        try:
            async for event in handle_message(
                question=content,
                user_id=user_id,
                conversation_id=conversation_id,
                supabase=supabase,
                case_id=conv.get("case_id"),
                explicit_agent_family=agent_family,
                user_message_id=user_msg_id,
            ):
                event_type = event.get("type")

                if event_type == "token":
                    text = event.get("text", "")
                    full_content += text
                    await queue.put(_sse_event("token", {"text": text}))

                elif event_type == "citations":
                    raw_articles = event.get("articles", []) or []
                    citations = [_reference_to_citation(r) for r in raw_articles]
                    await queue.put(_sse_event("citations", {"articles": citations}))

                elif event_type == "agent_selected":
                    await queue.put(_sse_event("agent_selected", {
                        "agent_family": event["agent_family"],
                    }))

                elif event_type == "agent_run_started":
                    await queue.put(_sse_event("agent_run_started", {
                        "agent_family": event["agent_family"],
                        "subtype": event.get("subtype"),
                    }))

                elif event_type == "agent_run_finished":
                    await queue.put(_sse_event("agent_run_finished", {
                        "agent_family": event["agent_family"],
                    }))

                elif event_type == "workspace_item_created":
                    payload = {
                        "item_id": event.get("item_id"),
                        "kind": event.get("kind"),
                        "title": event.get("title", ""),
                        "created_by": event.get("created_by"),
                    }
                    if event.get("subtype") is not None:
                        payload["subtype"] = event["subtype"]
                    await queue.put(_sse_event("workspace_item_created", payload))

                elif event_type == "workspace_item_updated":
                    await queue.put(_sse_event("workspace_item_updated", {
                        "item_id": event.get("item_id"),
                    }))

                elif event_type == "workspace_item_locked":
                    await queue.put(_sse_event("workspace_item_locked", {
                        "item_id": event.get("item_id"),
                        "locked_until": event.get("locked_until"),
                    }))

                elif event_type == "workspace_item_unlocked":
                    await queue.put(_sse_event("workspace_item_unlocked", {
                        "item_id": event.get("item_id"),
                    }))

                elif event_type == "status":
                    await queue.put(_sse_event("status", {
                        "text": event.get("text", ""),
                    }))

                elif event_type == "ask_user":
                    await queue.put(_sse_event("ask_user", {
                        "question": event.get("question", ""),
                    }))

                elif event_type == "agent_question":
                    # Planner paused — the question was already inserted as an
                    # assistant message by the orchestrator via _record_deferred.
                    # Mark the stream as paused so we skip writing an empty
                    # assistant placeholder on the subsequent 'done' event.
                    paused = True
                    await queue.put(_sse_event("agent_question", {
                        "run_id": event.get("run_id", ""),
                        "question": event.get("question", ""),
                        "suggestions": event.get("suggestions", []),
                    }))

                elif event_type == "agent_resumed":
                    await queue.put(_sse_event("agent_resumed", {
                        "run_id": event.get("run_id", ""),
                        "agent_family": event.get("agent_family", ""),
                    }))

                elif event_type == "done":
                    usage = event.get("usage", {})

                    # 5. Update assistant message with full content.
                    # Skip when the stream ended as a pause (agent_question event
                    # was emitted): the question row was already inserted by the
                    # orchestrator and the placeholder we created here is empty.
                    if paused:
                        # Delete the empty placeholder we created above — the real
                        # question message was inserted by _record_deferred.
                        try:
                            supabase.table("messages").delete().eq(
                                "message_id", assistant_msg_id
                            ).execute()
                        except Exception as e:
                            logger.warning("Could not delete paused assistant placeholder: %s", e)
                    else:
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
                        # On pause: only the user message was saved (the question
                        # row is inserted by _record_deferred separately).
                        msg_delta = 1 if paused else 2  # user + assistant
                        conv_update["message_count"] = current_count + msg_delta

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
                    await queue.put(_sse_event("done", {
                        "message_id": assistant_msg_id,
                        "usage": {
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                        },
                    }))

        except Exception as e:
            logger.exception("Error in agent pipeline: %s", e)
            await queue.put(_sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"}))
        finally:
            await queue.put(None)  # Sentinel: pipeline complete

    heartbeat_task = asyncio.create_task(heartbeat_producer())
    pipeline_task = asyncio.create_task(pipeline_producer())

    try:
        while True:
            if await request.is_disconnected():
                logger.info("Client disconnected during streaming for conversation %s", conversation_id)
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # Re-check disconnect
            if item is None:
                break
            yield item
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for conversation %s (client disconnect)", conversation_id)
        raise  # MUST re-raise per asyncio contract
    except Exception as e:
        logger.exception("Error in agent pipeline: %s", e)
        yield _sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"})
    finally:
        heartbeat_task.cancel()
        if not pipeline_task.done():
            pipeline_task.cancel()


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
