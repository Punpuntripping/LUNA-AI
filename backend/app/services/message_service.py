"""
Message business logic.
Orchestrates the full message pipeline: save → context → stream → update.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import HTTPException, Request
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.audit_service import write_audit_log
from backend.app.services.case_service import get_user_id
from agents.orchestrator import handle_message
from shared import quota
from shared.config import get_settings
from shared.db.run import run_db
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# Arabic constants for pipeline timeout (design_2 fix_1)
_PIPELINE_TIMEOUT_MSG_AR = (
    "عذراً، استغرقت معالجة طلبك وقتاً أطول من المعتاد فتم إيقافها. "
    "يرجى المحاولة مرة أخرى."
)
_PIPELINE_TIMEOUT_PARTIAL_NOTE_AR = (
    "\n\n_توقّف إكمال هذا الرد لتجاوز المهلة الزمنية المحددة._"
)


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


@dataclass
class _ActiveRun:
    """One in-flight turn for a conversation.

    ``task`` is None during the setup window between reserving the slot (right
    after the dedup check) and the pipeline task being spawned. That window
    now contains awaits (run_db inserts, quota check) and a yield, so a client
    disconnect inside it raises CancelledError/GeneratorExit past the
    early-return handlers and can leak an unbound reservation. ``reserved_at``
    lets the dedup guard reclaim such a leaked slot after
    ``_RESERVATION_STALE_S`` instead of locking the conversation forever.
    """

    assistant_msg_id: str
    task: asyncio.Task | None = None
    reserved_at: float = dataclasses.field(default_factory=time.monotonic)


# An unbound reservation (task=None) older than this is considered leaked —
# the setup window is a few DB round-trips; even with pathological quota
# rehydration + 15s httpx timeouts it cannot legitimately take this long.
_RESERVATION_STALE_S = 180.0


# Per-conversation in-flight registry, keyed by conversation_id. An entry lives
# from the moment a turn reserves its slot until that turn's pipeline task
# completes — INCLUDING background completion after an SSE detach. A second send
# for a conversation that already has a live entry is rejected (see the dedup
# guard at the top of send_message_stream) so one question can't bill two full
# pipeline runs. Keyed per conversation: other conversations stream freely in
# parallel. This is distinct from `_inflight_pipelines` (a GC-keepalive set for
# detached tasks); both can hold the same task.
_active_runs: dict[str, _ActiveRun] = {}


def _is_valid_uuid(value: str) -> bool:
    """Cheap pre-flight UUID syntax check.

    The frontend can briefly hold an optimistic placeholder id like
    ``optimistic-1779397580677`` for a conversation that hasn't been
    persisted yet. If that placeholder leaks into a request URL, passing it
    straight to Supabase produces a ``invalid input syntax for type uuid``
    error that bubbles up as 500 and trips the SSE retry loop in
    ``frontend/hooks/use-chat.ts`` (the user sees «فشل الاتصال بعد عدة
    محاولات»).

    Validating up front lets us return a clean 404 (non-retryable) and keep
    the error surface honest — the conversation genuinely doesn't exist.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def verify_conversation_ownership(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict:
    """Verify conversation exists and belongs to user. Returns conversation row."""
    if not _is_valid_uuid(conversation_id):
        # Optimistic placeholder or malformed id — treat as "not found" so the
        # frontend gets a non-retryable 404 with a clean Arabic detail string.
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.CONV_NOT_FOUND,
            detail="المحادثة غير موجودة",
        )
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
    # ``verify_conversation_ownership`` already short-circuits on invalid UUID
    # via the _is_valid_uuid pre-flight, so the optimistic-placeholder case
    # is handled here transparently.
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
            # Window B Tasks 5–7: pass the persisted linkage through to the UI
            # so a refresh / scroll-load still shows the source chip and the
            # clickable [n] citations on prior assistant messages.
            "artifact_ids": m.get("artifact_ids"),
            "referenced_item_ids": m.get("referenced_item_ids"),
        })

    return {
        "messages": enriched,
        "has_more": has_more,
    }


# ── Module-level sync helpers for run_db wraps ────────────────────────────────
# Each wraps a single Supabase operation so it can be dispatched off the event
# loop via `await run_db(helper, supabase, ...)`. Keeping them as named
# functions (rather than lambdas) makes stack traces readable and satisfies
# the run_db pattern that passes fn + args.

def _insert_user_message(
    supabase: SupabaseClient,
    user_msg_id: str,
    conversation_id: str,
    content: str,
) -> None:
    supabase.table("messages").insert({
        "message_id": user_msg_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": content,
    }).execute()


def _insert_attachment_links(
    supabase: SupabaseClient,
    user_msg_id: str,
    attachment_ids: list,
) -> None:
    rows = [
        {"message_id": user_msg_id, "document_id": doc_id}
        for doc_id in attachment_ids
    ]
    supabase.table("message_attachments").insert(rows).execute()


def _estimate_ocr_pages(supabase: SupabaseClient, attachment_ids: list) -> int:
    """Project the OCR page total for this message's attachments so the quota
    gate counts a multi-page document accurately *before* OCR runs.

    Per attachment, prefers the authoritative post-OCR count (``metadata.ocr_pages``,
    present on a re-sent attachment), else the upload-time client estimate
    (``metadata.page_count``), else a 1-page floor (unknown). Falls back to one
    page per attachment on any query failure — the gate must still run, and the
    post-OCR settle bills the real count regardless.
    """
    if not attachment_ids:
        return 0
    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id, metadata")
            .in_("item_id", list(attachment_ids))
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR page estimate query failed; using 1/attachment: %s", e)
        return len(attachment_ids)

    by_id = {
        r.get("item_id"): (r.get("metadata") or {})
        for r in (getattr(result, "data", None) or [])
    }
    total = 0
    for aid in attachment_ids:
        meta = by_id.get(aid) or {}
        raw = meta.get("ocr_pages") or meta.get("page_count")
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            pages = 0
        total += pages if pages > 0 else 1  # unknown → 1-page floor
    return total


def _insert_assistant_placeholder(
    supabase: SupabaseClient,
    assistant_msg_id: str,
    conversation_id: str,
) -> None:
    supabase.table("messages").insert({
        "message_id": assistant_msg_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": "",
        "model": "ريحان",
    }).execute()


def _delete_message_row(supabase: SupabaseClient, message_id: str) -> None:
    supabase.table("messages").delete().eq("message_id", message_id).execute()


def _update_message_content(
    supabase: SupabaseClient,
    message_id: str,
    update_data: dict,
) -> None:
    supabase.table("messages").update(update_data).eq("message_id", message_id).execute()


def _update_conversation_meta(
    supabase: SupabaseClient,
    conversation_id: str,
    conv_update: dict,
) -> None:
    supabase.table("conversations").update(conv_update).eq(
        "conversation_id", conversation_id
    ).execute()


# ─────────────────────────────────────────────────────────────────────────────


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

    # 0. In-flight dedup guard (per conversation). If a pipeline is already
    # running for THIS conversation — because the user resent after the first
    # SSE stream silently dropped, or the frontend auto-reconnect re-POSTed —
    # do NOT start a second pipeline. That would re-run the whole deep_search
    # for the same question and bill it twice (forensics: convo cb348fe6, ~2×
    # cost). Point the client at the existing in-flight assistant message and
    # return without saving a duplicate user message. The detach-to-background
    # policy keeps the first run alive, so its answer still surfaces on
    # completion. The check is keyed on conversation_id: other conversations
    # are unaffected. ``task.done()`` lets a finished-but-not-yet-cleaned slot
    # fall through (the done_callback clears it asynchronously).
    existing = _active_runs.get(conversation_id)
    if (
        existing is not None
        and existing.task is None
        and time.monotonic() - existing.reserved_at > _RESERVATION_STALE_S
    ):
        # Leaked reservation: a previous send's setup window was killed by a
        # client disconnect (CancelledError/GeneratorExit bypasses the
        # early-return cleanup) and never spawned its task. Reclaim instead of
        # locking this conversation until restart.
        _logfire.warning(
            "message.stream.stale_reservation_reclaimed",
            conversation_id=conversation_id,
            stale_assistant_message_id=existing.assistant_msg_id,
            age_s=round(time.monotonic() - existing.reserved_at, 1),
        )
        _active_runs.pop(conversation_id, None)
        existing = None
    if existing is not None and (existing.task is None or not existing.task.done()):
        _logfire.warning(
            "message.stream.duplicate_send_rejected",
            conversation_id=conversation_id,
            existing_assistant_message_id=existing.assistant_msg_id,
        )
        yield _sse_event("duplicate", {
            "assistant_message_id": existing.assistant_msg_id,
            "conversation_id": conversation_id,
            "detail": "ما زال يتم إنشاء الرد على رسالتك السابقة وسيظهر هنا حال اكتماله.",
        })
        return

    # 0b. Reserve the per-conversation in-flight slot SYNCHRONOUSLY, immediately
    # after the dedup check and before ANY await. A placeholder message_id is
    # used; the real assistant_msg_id is written into the slot once the
    # placeholder is inserted below. This closes the race: if a concurrent send
    # arrives during any of the awaits that follow (user-msg insert, quota check,
    # placeholder insert) it will see task=None and block. Every early-return
    # path between here and the task-spawn must explicitly clear the slot.
    _SLOT_PLACEHOLDER = "__reserving__"
    _active_runs[conversation_id] = _ActiveRun(assistant_msg_id=_SLOT_PLACEHOLDER)

    # 1. Save user message BEFORE AI call (Absolute Rule #7)
    user_msg_id = str(uuid.uuid4())
    try:
        await run_db(
            _insert_user_message,
            supabase, user_msg_id, conversation_id, content,
        )
    except Exception as e:
        logger.exception("Error saving user message: %s", e)
        _active_runs.pop(conversation_id, None)  # release slot — task never created
        yield _sse_event("error", {"detail": "حدث خطأ أثناء حفظ الرسالة"})
        return

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="create",
        resource_type="message",
        resource_id=user_msg_id,
    )

    # 1b. Link attachments to user message (if any)
    if attachment_ids:
        try:
            await run_db(_insert_attachment_links, supabase, user_msg_id, attachment_ids)
        except Exception as e:
            logger.warning("Error linking attachments: %s", e)

    # 1c. Quota gate — fires once per message, before OCR + router. Three
    # independent meters (ocr / ord / web): if any (meter, period) is over
    # limit, emit a quota_exceeded SSE event and end the stream without
    # spawning the pipeline. The user message is already saved (kept in
    # history); no assistant placeholder is created.
    # Project OCR pages from each attachment's stored page count (client-reported
    # at upload; real ocr_pages on a re-sent file) so the gate counts multi-page
    # documents accurately before OCR runs — not 1 page per file. Falls back to a
    # 1-page floor per attachment when unknown. The post-OCR settle remains the
    # authoritative billing count; this only drives the pre-send block decision.
    est_ocr_pages = 0
    if attachment_ids:
        try:
            est_ocr_pages = await run_db(
                _estimate_ocr_pages, supabase, attachment_ids
            )
        except Exception:  # noqa: BLE001
            est_ocr_pages = len(attachment_ids)
    try:
        await quota.check(
            getattr(request.app.state, "redis", None),
            supabase,
            user_id,
            needs_ocr=bool(attachment_ids),
            est_ocr_pages=est_ocr_pages,
            needs_ord=True,
            needs_web=False,  # future skill
        )
    except quota.PlanInactive as pi:
        # No plan assigned (users.plan_id IS NULL) — account locked until the
        # operator activates it in Supabase. Same SSE event as quota_exceeded
        # so the existing banner renders the Arabic notice.
        _logfire.info("message.plan_inactive", conversation_id=conversation_id)
        _active_runs.pop(conversation_id, None)  # release slot — task never created
        yield _sse_event("quota_exceeded", pi.to_event_payload())
        return
    except quota.QuotaExceeded as qe:
        _logfire.info(
            "message.quota_exceeded",
            conversation_id=conversation_id,
            meter=qe.meter,
            period=qe.period,
            used=float(qe.used),
            limit=float(qe.limit),
        )
        _active_runs.pop(conversation_id, None)  # release slot — task never created
        yield _sse_event("quota_exceeded", qe.to_event_payload())
        return
    except quota.QuotaUnavailable as qu:
        # Quota store is degraded — fail closed so we don't silently allow
        # unlimited spend. No assistant placeholder has been created yet, so
        # no orphan row.
        _logfire.warn(
            "message.quota_unavailable",
            conversation_id=conversation_id,
            meter=qu.meter,
            period=qu.period,
        )
        _active_runs.pop(conversation_id, None)  # release slot — task never created
        yield _sse_event("error", {
            "detail": quota.QUOTA_UNAVAILABLE_AR,
            "code": "QUOTA_UNAVAILABLE",
        })
        return

    # 2. Create assistant message placeholder
    assistant_msg_id = str(uuid.uuid4())
    try:
        await run_db(
            _insert_assistant_placeholder,
            supabase, assistant_msg_id, conversation_id,
        )
    except Exception as e:
        logger.exception("Error creating assistant placeholder: %s", e)
        _active_runs.pop(conversation_id, None)  # release slot — task never created
        yield _sse_event("error", {"detail": "حدث خطأ داخلي"})
        return

    # 2b. Update the reserved slot with the real assistant_msg_id now that the
    # placeholder row exists. No await between here and the task spawn below, so
    # the slot is fully coherent before any concurrent path can re-inspect it.
    _active_runs[conversation_id] = _ActiveRun(assistant_msg_id=assistant_msg_id)

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

    # Window B Tasks 5–7: capture every workspace_item produced by this turn
    # (and every prior card the planner pointed back to via build_artifact=False)
    # so we can write them onto the messages row at `done` time AND echo them
    # on the outgoing `done` SSE event. Without this, MessageBubble's
    # `hasArtifacts` gate (frontend/components/chat/MessageBubble.tsx:105)
    # never flips True and the inline source chip + clickable [n] citations
    # stay dark for every assistant message.
    captured_artifact_ids: list[str] = []
    captured_referenced_ids: list[str] = []

    async def pipeline_producer() -> None:
        """Run agent pipeline and put SSE events on the queue."""
        nonlocal full_content, paused
        try:
            async with asyncio.timeout(get_settings().LUNA_PIPELINE_TIMEOUT_S):
                async for event in handle_message(
                    question=content,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    supabase=supabase,
                    case_id=conv.get("case_id"),
                    user_message_id=user_msg_id,
                    assistant_message_id=assistant_msg_id,
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
                        item_id = event.get("item_id")
                        # Capture for the messages.artifact_ids write at done time.
                        # Skip None / duplicates defensively — the orchestrator emits
                        # one event per publish, but a future retry path could emit
                        # twice and we don't want duplicate uuids in the array.
                        if item_id and item_id not in captured_artifact_ids:
                            captured_artifact_ids.append(item_id)
                        payload = {
                            "item_id": item_id,
                            "kind": event.get("kind"),
                            "title": event.get("title", ""),
                            "created_by": event.get("created_by"),
                        }
                        if event.get("subtype") is not None:
                            payload["subtype"] = event["subtype"]
                        await queue.put(_sse_event("workspace_item_created", payload))

                    elif event_type == "workspace_item_updated":
                        updated_id = event.get("item_id")
                        # Capture for the messages.artifact_ids write at done time.
                        # The artifact-editor tool edits an existing item in place
                        # (§7) and emits this event instead of workspace_item_created.
                        # That edited item must still reach messages.artifact_ids so
                        # _load_wi_provenance tags THIS edit turn — otherwise a
                        # "refine the last artifact" follow-up routes provenance-blind.
                        # Same defensive guard as the created branch: skip None /
                        # duplicates (a future retry could emit twice).
                        if updated_id and updated_id not in captured_artifact_ids:
                            captured_artifact_ids.append(updated_id)
                        await queue.put(_sse_event("workspace_item_updated", {
                            "item_id": updated_id,
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
                        ref_id = event.get("item_id")
                        if ref_id and ref_id not in captured_referenced_ids:
                            captured_referenced_ids.append(ref_id)
                        await queue.put(_sse_event("referenced_existing_item", {
                            "item_id": ref_id,
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
                            # Delete the empty placeholder we created above — the
                            # real question message was inserted by _record_deferred.
                            try:
                                await run_db(
                                    _delete_message_row, supabase, assistant_msg_id
                                )
                            except Exception as e:
                                logger.warning(
                                    "Could not delete paused assistant placeholder: %s", e
                                )
                        else:
                            try:
                                update_data: dict = {"content": full_content}
                                if usage:
                                    update_data["prompt_tokens"] = usage.get("prompt_tokens", 0)
                                    update_data["completion_tokens"] = usage.get(
                                        "completion_tokens", 0
                                    )
                                # Window B Tasks 5–7: persist the linkage. Only
                                # write when non-empty so legacy / mock-RAG / Q&A
                                # rows keep NULL (frontend treats null === [] for
                                # gating, but NULL is the cleaner signal that "no
                                # agent run published an artifact for this turn").
                                if captured_artifact_ids:
                                    update_data["artifact_ids"] = captured_artifact_ids
                                if captured_referenced_ids:
                                    update_data["referenced_item_ids"] = captured_referenced_ids

                                await run_db(
                                    _update_message_content,
                                    supabase, assistant_msg_id, update_data,
                                )
                            except Exception as e:
                                logger.exception("Error updating assistant message: %s", e)

                        # 6. Update conversation metadata
                        try:
                            now = datetime.now(timezone.utc).isoformat()

                            conv_update: dict = {
                                "updated_at": now,
                            }
                            current_count = conv.get("message_count", 0)
                            # On pause: only the user message was saved (the
                            # question row is inserted by _record_deferred
                            # separately).
                            msg_delta = 1 if paused else 2  # user + assistant
                            conv_update["message_count"] = current_count + msg_delta

                            # Auto-title from first message
                            if current_count == 0:
                                title = content[:60].strip()
                                if len(content) > 60:
                                    title += "..."
                                conv_update["title_ar"] = title

                            await run_db(
                                _update_conversation_meta,
                                supabase, conversation_id, conv_update,
                            )
                        except Exception as e:
                            logger.exception("Error updating conversation: %s", e)

                        # 7. Yield done event. Echo the captured arrays so the
                        # frontend cache injection in use-chat.ts can light up
                        # the chip + citation linking immediately, without
                        # waiting for the next messages-list refetch.
                        await queue.put(_sse_event("done", {
                            "message_id": assistant_msg_id,
                            "usage": {
                                "prompt_tokens": usage.get("prompt_tokens", 0),
                                "completion_tokens": usage.get("completion_tokens", 0),
                            },
                            "artifact_ids": captured_artifact_ids or None,
                            "referenced_item_ids": captured_referenced_ids or None,
                        }))

        except TimeoutError:
            # Pipeline exceeded LUNA_PIPELINE_TIMEOUT_S. Log, update the
            # placeholder row with whatever partial content arrived, and
            # send an error SSE event so the frontend can show the Arabic
            # message. The asyncio.timeout() cancel unwinds through
            # collect_llm_calls.__exit__ → flush + quota settle by contract
            # (usage_sink.py), so all LLM calls made up to this point ARE
            # already billed — no refund needed; the timeout only stops
            # future spend.
            _logfire.error(
                "message.stream.pipeline_timeout",
                conversation_id=conversation_id,
                assistant_message_id=assistant_msg_id,
                user_message_id=user_msg_id,
                timeout_s=get_settings().LUNA_PIPELINE_TIMEOUT_S,
                partial_chars=len(full_content),
                paused=paused,
            )
            try:
                if paused:
                    # A pause already happened but the 'done' cleanup didn't
                    # run — the question row exists; delete the empty
                    # placeholder (mirrors the paused branch in 'done' above).
                    await run_db(_delete_message_row, supabase, assistant_msg_id)
                elif full_content:
                    await run_db(
                        _update_message_content,
                        supabase,
                        assistant_msg_id,
                        {
                            "content": full_content + _PIPELINE_TIMEOUT_PARTIAL_NOTE_AR,
                            "metadata": {"kind": "pipeline_timeout", "partial": True},
                        },
                    )
                else:
                    await run_db(
                        _update_message_content,
                        supabase,
                        assistant_msg_id,
                        {
                            "content": _PIPELINE_TIMEOUT_MSG_AR,
                            "metadata": {"kind": "pipeline_timeout"},
                        },
                    )
            except Exception:
                logger.warning("pipeline_timeout: placeholder cleanup failed", exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": _PIPELINE_TIMEOUT_MSG_AR,
                "code": "PIPELINE_TIMEOUT",
            }))
        except Exception as e:
            logger.exception("Error in agent pipeline: %s", e)
            await queue.put(_sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة"}))
        finally:
            await queue.put(None)  # Sentinel: pipeline complete

    # Ownership guard: if our reservation was reclaimed as stale while setup
    # was pathologically slow (and possibly taken over by a newer send), abort
    # rather than spawn a second pipeline for the same conversation — the
    # double-billing this dedup exists to prevent.
    _slot = _active_runs.get(conversation_id)
    if _slot is None or _slot.assistant_msg_id != assistant_msg_id:
        _logfire.warning(
            "message.stream.reservation_lost_abort",
            conversation_id=conversation_id,
            assistant_message_id=assistant_msg_id,
        )
        await run_db(_delete_message_row, supabase, assistant_msg_id)
        yield _sse_event("error", {"detail": "حدث خطأ أثناء معالجة الرسالة، يرجى إعادة المحاولة"})
        return

    heartbeat_task = asyncio.create_task(heartbeat_producer())
    pipeline_task = asyncio.create_task(pipeline_producer())

    # Bind the spawned task to the reserved in-flight slot and arrange cleanup.
    # The done-callback clears the slot when the pipeline finishes — whether it
    # completes while the consumer is attached OR in the background after a
    # detach — so a later send for this conversation is allowed once the run is
    # truly over. The identity guard avoids a stale callback clobbering a newer
    # run that legitimately took the slot.
    _slot.task = pipeline_task

    def _clear_active_run(_done: asyncio.Task) -> None:
        cur = _active_runs.get(conversation_id)
        if cur is not None and cur.task is _done:
            _active_runs.pop(conversation_id, None)

    pipeline_task.add_done_callback(_clear_active_run)

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
