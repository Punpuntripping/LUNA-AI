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
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# Convo-1 forensics bug #1 fix: when an SSE consumer exits before the pipeline
# task has finished (Railway gateway timeout, browser navigation, repeat-send,
# explicit Stop), the pipeline is moved here instead of cancelled. The Task
# runs to natural completion in the background, the workspace_item still
# publishes, and the artifact is visible to the user on next page load.
#
# Without this registry, the local pipeline_task variable would be GC'd after
# send_message_stream returns and Python would log a "Task was destroyed but it
# is pending" warning. The add_done_callback (registered at the detach site)
# discards the reference once the task naturally completes.
_inflight_pipelines: set[asyncio.Task] = set()


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

    # ── message.stream span ────────────────────────────────────────────────
    # Wraps the SSE consumer loop + pipeline_task lifecycle. Tagged with
    # conversation_id + both message ids so the Logfire trace tree for one
    # turn (router.classify → dispatch.specialist → … → SSE drain) is
    # filterable end-to-end on conversation_id alone.
    #
    # Cancellation source is recorded as the ``outcome`` attribute set in the
    # finally block — it surfaces *why* pipeline_task.cancel() fired (the
    # smoking-gun bug from convo-1 forensics). Possible values:
    #   completed              — pipeline finished naturally, sentinel drained.
    #   client_disconnect      — request.is_disconnected() flipped True.
    #   stream_cancelled       — asyncio.CancelledError raised in the consumer
    #                            (usually Railway gateway timeout, occasionally
    #                            a hard-kill from upstream).
    #   error                  — unhandled exception escaped the consumer.
    full_content = ""
    paused = False
    _stream_outcome = "unknown"
    _disconnect_detected = False
    # PII note: user_id intentionally NOT propagated here. The pre-existing
    # router.classify + dispatch.specialist spans (which DO carry user_id)
    # cover the per-turn user identity; downstream spans pivot by
    # conversation_id alone to keep user_id off ~10× more span surfaces.
    _stream_span = _logfire.span(
        "message.stream",
        conversation_id=conversation_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        case_id=conv.get("case_id"),
        attachment_count=len(attachment_ids or []),
    )
    _stream_span.__enter__()

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
        nonlocal full_content, paused
        try:
            async for event in handle_message(
                question=content,
                user_id=user_id,
                conversation_id=conversation_id,
                supabase=supabase,
                case_id=conv.get("case_id"),
                user_message_id=user_msg_id,
            ):
                event_type = event.get("type")

                if event_type == "token":
                    text = event.get("text", "")
                    full_content += text
                    await queue.put(_sse_event("token", {"text": text}))

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

                elif event_type == "referenced_existing_item":
                    # Phase E (§3.5 / §6.3): planner responder identified a
                    # prior artifact that covers the current question — no new
                    # card is published. Frontend highlights / chips the
                    # existing card by item_id.
                    await queue.put(_sse_event("referenced_existing_item", {
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
                _disconnect_detected = True
                _stream_outcome = "client_disconnect"
                logger.info("Client disconnected during streaming for conversation %s", conversation_id)
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # Re-check disconnect
            if item is None:
                _stream_outcome = "completed"
                break
            yield item
    except asyncio.CancelledError:
        _stream_outcome = "stream_cancelled"
        logger.info("SSE stream cancelled for conversation %s (client disconnect)", conversation_id)
        raise  # MUST re-raise per asyncio contract
    except Exception as e:
        _stream_outcome = "error"
        try:
            _stream_span.set_attribute("error", str(e))
            _stream_span.set_attribute("error.type", type(e).__name__)
        except Exception:
            pass
        logger.exception("Error in agent pipeline: %s", e)
        yield _sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"})
    finally:
        heartbeat_task.cancel()
        # Convo-1 forensics bug #1 fix: do NOT cancel the pipeline_task when
        # the SSE consumer exits early (Railway gateway timeout, browser nav,
        # repeat-send, explicit Stop). Before this fix, the cancel cascaded
        # into in-flight LLM streams and silently dropped:
        #   (a) ~$0.05–$0.10 of LLM spend per cancelled dispatch
        #   (b) the workspace_item that the pipeline would have published
        # New behavior: detach the pipeline to a module-level registry so it
        # runs to natural completion in the background, publishes the artifact,
        # and the user sees the card on next page load.
        #
        # The smoking-gun warning event still fires because "consumer exited
        # while pipeline was still running" is itself worth alerting on —
        # frequent occurrences may indicate broken UX or under-tuned gateway
        # timeouts.
        pipeline_already_done = pipeline_task.done()
        if not pipeline_already_done:
            try:
                _stream_span.set_attribute(
                    "pipeline_task.detached_to_background", True
                )
                _logfire.warning(
                    "message.stream.pipeline_detached",
                    conversation_id=conversation_id,
                    user_message_id=user_msg_id,
                    outcome=_stream_outcome,
                    disconnect_detected=_disconnect_detected,
                )
            except Exception:
                pass
            # Module-level set keeps the task alive past this function's exit
            # and prevents Python's "Task was destroyed but it is pending"
            # warning. add_done_callback removes the reference on completion.
            _inflight_pipelines.add(pipeline_task)
            pipeline_task.add_done_callback(_inflight_pipelines.discard)
        try:
            _stream_span.set_attributes({
                "outcome": _stream_outcome,
                "disconnect_detected": _disconnect_detected,
                "pipeline_task.done_before_detach": pipeline_already_done,
                "pipeline_task.detached": not pipeline_already_done,
                "paused": paused,
                "full_content_chars": len(full_content or ""),
            })
        except Exception:
            pass
        _stream_span.__exit__(None, None, None)


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
