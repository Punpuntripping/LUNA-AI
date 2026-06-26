"""Per-call token/cost ledger sink — writes ``llm_calls`` rows (migration 058).

Why this exists
---------------
Cost used to live on ``agent_runs``, captured *voluntarily*: every specialist
had to thread ``per_phase_stats`` / ``tokens_in/out`` up through its
``SpecialistResult`` for ``record_agent_run`` to derive a cost. The writer never
honoured that contract (``tokens_in=0`` hardcoded), so every writing-family run
billed $0. Cost capture that depends on each agent opting in is unreliable by
construction.

This module captures at the *one place every model call already passes through*:
``agents/utils/tracking.py`` (``run_tracked`` / ``AgentSpan.record_run``). Each
call appends one row to a per-turn buffer held in a :class:`contextvars.ContextVar`;
the buffer is flushed once at the turn boundary (orchestrator ``handle_message``,
the resume path, and the artifact-summarizer webhook). deep_search — which
aggregates usage manually rather than via ``run_tracked`` — feeds the same buffer
from its ``per_phase_stats`` per-model breakdown.

Quota settle (ORD cost + OCR pages) fires here, on flush, as the single
settle point. ``agents/runs.py`` no longer settles.

Contract: best-effort, never raises. Telemetry must never break a user turn.
A ``record_call`` outside any ``collect_llm_calls`` scope is silently dropped.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# The active per-turn buffer (a mutable list, shared by reference across any
# child tasks spawned after the scope opens) and the identity all rows in the
# scope inherit. ``None`` buffer ⇒ not inside a scope ⇒ record_call drops.
_buffer: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_calls_buffer", default=None)
_ident: ContextVar[dict[str, Any]] = ContextVar("llm_calls_ident", default={})
# Finer than the message-level scope: the logical agent run. Bound per dispatch
# (and reused on resume) so a run's calls — incl. across a pause boundary — share
# a run_id. None when not inside a run (e.g. background OCR/summarize).
_run_id: ContextVar[str | None] = ContextVar("llm_calls_run_id", default=None)


@contextmanager
def collect_llm_calls(
    supabase: Any,
    *,
    conversation_id: Any,
    user_id: Any,
    message_id: Any = None,
    case_id: Any = None,
) -> Iterator[None]:
    """Open a capture scope. Every :func:`record_call` inside it (including from
    nested agents via the tracking hook) accrues to one buffer that is inserted
    into ``llm_calls`` and quota-settled on exit.

    Auto-flushes on ``__exit__`` — including the GeneratorExit / CancelledError
    paths (SSE disconnect, gateway timeout), so partial turns still bill what
    actually ran. Never raises.
    """
    tok_b = _buffer.set([])
    tok_i = _ident.set(
        {
            "conversation_id": str(conversation_id) if conversation_id else None,
            "user_id": str(user_id) if user_id else None,
            "message_id": str(message_id) if message_id else None,
            "case_id": str(case_id) if case_id else None,
        }
    )
    try:
        yield
    finally:
        buf = _buffer.get()
        try:
            _buffer.reset(tok_b)
            _ident.reset(tok_i)
        except Exception:  # pragma: no cover — reset across tasks
            pass
        _flush(supabase, buf)


@contextmanager
def bind_run_id(run_id: str | None) -> Iterator[None]:
    """Tag every :func:`record_call` in this block with ``run_id`` — the logical
    agent run. Open it per dispatch with a freshly-allocated id; on resume, pass
    the paused run's id so the resume leg's calls share the original run's id.
    No-op (leaves run_id NULL) when ``run_id`` is falsy."""
    if not run_id:
        yield
        return
    tok = _run_id.set(str(run_id))
    try:
        yield
    finally:
        try:
            _run_id.reset(tok)
        except Exception:
            pass


def in_scope() -> bool:
    """True when a :func:`collect_llm_calls` scope is currently open.

    Lets a function that may be called both inline (already inside the turn
    scope) and standalone (a webhook / background job) open its OWN scope only
    when needed, instead of nesting a redundant one."""
    return _buffer.get() is not None


def record_call(
    *,
    agent: str,
    model: str | None = None,
    agent_family: str | None = None,
    subtype: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_reasoning: int = 0,
    tokens_cached: int = 0,
    pages_used: int | None = None,
    cost_usd: float | None = None,
    requests: int = 1,
    duration_ms: int | None = None,
    outcome: str = "ok",
) -> None:
    """Append one ledger row to the active scope's buffer.

    No-op when called outside a :func:`collect_llm_calls` scope (e.g. a CLI run
    or an un-wrapped background job) — telemetry is best-effort. ``cost_usd`` is
    computed from ``model`` + token counts when the caller leaves it ``None``.
    """
    buf = _buffer.get()
    if buf is None:
        return
    ident = _ident.get() or {}
    if cost_usd is None:
        cost_usd = _compute_cost(model, tokens_in, tokens_out, tokens_reasoning, tokens_cached)
    row: dict[str, Any] = {
        "conversation_id": ident.get("conversation_id"),
        "user_id": ident.get("user_id"),
        "message_id": ident.get("message_id"),
        "case_id": ident.get("case_id"),
        "run_id": _run_id.get(),
        "agent": agent,
        "agent_family": agent_family,
        "subtype": subtype,
        "model": model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "tokens_reasoning": int(tokens_reasoning or 0),
        "tokens_cached": int(tokens_cached or 0),
        "cost_usd": round(float(cost_usd or 0.0), 6),
        "requests": int(requests or 1),
        "outcome": outcome or "ok",
    }
    if pages_used is not None:
        row["pages_used"] = int(pages_used)
    if duration_ms is not None:
        row["duration_ms"] = int(duration_ms)
    buf.append(row)


def _compute_cost(
    model: str | None,
    tokens_in: int,
    tokens_out: int,
    reasoning: int,
    cached: int,
) -> float:
    try:
        from agents.utils.agent_models import cost_usd as _cost_usd

        return round(_cost_usd(model, int(tokens_in or 0), int(tokens_out or 0), int(reasoning or 0), int(cached or 0)), 6)
    except Exception:
        return 0.0


def _flush(supabase: Any, buf: list[dict[str, Any]] | None) -> int:
    """Insert buffered llm_calls rows. The ledger is the single source of truth
    for usage — the quota gate reads it directly — so the insert IS the settle.

    Never raises. Insert gets one immediate retry. If both attempts fail, the
    full row payload is logged at ERROR so cost can be backfilled into
    ``llm_calls`` manually; until then that turn simply isn't counted against the
    user's quota. Exposure: one free turn per insert outage — bounded and loud.
    """
    if not buf:
        return 0

    insert_ok = False
    for attempt in (1, 2):
        try:
            supabase.table("llm_calls").insert(buf).execute()
            insert_ok = True
            break
        except Exception as exc:
            if attempt == 1:
                logger.warning(
                    "llm_calls insert failed (attempt 1/2, %d rows), retrying: %s",
                    len(buf), exc,
                )
            else:
                try:
                    payload = json.dumps(buf, ensure_ascii=False, default=str)
                except Exception:
                    payload = repr(buf)
                logger.error(
                    "llm_calls insert FAILED after retry — quota settle SKIPPED "
                    "(backfill these rows manually): error=%s rows=%s", exc, payload,
                )

    if not insert_ok:
        return 0  # ledger insert failed — the row simply isn't counted (see below)

    # No quota settle step: the quota gate reads usage DIRECTLY from this ledger
    # (shared.quota.get_user_usage_windows). The ledger insert above IS the
    # settle — once the row lands, the next gate read counts it automatically.
    return len(buf)


__all__ = ["collect_llm_calls", "record_call", "in_scope", "bind_run_id"]
