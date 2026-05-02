"""Agent orchestrator — routes messages, dispatches specialists, records runs.

Wave 9 rewrite (Task 7):
- OpenTask / TaskContinue / TaskEnd → gone (no task_state table dependency).
- agents.state imports → gone.
- Full specialist body NEVER streamed to chat; only chat_summary + key_findings.
- Pre-router memory hook: resummarize_dirty_items + compact_conversation.
- Dispatch records an agent_runs row in the finally block (fire-and-forget).
- Cap pre-flight: refuse deep_search / writing dispatch when workspace_items >= 15.
- Logfire span wraps _dispatch; trace_id / span_id populated on AgentRunRecord.

Wave 9 Task 13.4/13.5 additions:
- Pre-route pause check: _find_awaiting_user / _expired / _resume_major_agent.
- _dispatch handles DeferredToolRequests output from the planner: records an
  'awaiting_user' agent_runs row, inserts an agent_question message, yields
  agent_question + done events (stream closes; run stays alive).
- _run_deep_search accepts an optional plan_override so the resume path can
  skip the planner and jump straight to the search/aggregate/publish phases.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import AsyncGenerator

from supabase import Client as SupabaseClient

import agents.memory.agent as memory
from agents.models import (
    ChatResponse,
    DispatchAgent,
    MajorAgentInput,
    ChatMessageSnapshot,
    WorkspaceItemSnapshot,
    SpecialistResult,
)
from agents.runs import AgentRunRecord, record_agent_run, update_run_status
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# Workspace item kinds counted toward the per-conversation cap.
_CAP_KINDS = ("agent_search", "agent_writing", "note")
_WORKSPACE_CAP = 15

# Number of recent messages to load for MajorAgentInput.
_RECENT_MESSAGES_N = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zero_usage(model: str) -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "model": model}


def _count_artifact_kinds(supabase: SupabaseClient, conversation_id: str) -> int:
    """Count non-deleted workspace_items in the capped kinds for this conversation."""
    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id", count="exact")
            .eq("conversation_id", conversation_id)
            .in_("kind", list(_CAP_KINDS))
            .is_("deleted_at", "null")
            .execute()
        )
        return getattr(result, "count", None) or len(result.data or [])
    except Exception as e:
        logger.warning("_count_artifact_kinds failed: %s", e)
        return 0


def _load_attached_items(
    supabase: SupabaseClient,
    item_ids: list[str],
) -> list[WorkspaceItemSnapshot]:
    """Hydrate workspace item UUIDs into full WorkspaceItemSnapshot objects."""
    if not item_ids:
        return []
    snapshots: list[WorkspaceItemSnapshot] = []
    for item_id in item_ids:
        try:
            row = (
                supabase.table("workspace_items")
                .select("item_id, kind, title, content_md, metadata")
                .eq("item_id", item_id)
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )
            if row and getattr(row, "data", None):
                d = row.data
                snapshots.append(
                    WorkspaceItemSnapshot(
                        item_id=d.get("item_id", item_id),
                        kind=d.get("kind") or "unknown",
                        title=d.get("title") or "",
                        content_md=d.get("content_md") or "",
                        metadata=d.get("metadata") or {},
                    )
                )
        except Exception as e:
            logger.warning("_load_attached_items: could not load %s: %s", item_id, e)
    return snapshots


def _load_recent_messages(
    supabase: SupabaseClient,
    conversation_id: str,
    n: int = _RECENT_MESSAGES_N,
) -> list[ChatMessageSnapshot]:
    """Load the last N messages for the conversation as ChatMessageSnapshot objects."""
    try:
        result = (
            supabase.table("messages")
            .select("role, content, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(n)
            .execute()
        )
        rows = list(reversed(result.data or []))
        snapshots = []
        for row in rows:
            role = row.get("role") or "user"
            if role not in ("user", "assistant"):
                role = "user"
            snapshots.append(
                ChatMessageSnapshot(
                    role=role,
                    content=row.get("content") or "",
                    created_at=row.get("created_at") or "",
                )
            )
        return snapshots
    except Exception as e:
        logger.warning("_load_recent_messages failed: %s", e)
        return []


def _extract_logfire_ids() -> tuple[str | None, str | None]:
    """Best-effort extraction of trace_id and span_id from the active Logfire span."""
    try:
        span = _logfire.current_span()  # type: ignore[attr-defined]
        ctx = getattr(span, "context", None)
        if ctx is None:
            return None, None
        trace_id = format(ctx.trace_id, "032x") if ctx.trace_id else None
        span_id = format(ctx.span_id, "016x") if ctx.span_id else None
        return trace_id, span_id
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Pause / resume helpers (Task 13.4 / 13.5)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _find_awaiting_user(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> dict | None:
    """Return the most-recent awaiting_user agent_run for this conversation, or None."""
    try:
        result = (
            supabase.table("agent_runs")
            .select("*")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .eq("status", "awaiting_user")
            .order("asked_at", desc=True)
            .limit(1)
            .execute()
        )
        data = getattr(result, "data", None) or []
        return data[0] if data else None
    except Exception as e:
        logger.warning("_find_awaiting_user failed: %s", e)
        return None


def _expired(pending: dict) -> bool:
    """Return True when the pause window has passed."""
    expires_raw = pending.get("expires_at")
    if not expires_raw:
        return False
    try:
        if isinstance(expires_raw, str):
            # ISO-8601; ensure tz-aware
            expires = datetime.fromisoformat(expires_raw)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        elif isinstance(expires_raw, datetime):
            expires = expires_raw
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        else:
            return False
        return expires < _now_utc()
    except Exception as e:
        logger.warning("_expired: could not parse expires_at=%r: %s", expires_raw, e)
        return False


async def _resume_major_agent(
    pending: dict,
    user_reply: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
) -> AsyncGenerator[dict, None]:
    """Resume a paused agent run after the user has replied to an ask_user question.

    Only ``deep_search`` supports pause/resume in this release.  Any other
    agent_family is abandoned and re-routed fresh via the normal router path.
    """
    import base64

    from pydantic_ai import DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    run_id: str = str(pending.get("run_id", ""))
    agent_family: str = str(pending.get("agent_family", ""))

    # ── Tag the user-reply message with the run_id so history readers can
    #    correlate it. The user message was already inserted by message_service
    #    BEFORE handle_message is called; we PATCH its metadata here rather
    #    than double-inserting.
    if user_message_id:
        try:
            supabase.table("messages").update(
                {"metadata": {"kind": "agent_answer", "run_id": run_id}}
            ).eq("message_id", user_message_id).execute()
        except Exception as e:
            logger.warning("_resume_major_agent: could not patch user message metadata: %s", e)

    yield {"type": "agent_resumed", "run_id": run_id, "agent_family": agent_family}

    # ── Only deep_search supports resume; abandon everything else. ──────────
    if agent_family != "deep_search":
        logger.info(
            "_resume_major_agent: agent_family=%s does not support resume; abandoning run_id=%s",
            agent_family, run_id,
        )
        update_run_status(supabase, run_id, "abandoned")
        # Re-route the user reply through the normal router.
        async for ev in _route(
            question=user_reply,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    # ── Rehydrate message history + build DeferredToolResults ───────────────
    try:
        raw_history = pending.get("message_history")
        if isinstance(raw_history, str):
            # PostgREST returns BYTEA as base64 string
            history_bytes = base64.b64decode(raw_history)
        elif isinstance(raw_history, bytes):
            history_bytes = raw_history
        else:
            raise ValueError(f"unexpected message_history type: {type(raw_history)}")

        history = ModelMessagesTypeAdapter.validate_json(history_bytes)

        deferred_payload = pending.get("deferred_payload") or {}
        tool_call_id = deferred_payload.get("tool_call_id")
        if not tool_call_id:
            raise ValueError("deferred_payload missing tool_call_id")

        results = DeferredToolResults(calls={tool_call_id: user_reply})
    except Exception as exc:
        logger.error(
            "_resume_major_agent: failed to rehydrate state for run_id=%s: %s",
            run_id, exc, exc_info=True,
        )
        update_run_status(supabase, run_id, "error")
        async for ev in _route(
            question=user_reply,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    # ── Resume the planner with the user's answer ────────────────────────────
    try:
        from agents.deep_search_v4.planner import PlannerDeps, PlannerOutput
        from agents.deep_search_v4.planner.agent import create_planner_agent, PLANNER_LIMITS
        from pydantic_ai import DeferredToolRequests

        planner_agent = create_planner_agent()
        planner_result = await planner_agent.run(
            "",  # user_prompt unused on resume — history provides full context
            message_history=history,
            deferred_tool_results=results,
            usage_limits=PLANNER_LIMITS,
        )
        planner_output = planner_result.output
    except Exception as exc:
        logger.error(
            "_resume_major_agent: planner resume failed for run_id=%s: %s",
            run_id, exc, exc_info=True,
        )
        update_run_status(supabase, run_id, "error")
        async for ev in _route(
            question=user_reply,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    # ── Another pause? (chained ask_user) ───────────────────────────────────
    if isinstance(planner_output, DeferredToolRequests):
        new_question, new_run_id = _record_deferred(
            supabase=supabase,
            planner_result=planner_result,
            planner_output=planner_output,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
            agent_family="deep_search",
            briefing=user_reply,
        )
        # Close out the previous run row (it's superseded by the new one).
        update_run_status(supabase, run_id, "abandoned")
        yield {"type": "agent_question", "run_id": new_run_id or "", "question": new_question}
        yield {"type": "done", "usage": _zero_usage("paused")}
        return

    # ── PlannerOutput.aborted — give up on deep_search, re-route fresh ──────
    if isinstance(planner_output, PlannerOutput) and planner_output.aborted:
        logger.info(
            "_resume_major_agent: planner aborted run_id=%s; re-routing via router",
            run_id,
        )
        update_run_status(supabase, run_id, "abandoned")
        async for ev in _route(
            question=user_reply,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    # ── Normal PlannerOutput — continue the deep_search pipeline ────────────
    if not isinstance(planner_output, PlannerOutput):
        logger.error(
            "_resume_major_agent: unexpected planner output type=%s for run_id=%s",
            type(planner_output), run_id,
        )
        update_run_status(supabase, run_id, "error")
        yield {"type": "token", "text": "حدث خطأ أثناء استئناف البحث. يرجى المحاولة مرة أخرى."}
        yield {"type": "done", "usage": _zero_usage("error")}
        return

    t0 = perf_counter()
    run_result: SpecialistResult | None = None
    status = "ok"
    err_payload: dict | None = None

    try:
        attached_items = _load_attached_items(supabase, [])
        recent_messages = _load_recent_messages(supabase, conversation_id)
        major_input = MajorAgentInput(
            briefing=user_reply,
            attached_items=attached_items,
            recent_messages=recent_messages,
            target_item_id=None,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
        )

        run_result = await _run_deep_search(major_input, supabase, plan_override=planner_output)

        for ev in run_result.sse_events:
            yield ev

        if run_result.chat_summary:
            yield {"type": "token", "text": run_result.chat_summary}

        if run_result.key_findings:
            bullets = "\n\n" + "\n".join(f"• {k}" for k in run_result.key_findings)
            yield {"type": "token", "text": bullets}

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": run_result.tokens_in or 0,
                "completion_tokens": run_result.tokens_out or 0,
                "model": run_result.model_used or "deep_search_v4",
            },
        }

    except Exception as exc:
        logger.error(
            "_resume_major_agent: deep_search run failed for run_id=%s: %s",
            run_id, exc, exc_info=True,
        )
        status = "error"
        err_payload = {"type": type(exc).__name__, "message": str(exc)[:500]}
        yield {
            "type": "token",
            "text": "عذراً، حدث خطأ أثناء استئناف البحث. يرجى المحاولة مرة أخرى.",
        }
        yield {"type": "done", "usage": _zero_usage("error")}

    finally:
        duration_ms = int((perf_counter() - t0) * 1000)
        update_run_status(
            supabase,
            run_id,
            status,
            duration_ms=duration_ms,
            tokens_in=getattr(run_result, "tokens_in", None),
            tokens_out=getattr(run_result, "tokens_out", None),
            model_used=getattr(run_result, "model_used", None),
            output_item_id=getattr(run_result, "output_item_id", None),
            per_phase_stats=getattr(run_result, "per_phase_stats", {}) or {},
            error=err_payload,
        )

    yield {"type": "agent_run_finished", "agent_family": "deep_search"}


def _record_deferred(
    *,
    supabase: SupabaseClient,
    planner_result: Any,
    planner_output: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    agent_family: str,
    briefing: str,
) -> tuple[str, str | None]:
    """Persist pause state for a DeferredToolRequests planner output.

    Inserts the agent_runs row and the agent_question message row.
    Returns (question_text, run_id).  Both are best-effort; run_id may be None
    on DB failure.
    """
    from typing import Any as _Any

    pending_call = planner_output.calls[0]
    # ToolCallPart.args is `str | dict | None` (JSON string when from streaming
    # providers, dict when synthesized locally); args_as_dict() normalizes both.
    pending_args = pending_call.args_as_dict()
    question = pending_args.get("question", "")
    now = _now_utc()

    run_id = record_agent_run(
        supabase,
        AgentRunRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            agent_family=agent_family,
            message_id=user_message_id,
            input_summary=briefing[:500],
            status="awaiting_user",
            message_history=planner_result.all_messages_json(),
            deferred_payload={
                "tool_call_id": pending_call.tool_call_id,
                "tool_name": pending_call.tool_name,
                "args": pending_args,
                "partial_output": None,
            },
            question_text=question,
            asked_at=now,
            expires_at=now + timedelta(hours=24),
        ),
    )

    # Insert the question as an assistant message so it appears in chat history.
    try:
        supabase.table("messages").insert({
            "message_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": question,
            "metadata": {
                "kind": "agent_question",
                "run_id": run_id,
                "agent_family": agent_family,
            },
        }).execute()
    except Exception as e:
        logger.warning("_record_deferred: failed to insert agent_question message: %s", e)

    return question, run_id


# Needed for _record_deferred type annotation
from typing import Any  # noqa: E402 — after class definitions


# ---------------------------------------------------------------------------
# Planner pre-flight for _dispatch (Task 13.4)
# ---------------------------------------------------------------------------

# Sentinel: returned by _try_run_planner_for_dispatch when the planner issued a
# DeferredToolRequests — the deferred events have already been yielded by the
# caller-side generator and _dispatch must stop without recording a completed row.
_DEFERRED_EMITTED = object()


class _SkipRunRecord(Exception):
    """Raised inside _dispatch's try block to skip the agent_runs INSERT.

    Used when the run transitions to 'awaiting_user' and is persisted by
    _record_deferred instead of by the normal finally block.
    """


async def _try_run_planner_for_dispatch(
    briefing: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    agent_family: str,
) -> "PlannerOutput | None | object":
    """Run the planner once before the deep_search pipeline.

    Returns:
    - ``PlannerOutput``   — planner succeeded; pass as plan_override to _run_deep_search.
    - ``None``            — planner disabled or degraded fallback; _run_deep_search handles it.
    - ``_DEFERRED_EMITTED`` — planner paused; the caller (a generator) has already
      yielded the agent_question + done events and must return immediately.

    Note: this function is NOT a generator; it cannot yield.  The generator
    contract is handled one level up in _dispatch by checking the return value.
    """
    # Import lazily so the module stays importable without pydantic_ai.
    try:
        from agents.deep_search_v4.planner import PlannerDeps
        from agents.deep_search_v4.planner.agent import create_planner_agent, PLANNER_LIMITS
        from agents.deep_search_v4.planner.prompts import build_planner_user_message
        from pydantic_ai import DeferredToolRequests
    except ImportError:
        return None

    try:
        planner_deps = PlannerDeps(model_override=None, emit_sse=None)
        user_message = build_planner_user_message(briefing)
        planner_agent = create_planner_agent()
        planner_result = await planner_agent.run(user_message, usage_limits=PLANNER_LIMITS)
        planner_output = planner_result.output
    except Exception as exc:
        logger.warning(
            "_try_run_planner_for_dispatch: planner failed, skipping plan: %s", exc, exc_info=True,
        )
        return None

    if isinstance(planner_output, DeferredToolRequests):
        # Persist the pause state and the question message.
        _record_deferred(
            supabase=supabase,
            planner_result=planner_result,
            planner_output=planner_output,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
            agent_family=agent_family,
            briefing=briefing,
        )
        return _DEFERRED_EMITTED

    # Normal PlannerOutput (including aborted=True — let _run_deep_search handle it).
    from agents.deep_search_v4.planner import PlannerOutput
    if isinstance(planner_output, PlannerOutput):
        return planner_output

    # Unknown type — degrade gracefully.
    logger.warning(
        "_try_run_planner_for_dispatch: unexpected planner output type=%s", type(planner_output),
    )
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def handle_message(
    question: str,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    explicit_agent_family: str | None = None,
    user_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Main entry point for all chat turns.

    1. Pre-router memory hook (best-effort).
    2. If explicit_agent_family: skip router, call _dispatch directly.
    3. Else: call _route which runs the router LLM and fans out.

    ``user_message_id`` is the FK of the persisted user message (inserted
    before the AI call, per CLAUDE.md rule #7). Thread-through to
    agent_runs.message_id so the audit row links to the triggering message.
    """
    # 0. Pre-route pause check — resume a pending major agent if one exists.
    pending = _find_awaiting_user(supabase, conversation_id, user_id)
    if pending:
        if _expired(pending):
            logger.info(
                "handle_message: run_id=%s expired; marking timeout and proceeding normally",
                pending.get("run_id"),
            )
            update_run_status(supabase, str(pending.get("run_id", "")), "timeout")
            # fall through to normal flow
        else:
            async for ev in _resume_major_agent(
                pending=pending,
                user_reply=question,
                supabase=supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                user_message_id=user_message_id,
            ):
                yield ev
            return

    # 1. Pre-router memory hook — best-effort, never aborts the turn.
    try:
        await memory.resummarize_dirty_items(supabase, conversation_id)
        await memory.compact_conversation(supabase, conversation_id, user_id)
    except Exception:
        logger.warning("memory pre-hook failed", exc_info=True)

    if explicit_agent_family:
        async for ev in _dispatch(
            agent_family=explicit_agent_family,
            briefing=question,
            target_item_id=None,
            attached_item_ids=[],
            subtype=None,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    async for ev in _route(
        question=question,
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        user_message_id=user_message_id,
    ):
        yield ev


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def _route(
    question: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the router LLM; on ChatResponse stream tokens; on DispatchAgent call _dispatch."""
    from agents.router.context import load_router_context
    from agents.router.router import run_router

    ctx = load_router_context(supabase, user_id, conversation_id, case_id)

    result = await run_router(
        question=question,
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=ctx.case_memory_md,
        case_metadata=ctx.case_metadata,
        user_preferences=ctx.user_preferences,
        message_history=ctx.message_history,
        workspace_item_summaries=ctx.workspace_item_summaries,
        compaction_summary_md=ctx.compaction_summary_md,
    )

    if isinstance(result, ChatResponse):
        # Fake-stream word-by-word — no agent_runs row for direct chat responses.
        words = result.message.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.03)

        yield {
            "type": "done",
            "usage": _zero_usage("router"),
        }
        return

    if isinstance(result, DispatchAgent):
        yield {"type": "agent_selected", "agent_family": result.agent_family}
        async for ev in _dispatch(
            agent_family=result.agent_family,
            briefing=result.briefing,
            target_item_id=result.target_item_id,
            attached_item_ids=list(result.attached_item_ids),
            subtype=result.subtype,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
        ):
            yield ev
        return

    # Defensive fallback — should never reach here.
    logger.error("run_router returned unexpected type: %s", type(result))
    yield {"type": "token", "text": "حدث خطأ في توجيه الطلب. يرجى المحاولة مرة أخرى."}
    yield {"type": "done", "usage": _zero_usage("error")}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _dispatch(
    agent_family: str,
    briefing: str,
    target_item_id: str | None,
    attached_item_ids: list[str],
    subtype: str | None,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Invoke the appropriate specialist agent and stream results.

    Cap pre-flight: families deep_search and writing refuse when workspace_items
    would exceed _WORKSPACE_CAP, UNLESS target_item_id is set (editing
    an existing item does not create a new one). Memory family bypasses cap.
    """
    # ── Cap pre-flight ──────────────────────────────────────────────────
    if agent_family in ("deep_search", "writing") and target_item_id is None:
        count = _count_artifact_kinds(supabase, conversation_id)
        if count >= _WORKSPACE_CAP:
            yield {
                "type": "token",
                "text": (
                    "وصلت إلى الحد الأقصى من المستندات في هذه المحادثة (15). "
                    "يرجى حذف مستند قبل إنشاء جديد."
                ),
            }
            yield {"type": "done", "usage": _zero_usage("cap_rejected")}
            return  # NO agent_runs row on cap rejection.

    t0 = perf_counter()
    # agent_selected is emitted by _route; re-emit here only on explicit dispatch
    # (explicit_agent_family path has no prior agent_selected).
    yield {"type": "agent_run_started", "agent_family": agent_family, "subtype": subtype}

    run_result: SpecialistResult | None = None
    err_payload: dict | None = None
    status = "ok"

    with _logfire.span(
        "dispatch.specialist",
        agent_family=agent_family,
        subtype=subtype,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        target_item_id=target_item_id,
        attached_count=len(attached_item_ids),
    ):
        try:
            # Build MajorAgentInput — hydrate attached items + recent messages.
            attached_items = _load_attached_items(supabase, attached_item_ids)
            recent_messages = _load_recent_messages(supabase, conversation_id)

            major_input = MajorAgentInput(
                briefing=briefing,
                attached_items=attached_items,
                recent_messages=recent_messages,
                target_item_id=target_item_id,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
            )

            if agent_family == "deep_search":
                # Planner pre-flight: run the planner here (outside run_full_loop)
                # so DeferredToolRequests can be caught before any search phase runs.
                planner_plan_override = await _try_run_planner_for_dispatch(
                    briefing=briefing,
                    supabase=supabase,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    case_id=case_id,
                    user_message_id=user_message_id,
                    agent_family=agent_family,
                )
                # _try_run_planner_for_dispatch returns either:
                #   - a PlannerOutput  → continue with deep_search pipeline
                #   - None             → planner disabled / degraded fallback
                #   - the sentinel _DEFERRED_EMITTED → planner paused; caller must stop
                if planner_plan_override is _DEFERRED_EMITTED:
                    # The deferred event + done were yielded inside _try_run_planner_for_dispatch.
                    # We must NOT record a completed agent_runs row — the run is still alive
                    # (status='awaiting_user').  Return without falling through to the finally block
                    # by raising a dedicated sentinel that the outer try/except/finally handles.
                    raise _SkipRunRecord()
                run_result = await _run_deep_search(
                    major_input, supabase, plan_override=planner_plan_override
                )
            elif agent_family == "writing":
                run_result = await _run_writer(major_input, subtype, supabase)
            elif agent_family == "memory":
                run_result = await _run_memory(
                    briefing=briefing,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    supabase=supabase,
                )
            else:
                logger.error("_dispatch: unknown agent_family=%s", agent_family)
                yield {"type": "token", "text": "حدث خطأ: نوع المهمة غير معروف."}
                yield {"type": "done", "usage": _zero_usage("error")}
                return

            # Forward queued SSE events (workspace_item_created / workspace_item_updated / etc.)
            for ev in run_result.sse_events:
                yield ev

            # Stream chat_summary to chat — full body stays in workspace item only.
            if run_result.chat_summary:
                yield {"type": "token", "text": run_result.chat_summary}

            # Stream key_findings as a single token block after chat_summary.
            if run_result.key_findings:
                bullets = "\n\n" + "\n".join(f"• {k}" for k in run_result.key_findings)
                yield {"type": "token", "text": bullets}

            yield {
                "type": "done",
                "usage": {
                    "prompt_tokens": run_result.tokens_in or 0,
                    "completion_tokens": run_result.tokens_out or 0,
                    "model": run_result.model_used or agent_family,
                },
            }

        except _SkipRunRecord:
            # Planner issued DeferredToolRequests — the run is alive as 'awaiting_user'.
            # No completed agent_runs row should be written; no error SSE either.
            # The deferred events (agent_question + done) have already been yielded
            # by _try_run_planner_for_dispatch via _record_deferred.
            # We simply yield the agent_question + done that _record_deferred stored
            # and return without hitting the finally record_agent_run call.
            question_text = getattr(_SkipRunRecord, "_question", None)
            run_id_deferred = getattr(_SkipRunRecord, "_run_id", None)
            # Note: the events were already persisted to messages; we just need
            # to emit SSE events for the client.  _try_run_planner_for_dispatch
            # stored them on the _SkipRunRecord class for retrieval here.
            # Actually: _record_deferred returns (question, run_id) but we raised
            # before capturing those values here.  Instead we re-query the fresh row.
            deferred_row = _find_awaiting_user(supabase, conversation_id, user_id)
            if deferred_row:
                yield {
                    "type": "agent_question",
                    "run_id": str(deferred_row.get("run_id", "")),
                    "question": deferred_row.get("question_text", ""),
                }
            yield {"type": "done", "usage": _zero_usage("paused")}
            return  # skip finally record_agent_run

        except Exception as exc:
            logger.error("specialist %s failed: %s", agent_family, exc, exc_info=True)
            status = "error"
            err_payload = {"type": type(exc).__name__, "message": str(exc)[:500]}
            yield {
                "type": "token",
                "text": "عذراً، حدث خطأ أثناء تنفيذ المهمة. يرجى المحاولة مرة أخرى.",
            }
            yield {"type": "done", "usage": _zero_usage("error")}

        finally:
            duration_ms = int((perf_counter() - t0) * 1000)
            trace_id, span_id = _extract_logfire_ids()
            record_agent_run(
                supabase,
                AgentRunRecord(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    case_id=case_id,
                    agent_family=agent_family,
                    subtype=subtype,
                    message_id=user_message_id,
                    input_summary=briefing[:500],
                    output_item_id=getattr(run_result, "output_item_id", None),
                    duration_ms=duration_ms,
                    tokens_in=getattr(run_result, "tokens_in", None),
                    tokens_out=getattr(run_result, "tokens_out", None),
                    model_used=getattr(run_result, "model_used", None),
                    per_phase_stats=getattr(run_result, "per_phase_stats", {}) or {},
                    status=status,
                    error=err_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                ),
            )

    yield {"type": "agent_run_finished", "agent_family": agent_family}


# ---------------------------------------------------------------------------
# Specialist runners
# ---------------------------------------------------------------------------


async def _run_deep_search(
    input: MajorAgentInput,
    supabase: SupabaseClient,
    plan_override: "PlannerOutput | None" = None,
) -> SpecialistResult:
    """Run the deep_search_v4 URA pipeline and return a SpecialistResult.

    Extracted from the former _run_pydantic_ai_task deep_search branch.
    Key changes:
    - supabase passed explicitly (MajorAgentInput is DB-free by contract).
    - Full synthesis_md NOT yielded to chat; chat_summary + key_findings used instead.
    - SSE events from deps._events and publish_result.sse_events are collected into
      SpecialistResult.sse_events.
    - Token counts mapped from AggregatorOutput (currently zero — placeholders).
    - plan_override: when provided (Task 13.4/13.5), the planner step inside
      run_full_loop is skipped and this plan is applied directly to FullLoopDeps
      via apply_plan_to_deps.  This supports both the initial dispatch path (after
      DeferredToolRequests is ruled out) and the resume path (planner already ran
      on the prior turn).
    """
    import httpx

    from agents.agent_search import (
        SearchPublishDeps,
        SearchPublishInput,
        publish_search_result,
    )
    from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop
    from agents.utils.embeddings import embed_regulation_query_alibaba
    from backend.app.services.preferences_service import get_detail_level
    from shared.config import get_settings

    # Read detail_level from user_preferences; swallow errors and default.
    try:
        detail_level = get_detail_level(supabase, input.user_id)
    except Exception:
        logger.warning("get_detail_level failed; defaulting to 'medium'", exc_info=True)
        detail_level = "medium"

    settings = get_settings()
    sse_events: list[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        deps = FullLoopDeps(
            supabase=supabase,
            embedding_fn=embed_regulation_query_alibaba,
            jina_api_key=settings.JINA_RERANKER_API_KEY or "",
            http_client=http_client,
            detail_level=detail_level,
            # When we have a pre-computed plan (from initial dispatch or resume),
            # set enable_planner=False so run_full_loop doesn't run it again.
            # apply_plan_to_deps below will overlay the plan onto deps directly.
            enable_planner=False,
        )

        # Apply the pre-computed plan (if any) before handing deps to run_full_loop.
        if plan_override is not None:
            try:
                from agents.deep_search_v4.planner import apply_plan_to_deps, derive_aggregator_prompt_key
                deps = apply_plan_to_deps(deps, plan_override)
                deps._plan = plan_override
            except Exception as exc:
                logger.warning(
                    "_run_deep_search: apply_plan_to_deps failed, proceeding without plan: %s", exc,
                )

        agg_output = await run_full_loop(
            query=input.briefing,
            query_id=0,
            deps=deps,
        )

        # Collect pipeline-accumulated SSE events (progress events etc.).
        sse_events.extend(deps._events)

        # Persist via agent_search publisher.
        output_item_id: str | None = None
        try:
            publish_input = SearchPublishInput(
                user_id=input.user_id,
                conversation_id=input.conversation_id,
                case_id=input.case_id,
                message_id=None,  # user_message_id threaded through Task 10 via MajorAgentInput
                agg_output=agg_output,
                original_query=input.briefing,
                detail_level=detail_level,
                ura=getattr(deps, "_ura", None),
                reg_rqrs=list(getattr(deps, "_reg_rqrs", []) or []),
                comp_rqrs=list(getattr(deps, "_comp_rqrs", []) or []),
                case_rqrs=list(getattr(deps, "_case_rqrs", []) or []),
                per_executor_stats=dict(
                    getattr(deps, "_per_executor_stats", {}) or {}
                ),
            )
            publish_result = await publish_search_result(
                publish_input,
                SearchPublishDeps(supabase=supabase, logger=logger),
            )
            sse_events.extend(publish_result.sse_events)
            output_item_id = publish_result.item_id
        except Exception as exc:
            logger.warning("deep_search artifact persist failed: %s", exc, exc_info=True)

    return SpecialistResult(
        output_item_id=output_item_id,
        chat_summary=agg_output.chat_summary or "",
        key_findings=list(agg_output.key_findings or []),
        sse_events=sse_events,
        model_used=agg_output.model_used or "deep_search_v4",
        tokens_in=0,
        tokens_out=0,
        per_phase_stats={},
    )


async def _run_writer(
    input: MajorAgentInput,
    subtype: str | None,
    supabase: SupabaseClient,
) -> SpecialistResult:
    """Run the agent_writer pipeline and return a SpecialistResult.

    Extracted from the former _run_pydantic_ai_task writing branch.
    Key changes:
    - supabase passed explicitly (MajorAgentInput is DB-free by contract).
    - Workspace context loading replaced by attached_items from MajorAgentInput.
    - Full content_md NOT streamed to chat; chat_summary + key_findings used instead.
    - WriterDeps built with briefing + attached_items + revising_item_id already set,
      so _populate_deps_from_input in the runner fills any remaining gaps.
    """
    import httpx

    from agents.agent_writer import (
        WorkspaceContextBlock,
        WriterInput,
        build_writer_deps,
        handle_writer_turn,
    )
    from backend.app.services.preferences_service import get_detail_level

    try:
        detail_level = get_detail_level(supabase, input.user_id)
    except Exception:
        logger.warning("get_detail_level failed; defaulting to 'medium'", exc_info=True)
        detail_level = "medium"

    # Build research_items from attached_items: search-like kinds only.
    research_items: list[dict] = []
    for snap in input.attached_items:
        kind_lower = (snap.kind or "").lower()
        subtype_hint = (snap.metadata.get("subtype") or "").lower()
        if kind_lower in {"agent_search"} or subtype_hint in {"legal_synthesis", "agent_search", "report"}:
            research_items.append({
                "item_id": snap.item_id,
                "title": snap.title,
                "content_md": snap.content_md,
                "metadata": snap.metadata,
            })

    chosen_subtype = subtype or "memo"

    writer_input = WriterInput(
        user_id=input.user_id,
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        message_id=None,  # task 10 will pass user_message_id through MajorAgentInput
        user_request=input.briefing,
        subtype=chosen_subtype,  # type: ignore[arg-type]
        research_items=research_items,
        workspace_context=WorkspaceContextBlock(
            notes=[],
            attachments=[],
            convo_context=None,
        ),
        revising_item_id=input.target_item_id,
        detail_level=detail_level,  # type: ignore[arg-type]
        tone="formal",
    )

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        writer_deps = build_writer_deps(
            supabase=supabase,
            http_client=http_client,
            briefing=input.briefing,
            attached_items=list(input.attached_items),
            revising_item_id=input.target_item_id,
            detail_level=detail_level,
            tone="formal",
        )

        writer_output = await handle_writer_turn(writer_input, writer_deps)

    return SpecialistResult(
        output_item_id=writer_output.item_id,
        chat_summary=writer_output.chat_summary or "",
        key_findings=list(writer_output.key_findings or []),
        sse_events=list(writer_output.sse_events or []),
        model_used=writer_output.metadata.get("model_used", "agent_writer"),
        tokens_in=0,
        tokens_out=0,
        per_phase_stats={},
    )


async def _run_memory(
    briefing: str,
    conversation_id: str,
    user_id: str,
    supabase: SupabaseClient,
) -> SpecialistResult:
    """Run the memory family (explicit dispatch path).

    Wave 9: delegates to compact_conversation as a best-effort fallback.
    The router does not normally dispatch to memory; this path is only hit
    when the caller sets explicit_agent_family='memory'.
    """
    new_item_id: str | None = None
    try:
        new_item_id = await memory.compact_conversation(
            supabase=supabase,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("_run_memory compact_conversation failed: %s", exc, exc_info=True)

    summary = (
        "تمت معالجة الذاكرة: ضغط المحادثة اكتمل."
        if new_item_id
        else "لا حاجة لضغط المحادثة في الوقت الحالي."
    )

    return SpecialistResult(
        output_item_id=new_item_id,
        chat_summary=summary,
        key_findings=[],
        sse_events=[],
        model_used="memory",
        tokens_in=0,
        tokens_out=0,
        per_phase_stats={},
    )


