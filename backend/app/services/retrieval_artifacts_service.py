"""Retrieval artifact + reranker-runs persistence helpers.

These live in the service layer because they must be callable from inside
the chat SSE stream (``agents/orchestrator.py::_run_pydantic_ai_task``)
without dragging route-layer imports. Both functions are best-effort:
any DB failure is logged and swallowed -- NEVER re-raised into the chat
stream -- so a broken persistence layer can't crash a user turn.

The Supabase SDK is sync, so we call it directly from async functions
(see the established project pattern in CLAUDE.md memory: "Sync Supabase
client used in async route handlers").
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


__all__ = ["save_retrieval_artifact", "save_reranker_runs"]


# ---------------------------------------------------------------------------
# retrieval_artifacts
# ---------------------------------------------------------------------------


async def save_retrieval_artifact(
    supabase: SupabaseClient,
    *,
    user_id: str,
    conversation_id: str | None,
    message_id: str | None,
    artifact_id: str | None,
    ura: Any,  # agents.deep_search_v4.ura.schema.UnifiedRetrievalArtifact
    duration_ms: int | None,
) -> dict:
    """Insert one row into ``retrieval_artifacts``.

    Returns the inserted row dict on success, or an empty dict on any
    failure. Errors are logged but NEVER raised -- this call runs inside
    the live SSE stream and must not break streaming.
    """
    if ura is None:
        logger.warning("save_retrieval_artifact called with ura=None; skipping")
        return {}

    try:
        ura_json = ura.model_dump()
    except Exception:
        logger.exception("save_retrieval_artifact: ura.model_dump() failed")
        return {}

    produced_by = dict(getattr(ura, "produced_by", {}) or {})
    schema_version = str(getattr(ura, "schema_version", "2.0") or "2.0")
    high_count = len(getattr(ura, "high_results", []) or [])
    medium_count = len(getattr(ura, "medium_results", []) or [])

    payload: dict[str, Any] = {
        "user_id": user_id,
        "ura_json": ura_json,
        "schema_version": schema_version,
        "high_count": high_count,
        "medium_count": medium_count,
        "produced_by": produced_by,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if message_id is not None:
        payload["message_id"] = message_id
    if artifact_id is not None:
        payload["artifact_id"] = artifact_id
    if duration_ms is not None:
        payload["duration_ms"] = int(duration_ms)

    try:
        result = supabase.table("retrieval_artifacts").insert(payload).execute()
    except Exception:
        logger.exception("save_retrieval_artifact: insert failed")
        return {}

    data = getattr(result, "data", None) or []
    if not data:
        logger.warning("save_retrieval_artifact: insert returned no data")
        return {}
    return data[0]


# ---------------------------------------------------------------------------
# reranker_runs
# ---------------------------------------------------------------------------


# Map from agent family identifier -> RerankerQueryResult list -> rows.
# Stays in lockstep with build_ura_from_phases absorption order (reg, then
# compliance, then case -- see agents/deep_search_v3/ura/merger.py).
_EXECUTOR_ORDER: tuple[str, ...] = ("reg_search", "compliance_search", "case_search")


def _kept_result_row(result: Any) -> dict:
    """Project a domain URA result down to the serializable summary stored
    in ``reranker_runs.kept_results``.

    Keeps only the fields forensic analysis actually needs so the JSONB
    column stays small (full content lives on ``retrieval_artifacts.ura_json``).
    """
    return {
        "ref_id": getattr(result, "ref_id", "") or "",
        "relevance": getattr(result, "relevance", "") or "",
        "reasoning": getattr(result, "reasoning", "") or "",
        "source_type": getattr(result, "source_type", "") or "",
        "title": getattr(result, "title", "") or "",
    }


def _build_row(
    *,
    ura_id: str,
    agent_family: str,
    sub_query_index: int,
    rqr: Any,
) -> dict:
    """Build one ``reranker_runs`` row from a shared RerankerQueryResult.

    ``tokens_in`` / ``tokens_out`` / ``duration_ms`` are left as NULL --
    per-sub-query telemetry isn't currently captured (only per-executor
    aggregates via ``state.inner_usage``). See TODO in orchestrator.

    ``dropped_results`` is left as ``[]`` -- reranker_runs currently emit
    ``dropped_count`` only; wiring full drop reasoning is a future pass.
    """
    kept = [_kept_result_row(r) for r in (getattr(rqr, "results", None) or [])]
    return {
        "ura_id": ura_id,
        "agent_family": agent_family,
        "sub_query_index": int(sub_query_index),
        "sub_query_text": str(getattr(rqr, "query", "") or ""),
        "sub_query_rationale": str(getattr(rqr, "rationale", "") or ""),
        "kept_results": kept,
        "dropped_results": [],  # TODO: capture dropped reasoning (currently drop_count-only)
        "sufficient": bool(getattr(rqr, "sufficient", False)),
        "summary_note": str(getattr(rqr, "summary_note", "") or ""),
        # TODO: wire per-sub-query tokens + timing once executor state exposes them
        # (currently only per-executor aggregates exist in state.inner_usage).
        "tokens_in": None,
        "tokens_out": None,
        "duration_ms": None,
    }


async def save_reranker_runs(
    supabase: SupabaseClient,
    *,
    ura_id: str,
    reg_rqrs: list,
    comp_rqrs: list,
    case_rqrs: list,
    per_executor_stats: dict | None = None,  # noqa: ARG001 — reserved for future use
) -> int:
    """Bulk-insert rows into ``reranker_runs``.

    Global ``sub_query_index`` matches the URA merger's absorption order:
    reg first (0..N-1), then compliance (N..N+M-1), then case
    (N+M..end). See ``agents/deep_search_v3/ura/merger.py`` -- every URA
    ``sub_queries`` entry's ``index`` uses the same scheme, so joins
    between the forensic layer and URA ``sub_queries`` stay trivial.

    Returns the number of rows inserted. Returns 0 if all three input
    lists are empty, or if the insert fails.

    ``per_executor_stats`` is reserved for future use (when per-SQ token
    telemetry is added we'll divide total_tokens_* by len(rqrs) as a
    rough estimate). Ignored for v1.
    """
    if not ura_id:
        logger.warning("save_reranker_runs called with empty ura_id; skipping")
        return 0

    buckets: dict[str, Iterable[Any]] = {
        "reg_search": reg_rqrs or [],
        "compliance_search": comp_rqrs or [],
        "case_search": case_rqrs or [],
    }

    rows: list[dict] = []
    global_idx = 0
    for family in _EXECUTOR_ORDER:
        for rqr in buckets.get(family, []) or []:
            rows.append(
                _build_row(
                    ura_id=ura_id,
                    agent_family=family,
                    sub_query_index=global_idx,
                    rqr=rqr,
                )
            )
            global_idx += 1

    if not rows:
        return 0

    try:
        result = supabase.table("reranker_runs").insert(rows).execute()
    except Exception:
        logger.exception("save_reranker_runs: insert failed")
        return 0

    data = getattr(result, "data", None) or []
    # Supabase returns the inserted rows; fall back to len(rows) when the
    # client didn't echo the payload (older SDK versions).
    return len(data) if data else len(rows)
