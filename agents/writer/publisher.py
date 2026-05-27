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
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from backend.app.services.workspace_service import create_workspace_item
from shared.observability import get_logfire

from .deps import WriterDeps
from .lock import write_lock_column
from .models import WriterInput, WriterLLMOutput, WriterOutput

if TYPE_CHECKING:
    pass


# Matches the LLM-facing WI-{seq} alias. Case-insensitive to tolerate models
# that lowercase the prefix; the resolver canonicalises to upper internally.
_WI_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)


logger = logging.getLogger(__name__)
_logfire = get_logfire()


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
# ``agents.writer.lock`` so streaming callers can use ``agent_lock_scope``
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


def _persist_writer_references(
    *,
    deps: WriterDeps,
    llm_output: WriterLLMOutput,
    input: WriterInput,
    new_item_id: str,
    metadata: dict,
) -> int:
    """Project source-WI references into the new agent_writing WI's
    ``workspace_item_references`` rows + ``metadata.references``.

    Bug #1 fix (`agents_reports/convo_accbc49c_2/critical_bugs.md` §2).
    Resolves each ``CitationRef(wi="WI-N", n=K)`` in
    ``llm_output.citations_used`` to a source-WI ref row via
    ``input.research_items`` (which the runner populated with
    ``{item_id, wi_seq, ...}`` per research item), fetches the matching
    source rows from ``workspace_item_references``, and inserts new rows
    on the new WI numbered ``1..K`` in the order the writer emitted them.

    The new WI's ``metadata["references"]`` is mutated in place with a
    forensics-friendly view (``n``, ``source_wi``, ``source_n``,
    ``ref_id``, ``domain``) so the frontend's ``ReferencePanel`` can
    surface the disambiguation.

    Returns:
        Number of new ref rows persisted (0 if nothing matched or supabase
        is unavailable).
    """
    if not deps.supabase or not llm_output.citations_used:
        return 0

    # Build {wi_seq: source_item_id} from the runner-supplied research_items.
    source_alias_map: dict[int, str] = {}
    for item in (input.research_items or []):
        if not isinstance(item, dict):
            continue
        seq = item.get("wi_seq")
        iid = item.get("item_id") or item.get("artifact_id")
        if seq is not None and iid:
            try:
                source_alias_map[int(seq)] = str(iid)
            except (TypeError, ValueError):
                continue

    # Resolve each CitationRef to (source_item_id, source_n) preserving order.
    resolved_picks: list[tuple[str, int]] = []
    for cr in (llm_output.citations_used or []):
        wi_raw = (getattr(cr, "wi", "") or "").strip()
        m = _WI_RE.match(wi_raw)
        if not m:
            logger.warning(
                "writer.publisher: malformed CitationRef.wi=%r — skipping", wi_raw
            )
            continue
        seq = int(m.group(1))
        source_iid = source_alias_map.get(seq)
        if not source_iid:
            logger.warning(
                "writer.publisher: CitationRef wi=%s not in source_alias_map "
                "(known seqs=%s) — skipping",
                wi_raw,
                sorted(source_alias_map.keys()),
            )
            continue
        try:
            n_val = int(getattr(cr, "n", 0))
        except (TypeError, ValueError):
            logger.warning(
                "writer.publisher: CitationRef.n is not an int (wi=%s) — skipping",
                wi_raw,
            )
            continue
        if n_val < 1:
            logger.warning(
                "writer.publisher: CitationRef.n=%d < 1 (wi=%s) — skipping",
                n_val, wi_raw,
            )
            continue
        resolved_picks.append((source_iid, n_val))

    if not resolved_picks:
        return 0

    # Fetch source ref rows; batch by source_item_id to keep query count low.
    needed: dict[str, set[int]] = {}
    for sid, n in resolved_picks:
        needed.setdefault(sid, set()).add(n)

    source_refs: dict[tuple[str, int], dict] = {}
    for sid, n_set in needed.items():
        try:
            resp = (
                deps.supabase.table("workspace_item_references")
                .select(
                    "ref_id, item_id, domain, n, relevance, sub_queries, "
                    "content_word_count"
                )
                .eq("wi_id", sid)
                .in_("n", list(n_set))
                .execute()
            )
            for row in (getattr(resp, "data", None) or []):
                try:
                    source_refs[(sid, int(row["n"]))] = row
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception as exc:
            logger.warning(
                "writer.publisher: failed to fetch refs for source %s: %s",
                sid, exc,
            )

    # Build new workspace_item_references rows + the forensics view for metadata.
    new_ref_rows: list[dict] = []
    metadata_refs_view: list[dict] = []
    new_n = 0
    for (sid, source_n) in resolved_picks:
        src_row = source_refs.get((sid, source_n))
        if src_row is None:
            logger.warning(
                "writer.publisher: no source ref row for (%s, n=%d) — skipping",
                sid, source_n,
            )
            continue
        new_n += 1
        new_ref_rows.append({
            "wi_id": new_item_id,
            "domain": src_row["domain"],
            "n": new_n,
            "relevance": src_row.get("relevance") or "high",
            "used": True,
            "sub_queries": src_row.get("sub_queries") or [],
            "ref_id": src_row["ref_id"],
            "item_id": src_row.get("item_id"),
            "content_word_count": int(src_row.get("content_word_count") or 0),
        })
        reverse_alias = next(
            (f"WI-{seq}" for seq, iid in source_alias_map.items() if iid == sid),
            None,
        )
        metadata_refs_view.append({
            "n": new_n,
            "source_wi": reverse_alias,
            "source_n": source_n,
            "ref_id": src_row["ref_id"],
            "domain": src_row["domain"],
        })

    if new_ref_rows:
        try:
            deps.supabase.table("workspace_item_references").insert(
                new_ref_rows
            ).execute()
        except Exception as exc:
            logger.warning(
                "writer.publisher: workspace_item_references batch insert failed: %s",
                exc, exc_info=True,
            )

    # Surface to the frontend's ReferencePanel via metadata.references.
    if metadata_refs_view:
        metadata["references"] = metadata_refs_view
        try:
            deps.supabase.table("workspace_items").update(
                {"metadata": metadata}
            ).eq("item_id", new_item_id).execute()
        except Exception as exc:
            logger.warning(
                "writer.publisher: metadata update with references failed: %s",
                exc,
            )

    return len(new_ref_rows)


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
    # PII note: user_id not on this span (recoverable via Supabase join).
    # router.classify + dispatch.specialist already carry the user identity
    # for this turn.
    _pub_span = _logfire.span(
        "publish.workspace_item",
        kind="agent_writing",
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        message_id=input.message_id,
        subtype=input.subtype,
        revising_item_id=input.revising_item_id,
        confidence=getattr(llm_output, "confidence", None),
    )
    _pub_span.__enter__()
    try:
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

        # Bug #1 fix: serialize CitationRef objects to dicts for JSON storage in
        # metadata. The LLM emits a list of {wi, n} pairs; we persist them as
        # forensic record + use them below to project the new WI's
        # workspace_item_references rows.
        citations_used_serialized: list[dict] = []
        for cr in (llm_output.citations_used or []):
            try:
                citations_used_serialized.append({"wi": cr.wi, "n": int(cr.n)})
            except Exception:
                # Defensive: tolerate odd shapes (legacy callers, tests).
                continue

        metadata: dict = {
            "subtype": input.subtype,
            "model_used": deps.primary_model,
            "confidence": llm_output.confidence,
            "citations_used": citations_used_serialized,
            "notes": list(llm_output.notes_ar or []),
            "research_item_ids": research_item_ids,
            "detail_level": input.detail_level,
            "tone": input.tone,
            "revised_from": input.revising_item_id,
        }

        # Title preference: router-emitted task_label (content-derived Arabic
        # phrase) wins over the LLM's title_ar when set. Falls back to title_ar.
        title = (deps.task_label or "").strip() or llm_output.title_ar

        row = create_workspace_item(
            deps.supabase,
            input.user_id,
            kind="agent_writing",
            created_by="agent",
            title=title,
            conversation_id=input.conversation_id,
            case_id=input.case_id,
            message_id=input.message_id,
            agent_family="writing",
            content_md=content_md,
            metadata=metadata,
            describe_query=deps.describe_query,
        )

        # Tolerate either the new or legacy column name on the returned row so
        # tests that stub create_workspace_item with the old shape keep working.
        item_id = row.get("item_id") or row.get("artifact_id") or ""
        if not item_id:
            raise RuntimeError("agent_writer: publish returned no item_id")

        # 3b. Project source-WI references into the new WI's
        # workspace_item_references rows so the frontend's ReferencePanel
        # can resolve every (n) marker in the body. This is Bug #1's fix —
        # before this block the bypass-path writer turns landed (n) markers
        # in content_md with no backing rows, leaving `ReferencePanel` empty.
        references_persisted = _persist_writer_references(
            deps=deps,
            llm_output=llm_output,
            input=input,
            new_item_id=item_id,
            metadata=metadata,
        )
        try:
            _pub_span.set_attribute(
                "references_persisted", int(references_persisted)
            )
        except Exception:
            pass

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
            "title": title,
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

        try:
            _pub_span.set_attributes({
                "item_id": item_id,
                "title_chars": len(title or ""),
                "content_md_chars": len(content_md or ""),
                "outcome": "ok",
            })
        except Exception:
            pass

        return WriterOutput(
            item_id=item_id,
            kind="agent_writing",
            subtype=input.subtype,
            title=title,
            content_md=content_md,
            confidence=llm_output.confidence,
            notes=list(llm_output.notes_ar or []),
            metadata=metadata,
            sse_events=list(deps._events),
            locked_until=None,
            chat_summary=llm_output.chat_summary or "",
            key_findings=list(llm_output.key_findings or []),
        )
    except Exception as exc:
        try:
            _pub_span.set_attributes({
                "outcome": "error",
                "error": str(exc),
                "error.type": type(exc).__name__,
            })
        except Exception:
            pass
        raise
    finally:
        _pub_span.__exit__(None, None, None)


__all__ = [
    "publish_writer_result",
    "_assemble_content",
]
