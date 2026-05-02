"""agent_runs writes -- append-only audit log for agent invocations.

Fire-and-forget writer: telemetry must never break a user-facing run.
All exceptions are swallowed and logged via stdlib logging.

Wave 9 Task 13.4/13.5 additions:
- AgentRunRecord has 5 new optional pause-state fields:
    message_history, deferred_payload, question_text, asked_at, expires_at
- record_agent_run serialises BYTEA columns (message_history → base64) and
  datetime columns (asked_at / expires_at → ISO-8601 strings).
- update_run_status() patches an existing run row (e.g. status flip on resume).
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime
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
    # Pause/resume state (Wave 9 Task 13).
    # message_history: raw bytes from pydantic_ai result.all_messages_json(); column is BYTEA.
    # deferred_payload: dict describing the pending DeferredToolCall (tool_call_id, args, etc.).
    # question_text: rendered question surfaced to the user.
    # asked_at / expires_at: pause window timestamps.
    message_history: bytes | None = None
    deferred_payload: dict | None = None
    question_text: str | None = None
    asked_at: datetime | None = None
    expires_at: datetime | None = None


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
        # Pause-state columns: present only when the run is paused.
        if rec.message_history is not None:
            # Supabase PostgREST represents BYTEA as base64 on the wire.
            payload["message_history"] = base64.b64encode(rec.message_history).decode()
        if rec.deferred_payload is not None:
            payload["deferred_payload"] = rec.deferred_payload
        if rec.question_text is not None:
            payload["question_text"] = rec.question_text
        if rec.asked_at is not None:
            payload["asked_at"] = rec.asked_at.isoformat()
        if rec.expires_at is not None:
            payload["expires_at"] = rec.expires_at.isoformat()

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


def update_run_status(
    supabase: SupabaseClient,
    run_id: str,
    status: str,
    **fields: Any,
) -> bool:
    """Patch an existing agent_runs row (e.g. flip status from awaiting_user → ok).

    Additional keyword arguments are included in the UPDATE payload verbatim.
    Special handling mirrors record_agent_run:
    - ``message_history`` (bytes) → base64-encoded string for BYTEA column.
    - ``asked_at`` / ``expires_at`` (datetime) → ISO-8601 string.

    Returns True on success, False on failure (never raises).
    """
    try:
        payload: dict[str, Any] = {"status": status}
        for key, val in fields.items():
            if val is None:
                continue
            if key == "message_history" and isinstance(val, bytes):
                payload[key] = base64.b64encode(val).decode()
            elif key in ("asked_at", "expires_at") and isinstance(val, datetime):
                payload[key] = val.isoformat()
            else:
                payload[key] = val

        supabase.table("agent_runs").update(payload).eq("run_id", run_id).execute()
        return True
    except Exception as e:
        logger.warning("update_run_status failed (non-blocking): run_id=%s status=%s %s", run_id, status, e)
        return False
