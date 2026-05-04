"""Publish a deep_search ``AggregatorOutput`` to the workspace.

This module is the **publishing adapter**, NOT an LLM. It takes the
post-validation aggregator output produced by ``agents/deep_search_v4`` and:

  1. Inserts one row into the ``workspace_items`` table with
     ``kind='agent_search'`` and ``created_by='agent'``. The original
     "legal_synthesis" descriptor lives on as ``metadata.subtype`` so the
     frontend chip color/icon survives the schema rename.
  2. Best-effort persists the forensic backing rows
     (``retrieval_artifacts`` + ``reranker_runs``).
  3. Returns the SSE events the orchestrator must forward -- both the new
     ``workspace_item_created`` event.
"""
from __future__ import annotations

import logging

from agents.agent_search.deps import SearchPublishDeps
from agents.agent_search.models import SearchPublishInput, SearchPublishOutput
from backend.app.services.workspace_service import create_workspace_item
from backend.app.services.retrieval_artifacts_service import (
    save_reranker_runs,
    save_retrieval_artifact,
)

_FALLBACK_TITLE = "إجابة البحث العميق"
_TITLE_MAX = 80


def _build_title(input: SearchPublishInput) -> str:
    artifact = getattr(input.agg_output, "artifact", None)
    if artifact is not None and getattr(artifact, "title", None):
        return artifact.title
    if input.original_query:
        return input.original_query[:_TITLE_MAX]
    return _FALLBACK_TITLE


def _build_content_md(input: SearchPublishInput) -> str:
    artifact = getattr(input.agg_output, "artifact", None)
    if artifact is not None and getattr(artifact, "content", None):
        return artifact.content
    return getattr(input.agg_output, "synthesis_md", "") or ""


def _build_metadata(input: SearchPublishInput) -> dict:
    """Merge artifact metadata (if any) with the deep_search-specific keys
    the frontend needs for chip color, citation popovers, and forensic joins.

    Sets ``subtype="legal_synthesis"`` so the frontend can keep treating
    deep_search outputs as a distinct chip style after migration 026 dropped
    the ``artifact_type`` column.
    """
    artifact = getattr(input.agg_output, "artifact", None)
    metadata: dict = (
        dict(artifact.metadata) if artifact is not None and getattr(artifact, "metadata", None) else {}
    )
    references = (
        artifact.references_json
        if artifact is not None and getattr(artifact, "references_json", None) is not None
        else []
    )
    metadata.update(
        {
            "subtype": "legal_synthesis",
            "references": references,
            "confidence": getattr(input.agg_output, "confidence", None),
            "detail_level": input.detail_level,
            "ura_log_id": getattr(input.agg_output, "log_id", "") or "",
        }
    )
    return metadata


async def _persist_forensics(
    input: SearchPublishInput,
    deps: SearchPublishDeps,
    item_id: str,
    logger: logging.Logger,
) -> None:
    """Best-effort persistence of retrieval_artifacts + reranker_runs.

    Wraps the same try/except envelope as the original inline block: a
    failure here is logged at WARNING and swallowed, never raised. The
    main item has already been persisted by the time this runs, so a
    hiccup in the forensic layer cannot lose user-visible data.

    Note: ``retrieval_artifacts.artifact_id`` continues to point at
    ``workspace_items.item_id`` -- the FK auto-followed the ALTER...RENAME
    in migration 026. Naming is unchanged for compatibility with
    ``save_retrieval_artifact``'s signature.
    """
    if input.ura is None:
        return
    try:
        ra_row = await save_retrieval_artifact(
            deps.supabase,
            user_id=input.user_id,
            conversation_id=input.conversation_id,
            message_id=input.message_id,
            artifact_id=item_id,
            ura=input.ura,
            duration_ms=None,  # TODO: wire end-to-end timing
        )
        if ra_row and ra_row.get("ura_id"):
            await save_reranker_runs(
                deps.supabase,
                ura_id=ra_row["ura_id"],
                reg_rqrs=list(input.reg_rqrs or []),
                comp_rqrs=list(input.comp_rqrs or []),
                case_rqrs=list(input.case_rqrs or []),
                per_executor_stats=dict(input.per_executor_stats or {}),
            )
    except Exception as exc:
        logger.warning(
            "retrieval_artifacts / reranker_runs persist failed: %s",
            exc,
            exc_info=True,
        )


async def publish_search_result(
    input: SearchPublishInput,
    deps: SearchPublishDeps,
) -> SearchPublishOutput:
    """Persist a deep_search aggregator output and return the SSE events
    the orchestrator should forward.

    The workspace_item insert is **not** wrapped in try/except: if the main
    persistence fails we want the orchestrator to see the exception and
    deliver a proper error response. The forensic-layer persistence
    (retrieval_artifacts + reranker_runs) IS wrapped, mirroring the
    behavior of the original inline block.

    SSE events: emits ``workspace_item_created``. Cut-1's legacy
    ``artifact_created`` alias was dropped in Wave 8B once the frontend
    rename pass replaced its event handler.
    """
    logger = deps.logger or logging.getLogger("agents.agent_search.publisher")

    title = _build_title(input)
    content_md = _build_content_md(input)
    metadata = _build_metadata(input)

    row = create_workspace_item(
        deps.supabase,
        input.user_id,
        kind="agent_search",
        created_by="agent",
        title=title,
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        message_id=input.message_id,
        agent_family="deep_search",
        content_md=content_md,
        metadata=metadata,
    )

    # Tolerate either the new or legacy column name on the returned row so
    # tests that stub create_workspace_item with the old shape keep working.
    item_id = row.get("item_id") or row.get("artifact_id") or ""
    if not item_id:
        raise RuntimeError("agent_search: publish returned no item_id")

    # Forensic persistence is best-effort -- failure here is logged but never
    # crashes the publish. Done AFTER the main insert so the user-visible
    # item is durable even if the backing tables hiccup.
    await _persist_forensics(input, deps, item_id, logger)

    # Project Reference -> dict so message_service.py's Reference->Citation
    # mapper has the fields it expects (ref_id, regulation_title, article_num,
    # relevance). Pydantic v2 model_dump for the Reference list.
    references_payload: list[dict] = []
    refs = getattr(input.agg_output, "references", None) or []
    for ref in refs:
        try:
            references_payload.append(
                ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
            )
        except Exception:
            continue

    sse_events: list[dict] = [
        {
            "type": "workspace_item_created",
            "item_id": item_id,
            "kind": "agent_search",
            "title": row.get("title", title),
            "subtype": "legal_synthesis",
            "created_by": "agent",
        },
    ]

    # Citations event: makes <CitationPills> render under the assistant
    # message bubble. message_service maps each Reference dict to the
    # frontend Citation shape via _reference_to_citation.
    if references_payload:
        sse_events.append({
            "type": "citations",
            "articles": references_payload,
        })

    return SearchPublishOutput(item_id=item_id, sse_events=sse_events)
