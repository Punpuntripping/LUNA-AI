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

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from supabase import Client as SupabaseClient

from agents.utils.agent_models import estimate_run_cost

logger = logging.getLogger(__name__)


@dataclass
class AgentRunRecord:
    user_id: str
    conversation_id: str
    agent_family: str
    case_id: str | None = None
    message_id: str | None = None
    subtype: str | None = None
    # Short Arabic content-derived label (≤80 chars) emitted by the router on
    # DispatchAgent. Used by the planner to enumerate prior tasks without
    # reading full describe_query text. Persisted to agent_runs.task_label
    # (migration 039).
    task_label: str | None = None
    input_summary: str | None = None
    output_item_id: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    # Reasoning ("thinking") tokens. Tracked separately because pydantic_ai's
    # output_tokens does NOT include them, yet providers bill them at the
    # output rate. Left None to let record_agent_run derive it from
    # per_phase_stats; set explicitly to override.
    tokens_reasoning: int | None = None
    # Estimated USD cost. Left None to let record_agent_run compute it from
    # per_phase_stats (tier-accurate) or aggregate tokens (flat tier_1).
    cost_usd: float | None = None
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
        if rec.task_label is not None:
            payload["task_label"] = rec.task_label
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
        # Derive cost + reasoning tokens unless caller supplied them. Prefers
        # the per-tier breakdown in per_phase_stats; falls back to flat tier_1.
        est_cost, est_reasoning = estimate_run_cost(
            rec.per_phase_stats, rec.tokens_in, rec.tokens_out, rec.tokens_reasoning
        )
        tokens_reasoning = (
            rec.tokens_reasoning if rec.tokens_reasoning is not None else est_reasoning
        )
        if tokens_reasoning:
            payload["tokens_reasoning"] = tokens_reasoning
        cost_usd = rec.cost_usd if rec.cost_usd is not None else est_cost
        if cost_usd:
            payload["cost_usd"] = cost_usd
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
            # Send BYTEA as Postgres-native '\x'-prefixed hex so the read path
            # (PostgREST also returns BYTEA as '\x'-hex) round-trips without a
            # double-encoding layer. Base64 round-trip would require b64decode
            # AFTER hex-decode on read.
            payload["message_history"] = "\\x" + rec.message_history.hex()
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
    - ``message_history`` (bytes) → '\\x'-prefixed hex string for BYTEA column.
    - ``asked_at`` / ``expires_at`` (datetime) → ISO-8601 string.

    Returns True on success, False on failure (never raises).
    """
    try:
        payload: dict[str, Any] = {"status": status}
        for key, val in fields.items():
            if val is None:
                continue
            if key == "message_history" and isinstance(val, bytes):
                payload[key] = "\\x" + val.hex()
            elif key in ("asked_at", "expires_at") and isinstance(val, datetime):
                payload[key] = val.isoformat()
            else:
                payload[key] = val

        # Derive cost when token/phase data is patched in (e.g. on deferred-run
        # resume) and the caller did not supply cost_usd explicitly.
        if "cost_usd" not in payload and (
            "per_phase_stats" in payload
            or "tokens_in" in payload
            or "tokens_out" in payload
        ):
            est_cost, est_reasoning = estimate_run_cost(
                payload.get("per_phase_stats"),
                payload.get("tokens_in"),
                payload.get("tokens_out"),
                payload.get("tokens_reasoning"),
            )
            if est_cost:
                payload["cost_usd"] = est_cost
            if est_reasoning and "tokens_reasoning" not in payload:
                payload["tokens_reasoning"] = est_reasoning

        supabase.table("agent_runs").update(payload).eq("run_id", run_id).execute()
        return True
    except Exception as e:
        logger.warning("update_run_status failed (non-blocking): run_id=%s status=%s %s", run_id, status, e)
        return False
