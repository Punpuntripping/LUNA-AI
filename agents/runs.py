"""agent_runs writes -- append-only audit log for agent invocations.

Fire-and-forget writer: telemetry must never break a user-facing run.
All exceptions are swallowed and logged via stdlib logging.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


@dataclass
class AgentRunRecord:
    user_id: str
    conversation_id: str
    agent_family: str
    case_id: str | None = None
    message_id: str | None = None
    subtype: str | None = None
    input_summary: str | None = None
    output_item_id: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model_used: str | None = None
    per_phase_stats: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: dict | None = None
    # Populated from the active Logfire span at write time, e.g.:
    #   span = logfire.current_span(); rec.trace_id = format(span.context.trace_id, '032x')
    trace_id: str | None = None
    span_id: str | None = None


def record_agent_run(supabase: SupabaseClient, rec: AgentRunRecord) -> str | None:
    """Insert a row into agent_runs. Returns run_id, or None on failure (never raises)."""
    try:
        payload: dict[str, Any] = {
            "user_id": str(rec.user_id),
            "conversation_id": str(rec.conversation_id),
            "agent_family": rec.agent_family,
            "status": rec.status,
            "per_phase_stats": rec.per_phase_stats or {},
        }
        if rec.case_id is not None:
            payload["case_id"] = str(rec.case_id)
        if rec.message_id is not None:
            payload["message_id"] = str(rec.message_id)
        if rec.subtype is not None:
            payload["subtype"] = rec.subtype
        if rec.input_summary is not None:
            payload["input_summary"] = rec.input_summary
        if rec.output_item_id is not None:
            payload["output_item_id"] = str(rec.output_item_id)
        if rec.duration_ms is not None:
            payload["duration_ms"] = rec.duration_ms
        if rec.tokens_in is not None:
            payload["tokens_in"] = rec.tokens_in
        if rec.tokens_out is not None:
            payload["tokens_out"] = rec.tokens_out
        if rec.model_used is not None:
            payload["model_used"] = rec.model_used
        if rec.error is not None:
            payload["error"] = rec.error
        if rec.trace_id is not None:
            payload["trace_id"] = rec.trace_id
        if rec.span_id is not None:
            payload["span_id"] = rec.span_id

        result = (
            supabase.table("agent_runs")
            .insert(payload)
            .execute()
        )
        data = getattr(result, "data", None)
        if data and len(data) > 0:
            run_id = data[0].get("run_id")
            return str(run_id) if run_id is not None else None
        return None
    except Exception as e:
        logger.warning("agent_runs write failed (non-blocking): %s", e)
        return None
