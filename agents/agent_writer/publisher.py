"""Persist a WriterLLMOutput as a workspace_item (kind='agent_writing').

This publisher turns the structured LLM output into:
    1. A markdown body (assembled from sections).
    2. A row in the ``workspace_items`` table with ``kind='agent_writing'``
       and ``created_by='agent'``. The post-026 schema no longer constrains
       subtype to an enum -- the WriterSubtype is written verbatim into
       ``metadata.subtype`` so contract / memo / legal_opinion /
       defense_brief / letter / summary all round-trip without the Cut-1
       defense_brief|letter -> memo fallback.
    3. A revision: if ``input.revising_item_id`` is provided, the old row is
       soft-deleted (``deleted_at = now()``) and a brand-new row is inserted.
       No in-place edit -- versioning falls out of soft-delete + new row.
    4. Lock semantics: writes ``locked_by_agent_until = now() + ttl`` on the
       row immediately after the insert, then clears it (sets to ``None``)
       at the end of publish. Cut-1 stored this in ``metadata.locked_until``
       as a stopgap; this Wave 8A revision uses the real column.
    5. SSE events: ``workspace_item_created`` + ``workspace_item_locked`` +
       ``workspace_item_unlocked`` per the wave_8 SSE vocabulary table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from backend.app.services.workspace_service import create_workspace_item

from .deps import WriterDeps
from .lock import write_lock_column
from .models import WriterInput, WriterLLMOutput, WriterOutput

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


def _assemble_content(llm_output: WriterLLMOutput) -> str:
    """Concatenate title + sections into a single markdown string.

    Layout::

        # <title_ar>

        ## <heading_1>

        <body_1>

        ## <heading_2>

        <body_2>
    """
    parts: list[str] = [f"# {llm_output.title_ar.strip()}", ""]
    for sec in llm_output.sections:
        heading = sec.heading_ar.strip()
        # Tolerate models that forget the leading ##.
        if heading and not heading.startswith("#"):
            heading = f"## {heading}"
        parts.append(heading)
        parts.append("")
        parts.append(sec.body_md.strip())
        parts.append("")
    return "\n".join(parts).rstrip()


def _soft_delete_revising(supabase, item_id: str) -> None:
    """Mark the revised row as deleted -- best-effort, never raises."""
    if not item_id:
        return
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        supabase.table("workspace_items").update(
            {"deleted_at": now_iso, "updated_at": now_iso}
        ).eq("item_id", item_id).execute()
    except Exception as exc:
        logger.warning(
            "agent_writer: soft-delete of revising row %s failed: %s",
            item_id, exc, exc_info=True,
        )


# ``_set_lock_column`` is preserved as a thin alias for backwards compatibility
# with any in-tree call sites; the canonical implementation lives in
# ``agents.agent_writer.lock`` so streaming callers can use ``agent_lock_scope``
# (Wave 8D) for heartbeat-style holds.
_set_lock_column = write_lock_column


def _emit(deps: WriterDeps, event: dict) -> None:
    """Append the event to the deps run-state and forward to emit_sse."""
    deps._events.append(event)
    if deps.emit_sse is not None:
        try:
            deps.emit_sse(event)
        except Exception:
            logger.debug("agent_writer: emit_sse failed", exc_info=True)


async def publish_writer_result(
    llm_output: WriterLLMOutput,
    input: WriterInput,
    deps: WriterDeps,
) -> WriterOutput:
    """Persist a writer LLM output as a workspace_item and emit SSE events.

    Pipeline:
        1. (Revision case) soft-delete ``input.revising_item_id``.
        2. Assemble ``content_md`` from sections.
        3. Insert a new ``workspace_items`` row via ``create_workspace_item``
           with ``kind='agent_writing'`` and ``created_by='agent'``.
        4. Acquire lock: write ``locked_by_agent_until = now + ttl`` directly
           on the column. Emit ``workspace_item_created`` +
           ``workspace_item_locked``.
        5. Release lock: write ``locked_by_agent_until = NULL``.
           Emit ``workspace_item_unlocked``.
        6. Return a populated ``WriterOutput``.

    Returns:
        WriterOutput with ``locked_until=None`` (lock released by end of
        publish in Cut-1; Wave 8D will hold the lock through token-by-token
        streaming).
    """
    # 1. Revision: soft-delete the row being revised.
    if input.revising_item_id:
        _soft_delete_revising(deps.supabase, input.revising_item_id)

    # 2. Build the markdown body.
    content_md = _assemble_content(llm_output)

    # 3. Build metadata BEFORE the insert so the same dict ships into the row
    # AND drives the SSE event payloads. The subtype is preserved verbatim --
    # post-026 there is no enum constraint to dodge.
    research_item_ids = [
        item.get("item_id") or item.get("artifact_id")
        for item in (input.research_items or [])
        if isinstance(item, dict)
    ]
    research_item_ids = [r for r in research_item_ids if r]

    metadata: dict = {
        "subtype": input.subtype,
        "model_used": deps.primary_model,
        "confidence": llm_output.confidence,
        "citations_used": list(llm_output.citations_used or []),
        "notes": list(llm_output.notes_ar or []),
        "research_item_ids": research_item_ids,
        "detail_level": input.detail_level,
        "tone": input.tone,
        "revised_from": input.revising_item_id,
    }

    row = create_workspace_item(
        deps.supabase,
        input.user_id,
        kind="agent_writing",
        created_by="agent",
        title=llm_output.title_ar,
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        message_id=input.message_id,
        agent_family="end_services",
        content_md=content_md,
        metadata=metadata,
    )

    # Tolerate either the new or legacy column name on the returned row so
    # tests that stub create_workspace_item with the old shape keep working.
    item_id = row.get("item_id") or row.get("artifact_id") or ""
    if not item_id:
        raise RuntimeError("agent_writer: publish returned no item_id")

    # 4. Acquire lock on the real column.
    locked_until_dt = datetime.now(timezone.utc) + timedelta(
        seconds=deps.lock_ttl_seconds
    )
    locked_until_iso = locked_until_dt.isoformat()
    _set_lock_column(deps.supabase, item_id, locked_until_iso)

    # SSE events.
    _emit(deps, {
        "type": "workspace_item_created",
        "item_id": item_id,
        "kind": "agent_writing",
        "subtype": input.subtype,
        "title": llm_output.title_ar,
        "created_by": "agent",
    })
    _emit(deps, {
        "type": "workspace_item_locked",
        "item_id": item_id,
        "locked_until": locked_until_iso,
    })

    # 5. Release lock immediately for Cut-1 (no token-stream yet -- the full
    # body arrived in one LLM response). Wave 8D will hold the lock through
    # the streaming window.
    _set_lock_column(deps.supabase, item_id, None)
    _emit(deps, {"type": "workspace_item_unlocked", "item_id": item_id})

    return WriterOutput(
        item_id=item_id,
        kind="agent_writing",
        subtype=input.subtype,
        title=llm_output.title_ar,
        content_md=content_md,
        confidence=llm_output.confidence,
        notes=list(llm_output.notes_ar or []),
        metadata=metadata,
        sse_events=list(deps._events),
        locked_until=None,
        chat_summary=llm_output.chat_summary or "",
        key_findings=list(llm_output.key_findings or []),
    )


__all__ = [
    "publish_writer_result",
    "_assemble_content",
]
