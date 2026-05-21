"""Agent orchestrator — routes messages, dispatches specialists, records runs.

Wave 9 rewrite (Task 7):
- OpenTask / TaskContinue / TaskEnd → gone (no task_state table dependency).
- agents.state imports → gone.
- Full specialist body NEVER streamed to chat; only chat_summary + key_findings.
- Pre-router memory hook: resummarize_dirty_items + compact_conversation.
- Dispatch records an agent_runs row in the finally block (fire-and-forget).
- Cap pre-flight: refuse deep_search / writing dispatch when workspace_items >= 15.
- Logfire span wraps _dispatch; trace_id / span_id populated on AgentRunRecord.

Planner-redesign rewiring:
- The planner owns the loop. _run_deep_search builds PlannerDeps and calls
  handle_planner_turn (phase 1 decide → phase 2 retrieve → phase 3 respond),
  returning a _DeepSearchOutcome (kind="completed" | "paused").
- Pre-route pause check: _find_awaiting_user / _expired / _resume_major_agent.
- A phase-1 ask_user pause comes back as kind="paused"; _dispatch records the
  'awaiting_user' agent_runs row + agent_question message and skips the run row.
- _run_deep_search accepts an optional `decision`: on resume, _resume_major_agent
  resumes planner_decider itself, then passes the PlannerDecision so phases 2–3
  run through the same convergence point as a fresh dispatch.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import AsyncGenerator

from supabase import Client as SupabaseClient

import agents.memory.agent as memory
from agents.deep_search_v4.planner.models import PriorSearchSummary
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
    user_id: str,
    conversation_id: str,
) -> list[WorkspaceItemSnapshot]:
    """Hydrate workspace item UUIDs into full WorkspaceItemSnapshot objects.

    Scope: ONLY items in the current conversation owned by the current user.
    The ``.eq("user_id", user_id)`` + ``.eq("conversation_id", conversation_id)``
    filters are **load-bearing** (§6.4 of the redesign spec): the backend's
    Supabase client runs as ``service_role`` and bypasses RLS, so these filters
    are the actual scope enforcement, not a defense-in-depth supplement.

    The router may emit a hallucinated ``attached_item_ids`` UUID that doesn't
    belong to this conversation or user; those rows are silently dropped (no
    error) so a single bad id doesn't poison the dispatch.
    """
    if not item_ids:
        return []
    snapshots: list[WorkspaceItemSnapshot] = []
    for item_id in item_ids:
        try:
            row = (
                supabase.table("workspace_items")
                .select("item_id, kind, title, content_md, metadata")
                .eq("item_id", item_id)
                .eq("user_id", user_id)
                .eq("conversation_id", conversation_id)
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


# Cap on how many prior agent_search items to surface to the planner. Bounded
# by _WORKSPACE_CAP (15) but the planner only needs the most relevant recents —
# 10 leaves room for older sub-tasks without ballooning the decider prompt.
_PRIOR_SEARCH_LIMIT = 10


def _load_case_brief(
    supabase: SupabaseClient, case_id: str, user_id: str
) -> str | None:
    """Render a planner-facing case brief string from lawyer_cases + case_memories.

    Wraps the existing :func:`agents.router.context._load_case_block` so both
    the router and the planner read from the same source. The router consumes
    a ``(metadata, memory_md)`` tuple; the planner only needs the rendered
    markdown — we return that directly. Returns ``None`` on any DB failure or
    when neither the case row nor its memories yielded anything to render.

    The ``user_id`` is threaded through to ``_load_case_block`` so the
    ``lawyer_cases`` lookup is scoped to ``lawyer_user_id = user_id``. This
    filter is **load-bearing** (§6.4) — service_role bypasses RLS.
    """
    if not case_id:
        return None
    try:
        from agents.router.context import _load_case_block  # lazy import
        _metadata, memory_md = _load_case_block(supabase, case_id, user_id)
        return memory_md
    except Exception as e:
        logger.warning("_load_case_brief failed for case_id=%s: %s", case_id, e)
        return None


def _load_prior_search_summaries(
    supabase: SupabaseClient, conversation_id: str, user_id: str
) -> list[PriorSearchSummary]:
    """Hydrate prior ``kind='agent_search'`` items into ``PriorSearchSummary`` list.

    Filters by ``user_id`` AND ``conversation_id`` (both **load-bearing** —
    service_role bypasses RLS per §6.4 of the redesign spec), ``kind='agent_search'``,
    and ``deleted_at IS NULL``. Confidence comes from ``metadata.confidence``
    and defaults to ``"medium"`` when missing or when the value isn't one of
    the three accepted literals. ``summary`` may be NULL/empty — see Window D
    race in §11a; tolerated by including empty string so the planner can still
    reason about prior task identity.

    Capped at :data:`_PRIOR_SEARCH_LIMIT` (most recent first), ordered by
    ``created_at DESC`` then re-sorted for chronological rendering downstream.
    """
    if not conversation_id:
        return []
    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id, title, describe_query, summary, metadata, created_at")
            .eq("user_id", user_id)
            .eq("conversation_id", conversation_id)
            .eq("kind", "agent_search")
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(_PRIOR_SEARCH_LIMIT)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(
            "_load_prior_search_summaries failed for conversation_id=%s: %s",
            conversation_id, e,
        )
        return []

    summaries: list[PriorSearchSummary] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        confidence_raw = (
            metadata.get("confidence") if isinstance(metadata, dict) else None
        )
        confidence = (
            confidence_raw
            if confidence_raw in ("high", "medium", "low")
            else "medium"
        )
        try:
            summaries.append(
                PriorSearchSummary(
                    item_id=row.get("item_id") or "",
                    title=row.get("title") or "",
                    describe_query=row.get("describe_query") or "",
                    summary=row.get("summary") or "",
                    confidence=confidence,
                    created_at=row.get("created_at") or "",
                )
            )
        except Exception as exc:
            logger.warning(
                "_load_prior_search_summaries: skipping malformed row %r: %s",
                row.get("item_id"), exc,
            )
    return summaries


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
        if isinstance(raw_history, bytes):
            history_bytes = raw_history
        elif isinstance(raw_history, str):
            # PostgREST returns BYTEA as Postgres-native '\x'-prefixed hex
            # by default. Some serializers also produce base64 — handle both.
            if raw_history.startswith("\\x"):
                history_bytes = bytes.fromhex(raw_history[2:])
            else:
                history_bytes = base64.b64decode(raw_history)
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

    # ── Resume the planner_decider (phase 1) with the user's answer ──────────
    # The two-phase planner resumes ONLY phase 1 here — it yields a
    # PlannerDecision. Phases 2–3 then run via _run_deep_search(decision=...),
    # the same convergence point as a fresh dispatch.
    #
    # Phase C: the decider's deps_type is now PlannerDeps (no longer None) — we
    # build a minimal deps object so the resume call still satisfies the
    # decider's contract. The comprehension surface is hydrated here too: the
    # user's clarification reply is already in `messages` (patched above before
    # this resume entry), so `recent_messages` reload picks it up. The decider
    # itself reads its prior turn's bytes from `message_history`, so the
    # rendered comprehension is the *post-pause* world, not the pre-pause one
    # (per §6.3.1).
    try:
        import httpx as _httpx

        from agents.deep_search_v4.planner import PlannerDecision, build_planner_deps
        from agents.deep_search_v4.planner.agent import (
            create_planner_decider,
            PLANNER_DECIDER_LIMITS,
        )
        from agents.utils.embeddings import embed_regulation_query_alibaba
        from shared.config import get_settings as _get_settings
        from pydantic_ai import DeferredToolRequests

        _resume_case_brief = (
            _load_case_brief(supabase, case_id, user_id) if case_id else None
        )
        _resume_prior_searches = _load_prior_search_summaries(
            supabase, conversation_id, user_id,
        )
        _resume_attached_items = _load_attached_items(
            supabase, [], user_id, conversation_id
        )
        _resume_recent_messages = _load_recent_messages(supabase, conversation_id)
        _resume_settings = _get_settings()

        async with _httpx.AsyncClient(timeout=30.0) as _resume_http:
            _resume_deps = build_planner_deps(
                supabase=supabase,
                embedding_fn=embed_regulation_query_alibaba,
                http_client=_resume_http,
                jina_api_key=_resume_settings.JINA_RERANKER_API_KEY or "",
                user_id=user_id,
                conversation_id=conversation_id,
                case_brief=_resume_case_brief,
                recent_messages=_resume_recent_messages,
                prior_searches=_resume_prior_searches,
                attached_items=_resume_attached_items,
            )

            planner_decider = create_planner_decider()
            planner_result = await planner_decider.run(
                "",  # user_prompt unused on resume — history provides full context
                message_history=history,
                deferred_tool_results=results,
                deps=_resume_deps,
                usage_limits=PLANNER_DECIDER_LIMITS,
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
            describe_query=user_reply,
            # Inherit the original task_label from the paused row so the chained
            # pause carries the same label forward (router did not re-run here).
            task_label=(pending.get("task_label") if pending else None),
        )
        # Close out the previous run row (it's superseded by the new one).
        update_run_status(supabase, run_id, "abandoned")
        yield {"type": "agent_question", "run_id": new_run_id or "", "question": new_question}
        yield {"type": "done", "usage": _zero_usage("paused")}
        return

    # ── PlannerDecision.aborted — give up on deep_search, re-route fresh ────
    if isinstance(planner_output, PlannerDecision) and planner_output.aborted:
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

    # ── Unexpected output type — bail out cleanly ───────────────────────────
    if not isinstance(planner_output, PlannerDecision):
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
        attached_items = _load_attached_items(
            supabase, [], user_id, conversation_id
        )
        recent_messages = _load_recent_messages(supabase, conversation_id)
        # On resume the router has NOT re-run, so task_label / describe_query
        # are inherited from the paused agent_runs row (populated at original
        # dispatch time). Fall back to a placeholder if missing — keeps the
        # required MajorAgentInput field satisfied without inventing data.
        resumed_task_label = (pending.get("task_label") or "").strip() or "متابعة المحادثة"
        major_input = MajorAgentInput(
            describe_query=user_reply,
            task_label=resumed_task_label,
            attached_items=attached_items,
            recent_messages=recent_messages,
            target_item_id=None,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
        )

        # Phases 2–3 run via the same convergence point as fresh dispatch.
        # decision is supplied → phase 1 is skipped → cannot pause again.
        ds_outcome = await _run_deep_search(
            major_input, supabase, decision=planner_output
        )
        run_result = ds_outcome.result

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
        # Embed the user's reply into deferred_payload so the agent_runs row
        # carries both the question_text AND the answer side-by-side without a
        # join through the messages table.
        merged_payload = dict(pending.get("deferred_payload") or {})
        merged_payload["user_reply"] = user_reply
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
            deferred_payload=merged_payload,
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
    describe_query: str,
    task_label: str | None = None,
) -> tuple[str, str | None]:
    """Persist pause state for a DeferredToolRequests planner output.

    Inserts the agent_runs row and the agent_question message row.
    Returns (question_text, run_id).  Both are best-effort; run_id may be None
    on DB failure.

    ``task_label`` is the router-emitted short Arabic label for the dispatched
    task (Wave 1 redesign). Persisted to ``agent_runs.task_label`` so the
    resume path can recover it without re-running the router.
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
            task_label=task_label,
            input_summary=describe_query[:500],
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


class _SkipRunRecord(Exception):
    """Raised inside _dispatch's try block to skip the agent_runs INSERT.

    Used when the run transitions to 'awaiting_user' and is persisted by
    _record_deferred instead of by the normal finally block.
    """


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def handle_message(
    question: str,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    user_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Main entry point for all chat turns.

    1. Pre-router memory hook (best-effort).
    2. Run router which fans out to specialist agents.

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
            describe_query=result.describe_query,
            task_label=result.task_label,
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
    describe_query: str,
    task_label: str,
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

    ``describe_query`` is the router-emitted query description (Wave 1
    redesign — was ``briefing`` pre-redesign). ``task_label`` is the
    router-emitted short Arabic content-derived label used as both the
    workspace item title and the ``agent_runs.task_label`` value.
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
    yield {"type": "agent_run_started", "agent_family": agent_family, "subtype": subtype}

    run_result: SpecialistResult | None = None
    err_payload: dict | None = None
    status = "ok"

    # PII note: user_id intentionally NOT on this span. The monitor recovers
    # user_id via Supabase join on conversation_id — every persisted row
    # (agent_runs, messages, conversations, workspace_items) carries user_id
    # as a column. Keeping user_id off Logfire spans narrows the PII surface
    # area across the 30-day retention window.
    with _logfire.span(
        "dispatch.specialist",
        agent_family=agent_family,
        subtype=subtype,
        conversation_id=conversation_id,
        case_id=case_id,
        target_item_id=target_item_id,
        attached_count=len(attached_item_ids),
    ) as _dispatch_span:
        # §6.1 / Wave 1 redesign — surface the router-emitted dispatch context
        # as Logfire attributes so dashboards can filter on task identity.
        # `describe_query_chars` (not full text) keeps span attribute size
        # bounded — describe_query can be up to ~150 words and full-text in
        # attributes balloons telemetry cost.
        try:
            _dispatch_span.set_attributes({
                "dispatch.task_label": task_label,
                "dispatch.describe_query_chars": len(describe_query or ""),
                "dispatch.agent_family": agent_family,
                "dispatch.attached_item_count": len(attached_item_ids or []),
            })
        except Exception:
            pass
        try:
            # Build MajorAgentInput — hydrate attached items + recent messages.
            # _load_attached_items takes user_id + conversation_id so the
            # workspace_items lookup is scoped to this conversation only
            # (load-bearing — service_role bypasses RLS per §6.4).
            attached_items = _load_attached_items(
                supabase, attached_item_ids, user_id, conversation_id
            )
            recent_messages = _load_recent_messages(supabase, conversation_id)

            major_input = MajorAgentInput(
                describe_query=describe_query,
                task_label=task_label,
                attached_items=attached_items,
                recent_messages=recent_messages,
                target_item_id=target_item_id,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
            )

            if agent_family == "deep_search":
                # The planner owns the loop: _run_deep_search → handle_planner_turn
                # runs phase 1 (decide) → phase 2 (retrieve) → phase 3 (respond).
                # A phase-1 ask_user pause comes back as kind="paused".
                ds_outcome = await _run_deep_search(major_input, supabase)
                if ds_outcome.kind == "paused":
                    # Persist the awaiting_user row + question message, then skip
                    # the normal completed-run record (the run stays alive).
                    _record_deferred(
                        supabase=supabase,
                        planner_result=ds_outcome.planner_result,
                        planner_output=ds_outcome.deferred,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        case_id=case_id,
                        user_message_id=user_message_id,
                        agent_family=agent_family,
                        describe_query=describe_query,
                        task_label=task_label,
                    )
                    raise _SkipRunRecord()
                run_result = ds_outcome.result
            elif agent_family == "writing":
                run_result = await _run_writer(major_input, subtype, supabase)
            elif agent_family == "memory":
                run_result = await _run_memory(
                    describe_query=describe_query,
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
            # Phase-1 ask_user pause — _run_deep_search returned kind="paused"
            # and the deep_search branch already persisted the awaiting_user row
            # + question message via _record_deferred. The run is alive; no
            # completed agent_runs row should be written and no error SSE.
            # Re-query the fresh row to emit the agent_question SSE event.
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
                    task_label=task_label,
                    input_summary=describe_query[:500],
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


@dataclass
class _DeepSearchOutcome:
    """What :func:`_run_deep_search` returns.

    - ``kind="completed"`` — the planner ran phases 2–3; ``result`` is the
      ``SpecialistResult`` to stream.
    - ``kind="paused"`` — phase 1 called ``ask_user``; ``planner_result`` +
      ``deferred`` carry the pause state for :func:`_record_deferred`.
    """

    kind: str  # "completed" | "paused"
    result: SpecialistResult | None = None
    planner_result: Any = None
    deferred: Any = None


async def _run_deep_search(
    input: MajorAgentInput,
    supabase: SupabaseClient,
    decision: Any = None,
) -> _DeepSearchOutcome:
    """Run the planner-driven deep_search_v4 loop.

    Builds ``PlannerDeps``, then runs ``handle_planner_turn`` — phase 1 decide
    (skipped when ``decision`` is supplied), phase 2 retrieve, phase 3 respond.

    - On a phase-1 ``ask_user`` pause → returns ``kind="paused"`` (the caller
      persists it via ``_record_deferred``). Only happens on fresh dispatch.
    - Otherwise: branches on ``response.build_artifact`` (Phase E §6.3).
      * ``build_artifact=True`` (default) → publishes the ``agent_search``
        artifact via ``publish_search_result``; ``output_item_id`` is set.
      * ``build_artifact=False`` → publish is SKIPPED entirely
        (``output_item_id`` stays ``None``). When ``response.referenced_item_id``
        is set (prior-artifact-covers branch), appends a
        ``referenced_existing_item`` SSE event so the frontend can highlight or
        chip the existing card. The user-facing chat summary still flows from
        the responder's ``chat_summary_md`` + ``suggestion_md``.
    Returns ``kind="completed"`` with a ``SpecialistResult`` in either branch.

    ``decision``: supplied on the resume path (phase 1 already resolved by
    ``_resume_major_agent``); ``None`` on fresh dispatch.

    The user-facing chat summary is written by the **planner** (phase 3):
    ``SpecialistResult.chat_summary`` = the planner's ``chat_summary_md`` +
    ``suggestion_md``. ``key_findings`` was historically copied from the
    aggregator artifact; since Wave 10 the aggregator no longer emits it
    (the per-artifact agent-facing summary is written asynchronously by the
    Supabase-trigger-driven ``artifact_summarizer`` to ``workspace_items.summary``).
    """
    import httpx

    from agents.agent_search import (
        SearchPublishDeps,
        SearchPublishInput,
        publish_search_result,
    )
    from agents.deep_search_v4.planner import build_planner_deps, handle_planner_turn
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
    output_item_id: str | None = None

    # Phase C — hydrate the comprehension surface for the planner decider.
    # These loaders are best-effort; failures return safe defaults so a flaky
    # DB query doesn't abort the dispatch. Reload on every entry into
    # _run_deep_search (including resume) per the invariant in
    # `planner/deps.py` — PlannerDeps is never persisted across pause.
    case_brief = (
        _load_case_brief(supabase, input.case_id, input.user_id)
        if input.case_id
        else None
    )
    prior_searches = _load_prior_search_summaries(
        supabase, input.conversation_id, input.user_id
    )

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        deps = build_planner_deps(
            supabase=supabase,
            embedding_fn=embed_regulation_query_alibaba,
            http_client=http_client,
            jina_api_key=settings.JINA_RERANKER_API_KEY or "",
            detail_level=detail_level,
            user_id=input.user_id,
            conversation_id=input.conversation_id,
            case_brief=case_brief,
            recent_messages=list(input.recent_messages),
            prior_searches=prior_searches,
            attached_items=list(input.attached_items),
        )

        # The planner owns the loop — decide → retrieve → respond. Never raises.
        # `describe_query` is the renamed positional parameter (Phase C —
        # previously `briefing`, mechanical rename per the Wave 1 plan-reviewer
        # follow-up note).
        turn = await handle_planner_turn(input.describe_query, deps, decision=decision)

        # Pipeline-accumulated SSE events (planner + executor progress).
        sse_events.extend(deps._events)

        if turn.kind == "paused":
            # Phase-1 ask_user pause — hand the raw pause state back to the
            # caller, which records the awaiting_user row.
            return _DeepSearchOutcome(
                kind="paused",
                planner_result=turn.planner_result,
                deferred=turn.deferred,
            )

        # ── completed — Phase E publish-gate branch (§6.3) ─────────────────
        # `response.build_artifact` is the orchestrator's branch input:
        #   True  → publish the agent_search artifact (default path).
        #   False → skip publish entirely; output_item_id stays None. When
        #           response.referenced_item_id is set, emit a
        #           `referenced_existing_item` SSE event so the frontend can
        #           highlight/chip the prior covering card.
        agg_output = turn.agg_output
        response = turn.response
        should_publish = (
            agg_output is not None
            and response is not None
            and getattr(response, "build_artifact", True)
        )
        if should_publish:
            try:
                publish_input = SearchPublishInput(
                    user_id=input.user_id,
                    conversation_id=input.conversation_id,
                    case_id=input.case_id,
                    message_id=None,
                    agg_output=agg_output,
                    original_query=input.describe_query,
                    detail_level=detail_level,
                    ura=getattr(deps, "_ura", None),
                    reg_rqrs=list(getattr(deps, "_reg_rqrs", []) or []),
                    comp_rqrs=list(getattr(deps, "_comp_rqrs", []) or []),
                    case_rqrs=list(getattr(deps, "_case_rqrs", []) or []),
                    per_executor_stats=dict(
                        getattr(deps, "_per_executor_stats", {}) or {}
                    ),
                    # Wave 1 redesign: router-emitted title/query description.
                    task_label=input.task_label,
                    describe_query=input.describe_query,
                )
                publish_result = await publish_search_result(
                    publish_input,
                    SearchPublishDeps(supabase=supabase, logger=logger),
                )
                sse_events.extend(publish_result.sse_events)
                output_item_id = publish_result.item_id
            except Exception as exc:
                logger.warning("deep_search artifact persist failed: %s", exc, exc_info=True)
        elif response is not None and not getattr(response, "build_artifact", True):
            # Phase E build_artifact=False branch: no new card, no publish.
            # When the responder identified a prior covering artifact, emit a
            # SSE event so the frontend can highlight/chip the existing card.
            referenced_item_id = getattr(response, "referenced_item_id", None)
            if referenced_item_id:
                sse_events.append({
                    "type": "referenced_existing_item",
                    "item_id": referenced_item_id,
                })
            logger.info(
                "deep_search: build_artifact=False (referenced_item_id=%s) — "
                "skipped publish",
                referenced_item_id,
            )

    # Aggregate token usage from per-executor stats (planner + aggregator tokens
    # land in Logfire spans, not this dict — tracked as a follow-up).
    stats: dict = dict(getattr(deps, "_per_executor_stats", {}) or {})
    # §7 forensic surface — planner decisions on this turn. EVENT_DECIDED /
    # EVENT_RESPONDED already log these to Logfire for dashboards; this writes
    # them to agent_runs.per_phase_stats for post-hoc SQL queries.
    decision = getattr(deps, "_decision", None)
    if decision is not None:
        stats["planner_brief"] = getattr(decision, "planner_brief", "") or ""
        stats["context_labels_used"] = list(getattr(decision, "context_labels", []) or [])
    if response is not None:
        stats["build_artifact"] = getattr(response, "build_artifact", True)
        stats["referenced_item_id"] = getattr(response, "referenced_item_id", None)
    tokens_in = sum(
        int(v.get("total_tokens_in", 0) or 0)
        for v in stats.values()
        if isinstance(v, dict)
    )
    tokens_out = sum(
        int(v.get("total_tokens_out", 0) or 0)
        for v in stats.values()
        if isinstance(v, dict)
    )

    # chat_summary = planner's phase-3 prose + the next-step suggestion.
    # `response` already captured above for the build_artifact branch.
    chat_summary = (getattr(response, "chat_summary_md", "") or "") if response else ""
    suggestion = (getattr(response, "suggestion_md", "") or "").strip() if response else ""
    if suggestion:
        chat_summary = f"{chat_summary}\n\n{suggestion}" if chat_summary else suggestion
    # key_findings: aggregator no longer emits these (Wave 10 — moved to the
    # async artifact_summarizer). Kept as a default-empty list so the SSE
    # bullet block simply yields nothing for deep_search turns.
    key_findings: list[str] = []
    model_used = (
        getattr(agg_output, "model_used", None) or "deep_search_v4"
        if agg_output
        else "deep_search_v4"
    )

    return _DeepSearchOutcome(
        kind="completed",
        result=SpecialistResult(
            output_item_id=output_item_id,
            chat_summary=chat_summary,
            key_findings=key_findings,
            sse_events=sse_events,
            model_used=model_used,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            per_phase_stats=stats,
        ),
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
    - WriterDeps built with describe_query + task_label + attached_items +
      revising_item_id already set (Wave 1 redesign — was ``briefing`` field
      pre-redesign), so _populate_deps_from_input in the runner fills any
      remaining gaps.
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
        user_request=input.describe_query,
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
            describe_query=input.describe_query,
            task_label=input.task_label,
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
    describe_query: str,
    conversation_id: str,
    user_id: str,
    supabase: SupabaseClient,
) -> SpecialistResult:
    """Run the memory family.

    Wave 9: delegates to compact_conversation as a best-effort fallback.
    The router does not normally dispatch to memory.

    ``describe_query`` is accepted for parity with the other dispatch paths
    (Wave 1 redesign). Memory's output items are sparse — task_label /
    describe_query plumbing into a publisher surface is deferred until memory
    grows a real artifact pipeline.
    """
    # Silence unused-arg lint; reserved for the future memory artifact pipeline.
    _ = describe_query

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


