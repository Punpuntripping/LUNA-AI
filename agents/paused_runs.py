"""paused_runs — pause/resume working state (migration 060).

Split out of ``agent_runs``: the suspension state is the only part of agent_runs
anything reads back (``find_open_pause`` reads the single open pause for a
conversation; nothing reads completed rows). It is mutable working state — born
when a run pauses (ask_user / approve_plan), read back to rehydrate the
pydantic-ai run, then deleted on resolve. An append-only cost trace is the wrong
home for it (see ``agents/utils/usage_sink.py``); it gets its own tiny table.

Delete-on-resolve: a row exists ONLY while a run is paused. Resume success /
abandon / timeout / expire all DELETE it, so the table self-cleans.

Fire-and-forget contract, like the old ``agents/runs.py``: every write is
best-effort and swallows exceptions — telemetry/state persistence must never
break a user-facing turn. The run_id is allocated app-side (not DB default) so
the caller has it before the insert returns (and so a future llm_calls.run_id
linkage can bind it during the run).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from supabase import Client as SupabaseClient

from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


@dataclass
class PauseRecord:
    conversation_id: str
    user_id: str
    case_id: str | None = None
    agent_family: str | None = None
    task_label: str | None = None
    # pydantic-ai result.all_messages_json() — raw bytes; column is BYTEA.
    message_history: bytes | None = None
    deferred_payload: dict | None = None
    question_text: str | None = None
    # 'clarify' (ask_user) | 'approve_plan' (present_plan_for_approval).
    pause_reason: str = "clarify"
    asked_at: datetime | None = None
    expires_at: datetime | None = None


def record_pause(
    supabase: SupabaseClient, rec: PauseRecord, *, run_id: str | None = None
) -> str | None:
    """INSERT a paused_runs row. Returns the run_id, or None on failure (never
    raises). ``run_id`` may be pre-allocated by the caller (so the run's
    pre-pause LLM calls can already be tagged with it via
    ``usage_sink.bind_run_id``); otherwise one is minted here."""
    run_id = str(run_id) if run_id else str(uuid.uuid4())
    with _logfire.span(
        "paused_runs.record",
        run_id=run_id,
        agent_family=rec.agent_family,
        pause_reason=rec.pause_reason,
        conversation_id=str(rec.conversation_id) if rec.conversation_id else None,
    ) as _span:
        try:
            payload: dict[str, Any] = {
                "run_id": run_id,
                "user_id": str(rec.user_id),
                "conversation_id": str(rec.conversation_id),
                "pause_reason": rec.pause_reason or "clarify",
            }
            if rec.case_id is not None:
                payload["case_id"] = str(rec.case_id)
            if rec.agent_family is not None:
                payload["agent_family"] = rec.agent_family
            if rec.task_label is not None:
                payload["task_label"] = rec.task_label
            if rec.message_history is not None:
                # Postgres-native '\x'-prefixed hex — round-trips with PostgREST,
                # which also returns BYTEA as '\x'-hex on read.
                payload["message_history"] = "\\x" + rec.message_history.hex()
            if rec.deferred_payload is not None:
                payload["deferred_payload"] = rec.deferred_payload
            if rec.question_text is not None:
                payload["question_text"] = rec.question_text
            if rec.asked_at is not None:
                payload["asked_at"] = rec.asked_at.isoformat()
            if rec.expires_at is not None:
                payload["expires_at"] = rec.expires_at.isoformat()

            supabase.table("paused_runs").insert(payload).execute()
            try:
                _span.set_attribute("write_ok", True)
            except Exception:
                pass
            return run_id
        except Exception as e:
            try:
                _span.set_attribute("write_ok", False)
                _span.set_attribute("error", str(e))
            except Exception:
                pass
            logger.warning("paused_runs insert failed (non-blocking): %s", e)
            return None


def find_open_pause(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict | None:
    """Return the most-recent open pause for this conversation, or None.

    Because rows are deleted on resolve, any row found IS an open pause — no
    status filter needed. Expiry is enforced by the caller via ``is_expired``."""
    try:
        result = (
            supabase.table("paused_runs")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .order("asked_at", desc=True)
            .limit(1)
            .execute()
        )
        data = getattr(result, "data", None) or []
        return data[0] if data else None
    except Exception as e:
        logger.warning("find_open_pause failed: %s", e)
        return None


def resolve_pause(supabase: SupabaseClient, run_id: str) -> bool:
    """DELETE the pause row — the run resumed, was abandoned, timed out, or
    expired. Idempotent (deleting an absent row is a no-op). Never raises."""
    if not run_id:
        return False
    with _logfire.span("paused_runs.resolve", run_id=run_id) as _span:
        try:
            supabase.table("paused_runs").delete().eq("run_id", run_id).execute()
            try:
                _span.set_attribute("write_ok", True)
            except Exception:
                pass
            return True
        except Exception as e:
            try:
                _span.set_attribute("write_ok", False)
                _span.set_attribute("error", str(e))
            except Exception:
                pass
            logger.warning("resolve_pause failed (non-blocking): run_id=%s %s", run_id, e)
            return False


def is_expired(row: dict) -> bool:
    """True if the pause's ``expires_at`` is in the past. Missing/garbage
    ``expires_at`` → treated as NOT expired (fail-open: don't drop a live pause
    on a parse error)."""
    raw = row.get("expires_at")
    if not raw:
        return False
    try:
        if isinstance(raw, str):
            exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        elif isinstance(raw, datetime):
            exp = raw
        else:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < datetime.now(timezone.utc)
    except Exception:
        return False


__all__ = [
    "PauseRecord",
    "record_pause",
    "find_open_pause",
    "resolve_pause",
    "is_expired",
]
