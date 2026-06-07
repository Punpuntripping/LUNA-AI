"""Agent orchestrator — routes messages, dispatches specialists, records runs.

Wave 9 rewrite (Task 7):
- OpenTask / TaskContinue / TaskEnd → gone (no task_state table dependency).
- agents.state imports → gone.
- Full specialist body NEVER streamed to chat; only chat_summary + key_findings.
- Pre-router memory hook: resummarize_dirty_items + compact_conversation.
- Cap pre-flight: refuse deep_search / writing dispatch when workspace_items >= 15.
- Token/cost: handle_message opens a collect_llm_calls scope; every model call
  in the turn lands one row in the llm_calls ledger (deep_search fed from its
  per_phase_stats), flushed + quota-settled once at turn end. No completed-run
  agent_runs row is written — cost lives in llm_calls, outcome on the Logfire span.
- Pause/resume state lives in paused_runs (migration 060), written via
  agents/paused_runs.py and deleted on resolve. run_id (migration 061) ties a
  run's llm_calls across the pause boundary.

Planner-redesign rewiring:
- The planner owns the loop. _run_deep_search builds PlannerDeps and calls
  handle_planner_turn (phase 1 decide → phase 2 retrieve → phase 3 respond),
  returning a _DeepSearchOutcome (kind="completed" | "paused").
- Pre-route pause check: _find_awaiting_user / _expired / _resume_major_agent.
- A phase-1 ask_user pause comes back as kind="paused"; _dispatch writes the
  paused_runs row + agent_question message and keeps the run alive.
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
from agents.memory.ocr_extractor import run_ocr_extraction
from agents.memory.summarize import summarize_workspace_item
from agents.deep_search_v4.planner.models import PriorSearchSummary
from agents.models import (
    ChatResponse,
    DispatchAgent,
    MajorAgentInput,
    ChatMessageSnapshot,
    WorkspaceItemSnapshot,
    SpecialistResult,
)
from agents.paused_runs import (
    PauseRecord,
    find_open_pause,
    is_expired,
    record_pause,
    resolve_pause,
)
from agents.utils.usage_sink import bind_run_id, collect_llm_calls, record_call
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
                .select("item_id, wi_seq, kind, title, content_md, metadata")
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
                        wi_seq=d.get("wi_seq"),
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
            .select("item_id, wi_seq, title, describe_query, summary, metadata, created_at")
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
                    wi_seq=row.get("wi_seq"),
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


def _feed_resume_decider(planner_result: Any) -> None:
    """Emit one ``deep_search.planner`` ledger row for the RESUME decider.

    Every other deep_search stage now self-emits at its own boundary (executors,
    sector_picker, aggregator in deep_search_v4; the fresh planner decide+respond
    in planner/runner.py). The one exception is the resume decider, which runs
    raw in ``_resume_major_agent_inner`` outside ``handle_planner_turn`` — feed it
    here. Best-effort; appends to the active scope and inherits the bound run_id.
    """
    try:
        usage = planner_result.usage()
        ti = int(getattr(usage, "input_tokens", 0) or 0)
        to = int(getattr(usage, "output_tokens", 0) or 0)
        if not ti and not to:
            return
        details = getattr(usage, "details", None) or {}
        from agents.utils.agent_models import AGENT_MODELS, resolve_chain
        model = resolve_chain(AGENT_MODELS["planner_decider"])[0] if "planner_decider" in AGENT_MODELS else None
        record_call(
            agent="deep_search.planner",
            model=model,
            agent_family="deep_search",
            tokens_in=ti,
            tokens_out=to,
            tokens_reasoning=int(details.get("reasoning_tokens", 0) or 0),
            tokens_cached=int(getattr(usage, "cache_read_tokens", 0) or 0),
        )
    except Exception:
        logger.debug("resume decider ledger feed failed", exc_info=True)


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
    """Return the most-recent open pause for this conversation, or None.

    Thin delegate to ``paused_runs.find_open_pause`` (migration 060 moved
    pause state out of agent_runs into its own delete-on-resolve table)."""
    return find_open_pause(supabase, conversation_id, user_id)


def _expired(pending: dict) -> bool:
    """Return True when the pause window has passed.

    Thin delegate to ``paused_runs.is_expired``."""
    return is_expired(pending)


async def _resume_major_agent(
    pending: dict,
    user_reply: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Resume a paused agent run — binds the original run's id so the resume
    leg's LLM calls share it (migration 061), then delegates to the body."""
    with bind_run_id(str(pending.get("run_id", ""))):
        async for ev in _resume_major_agent_inner(
            pending=pending,
            user_reply=user_reply,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        ):
            yield ev


async def _resume_major_agent_inner(
    pending: dict,
    user_reply: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Resume a paused agent run after the user has replied to an ask_user question.

    ``deep_search`` and ``writing`` support pause/resume. Any other
    agent_family (memory, etc.) is abandoned and re-routed fresh via the
    normal router path.

    ``assistant_message_id`` is the FK of the assistant-message placeholder
    for THIS resume turn (a new placeholder is created on every user reply
    by message_service.py). Threaded to the downstream publish step so the
    artifact produced on resume carries ``workspace_items.message_id``.
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

    # ── Only deep_search + writing support resume; abandon everything else. ──
    if agent_family not in ("deep_search", "writing"):
        logger.info(
            "_resume_major_agent: agent_family=%s does not support resume; abandoning run_id=%s",
            agent_family, run_id,
        )
        resolve_pause(supabase, run_id)
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
    # Shared by both resume families: the pause row's serialized
    # ``message_history`` bytes (written by _record_deferred via
    # planner_result.all_messages_json()) + the user's reply wrapped in a
    # DeferredToolResults keyed by the stored tool_call_id.
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
        resolve_pause(supabase, run_id)
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

    # ── writing resume ──────────────────────────────────────────────────────
    # The writer_planner owns its own loop; on resume we hand it the rehydrated
    # history + the user's reply and it drives writing_executor + publish (or
    # pauses again on a 2nd present_plan round). Mirrors the fresh-dispatch
    # writing branch in _dispatch (~line 1144): completed → stream the
    # SpecialistResult; paused → re-record the pause and keep the run alive.
    if agent_family == "writing":
        yield {"type": "agent_run_started", "agent_family": "writing", "subtype": None}
        # Set when the planner pauses AGAIN and we re-record a NEW pause row on
        # the SAME run_id — the finally must then NOT resolve_pause (that would
        # delete the row we just wrote and kill the chained pause).
        _rewrote_pause = False
        try:
            attached_items = _load_attached_items(supabase, [], user_id, conversation_id)
            recent_messages = _load_recent_messages(supabase, conversation_id)
            resumed_task_label = (
                (pending.get("task_label") or "").strip() or "متابعة المحادثة"
            )
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

            wp_outcome = await _run_writer(
                major_input,
                None,  # subtype hint — advisory only, planner re-derives
                supabase,
                assistant_message_id=assistant_message_id,
                message_history=history,
                deferred_results=results,
            )

            if wp_outcome.kind == "paused":
                # The planner paused AGAIN (e.g. a 2nd present_plan round).
                # Mirror the deep_search chained-pause path (~line 565): mint a
                # FRESH run_id for the new pause (paused_runs.run_id is the PK —
                # reusing this leg's id would collide on INSERT and silently drop
                # the new row), then resolve THIS leg's consumed row. Keep the run
                # alive — do NOT abandon. The decider self-captures its cost via
                # the tracking hook, so no explicit feed here. _rewrote_pause
                # stops the finally from resolving the (already-resolved) old id.
                _rewrote_pause = True
                new_question, new_run_id = _record_deferred(
                    supabase=supabase,
                    planner_result=wp_outcome.planner_result,
                    planner_output=wp_outcome.deferred,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    case_id=case_id,
                    user_message_id=user_message_id,
                    agent_family="writing",
                    describe_query=user_reply,
                    # Inherit the original task_label (router did not re-run).
                    task_label=(pending.get("task_label") if pending else None),
                    pause_reason=wp_outcome.pause_reason or "clarify",
                )
                # Close out the previous run row (superseded by the new one).
                resolve_pause(supabase, run_id)
                yield {
                    "type": "agent_question",
                    "run_id": new_run_id or "",
                    "question": new_question,
                }
                yield {"type": "done", "usage": _zero_usage("paused")}
                yield {"type": "agent_run_finished", "agent_family": "writing"}
                return

            # completed — stream the SpecialistResult (mirror fresh dispatch).
            run_result = wp_outcome.result
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
                    "model": run_result.model_used or "writer_planner_decider",
                },
            }
        except asyncio.CancelledError:
            logger.info(
                "_resume_major_agent: writing run cancelled mid-resume run_id=%s",
                run_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "_resume_major_agent: writing run failed for run_id=%s: %s",
                run_id, exc, exc_info=True,
            )
            yield {
                "type": "token",
                "text": "عذراً، حدث خطأ أثناء استئناف الكتابة. يرجى المحاولة مرة أخرى.",
            }
            yield {"type": "done", "usage": _zero_usage("error")}
        finally:
            # The pause is consumed (resumed to completion / error / cancel) —
            # drop the row so it can't be resumed again. Skip when we re-wrote a
            # chained pause: that branch already resolved THIS leg's row and
            # persisted a NEW row under a fresh run_id which must survive.
            if not _rewrote_pause:
                resolve_pause(supabase, run_id)

        yield {"type": "agent_run_finished", "agent_family": "writing"}
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

        # Rebuild the WI alias map on resume — PlannerDeps is never persisted
        # across pause, so the map has to be reconstructed from the same
        # surfaces the original dispatch saw.
        _resume_alias_map: dict[int, str] = {}
        for _p in _resume_prior_searches:
            if _p.wi_seq is not None and _p.item_id:
                _resume_alias_map[int(_p.wi_seq)] = _p.item_id
        for _s in _resume_attached_items:
            if _s.wi_seq is not None and _s.item_id:
                _resume_alias_map[int(_s.wi_seq)] = _s.item_id

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
                wi_alias_map=_resume_alias_map,
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
            # The resume decider runs raw (not run_tracked) and outside
            # handle_planner_turn's stats, so feed its cost to the ledger
            # (run_id already bound by the wrapper).
            _feed_resume_decider(planner_result)
    except Exception as exc:
        logger.error(
            "_resume_major_agent: planner resume failed for run_id=%s: %s",
            run_id, exc, exc_info=True,
        )
        resolve_pause(supabase, run_id)
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
        resolve_pause(supabase, run_id)
        yield {"type": "agent_question", "run_id": new_run_id or "", "question": new_question}
        yield {"type": "done", "usage": _zero_usage("paused")}
        return

    # ── PlannerDecision.aborted — give up on deep_search, re-route fresh ────
    if isinstance(planner_output, PlannerDecision) and planner_output.aborted:
        logger.info(
            "_resume_major_agent: planner aborted run_id=%s; re-routing via router",
            run_id,
        )
        resolve_pause(supabase, run_id)
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
        resolve_pause(supabase, run_id)
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
            major_input, supabase, decision=planner_output,
            assistant_message_id=assistant_message_id,
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

    except asyncio.CancelledError:
        # Convo-1 forensics bug #2: cancellation during resume must NOT persist
        # as status='ok'. See _dispatch sibling handler for the rationale.
        logger.info(
            "_resume_major_agent: deep_search run cancelled mid-resume run_id=%s",
            run_id,
        )
        status = "cancelled"
        err_payload = {
            "type": "CancelledError",
            "message": "resume cancelled mid-run",
        }
        raise

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
        # Cost self-emits at each stage's boundary (executors / sector_picker /
        # aggregator in deep_search_v4, planner respond in planner/runner.py;
        # the resume decider fed above) — all inside the capture scope. Nothing
        # to feed here.
        # The pause is consumed (resumed to completion / error / cancel) — drop
        # the paused_runs row so it can't be resumed again. status / err_payload
        # are surfaced via the Logfire span + SSE; no DB row to flip.
        resolve_pause(supabase, run_id)

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
    pause_reason: str = "clarify",
    run_id: str | None = None,
) -> tuple[str, str | None]:
    """Persist pause state for a DeferredToolRequests planner output.

    Inserts the paused_runs row (migration 060) and the agent_question message
    row. Returns (question_text, run_id).  Both are best-effort; run_id may be
    None on DB failure.

    ``task_label`` is the router-emitted short Arabic label for the dispatched
    task (Wave 1 redesign). Persisted to ``paused_runs.task_label`` so the
    resume path can recover it without re-running the router.

    ``pause_reason`` (migration 053) distinguishes pause flavors:
        'clarify'      — ask_user deferred (plain Arabic question)
        'approve_plan' — present_plan_for_approval deferred (plan_md awaiting yes/no/edit)
    Legacy ask_user callers (deep_search) accept the default 'clarify'. The
    writer_planner runner derives the value from the deferred tool_name.
    """
    from typing import Any as _Any

    pending_call = planner_output.calls[0]
    # ToolCallPart.args is `str | dict | None` (JSON string when from streaming
    # providers, dict when synthesized locally); args_as_dict() normalizes both.
    pending_args = pending_call.args_as_dict()
    # Pull the question text from whichever arg the deferred tool used:
    # ask_user → 'question' ; present_plan_for_approval → 'plan_md'.
    question = (
        pending_args.get("question")
        or pending_args.get("plan_md")
        or ""
    )
    now = _now_utc()

    run_id = record_pause(
        supabase,
        PauseRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            agent_family=agent_family,
            task_label=task_label,
            pause_reason=pause_reason,
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
        run_id=run_id,
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
                "pause_reason": pause_reason,
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
    assistant_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Public entry point for all chat turns.

    Thin wrapper that opens the per-turn token/cost capture scope
    (``collect_llm_calls``) around the whole turn — OCR, memory pre-hook,
    router, dispatch, and the resume path. Every LLM call inside accrues to one
    ``llm_calls`` buffer attributed to ``user_message_id`` and flushed (+ quota
    settled) once when this generator finishes or is closed (SSE disconnect /
    gateway timeout included). See ``agents/utils/usage_sink.py``.
    """
    with collect_llm_calls(
        supabase,
        conversation_id=conversation_id,
        user_id=user_id,
        message_id=user_message_id,
        case_id=case_id,
    ):
        async for ev in _handle_message_inner(
            question=question,
            user_id=user_id,
            conversation_id=conversation_id,
            supabase=supabase,
            case_id=case_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        ):
            yield ev


async def _handle_message_inner(
    question: str,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Main entry point for all chat turns.

    1. Pre-router memory hook (best-effort).
    2. Run router which fans out to specialist agents.

    ``user_message_id`` is the FK of the persisted user message (inserted
    before the AI call, per CLAUDE.md rule #7). Threaded to
    ``agent_runs.message_id`` so the audit row links to the triggering
    message.

    ``assistant_message_id`` is the FK of the assistant-message placeholder
    (allocated by ``backend/app/services/message_service.py`` before this
    function is called). Threaded all the way to the publishers so any
    workspace_item produced by this turn carries it as
    ``workspace_items.message_id`` — the assistant message that produced
    the artifact. Without this, agent-produced workspace_items end up with
    a NULL message_id and the chat ↔ artifact linkage breaks.
    """
    # 0. Pre-route pause check — resume a pending major agent if one exists.
    pending = _find_awaiting_user(supabase, conversation_id, user_id)
    if pending:
        if _expired(pending):
            logger.info(
                "handle_message: run_id=%s expired; marking timeout and proceeding normally",
                pending.get("run_id"),
            )
            resolve_pause(supabase, str(pending.get("run_id", "")))
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
                assistant_message_id=assistant_message_id,
            ):
                yield ev
            return

    # 1. Pre-router memory hook — best-effort, never aborts the turn.
    try:
        await memory.resummarize_dirty_items(supabase, conversation_id)
        await memory.compact_conversation(supabase, conversation_id, user_id)
    except Exception:
        logger.warning("memory pre-hook failed", exc_info=True)

    # 1b. OCR memory step — extract text from new PDF/image attachments so the
    #     router (and any dispatched agent) can see document content.
    ocr_item_ids: list[str] = []
    try:
        ocr_item_ids = await run_ocr_extraction(supabase, conversation_id, user_id)
    except Exception:
        logger.warning("OCR extraction step failed", exc_info=True)

    # 1c. Summarize the freshly-OCR'd attachments inline (awaited) before routing.
    for _ocr_item_id in ocr_item_ids:
        try:
            await summarize_workspace_item(supabase, _ocr_item_id)
        except Exception:
            logger.warning("inline summarize failed for %s", _ocr_item_id, exc_info=True)

    async for ev in _route(
        question=question,
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
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
    assistant_message_id: str | None = None,
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
            # The router no longer paraphrases the query; the specialist gets
            # the user's raw message (plus recent_messages for context).
            describe_query=question,
            task_label=result.task_label,
            target_item_id=result.target_item_id,
            attached_item_ids=list(result.attached_item_ids),
            subtype=result.subtype,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
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
    assistant_message_id: str | None = None,
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

    # One logical run id for this dispatch. Allocated up front so every LLM call
    # in the run — and, if it pauses, the paused_runs row + the resume leg —
    # share it on their llm_calls rows (migration 061). bind_run_id tags rows
    # nested in this block; pass the same id to _record_deferred on pause.
    run_id = str(uuid.uuid4())

    # PII note: user_id intentionally NOT on this span. The monitor recovers
    # user_id via Supabase join on conversation_id — every persisted row
    # (messages, conversations, workspace_items, llm_calls) carries user_id as a
    # column. Keeping user_id off Logfire spans narrows the PII surface area.
    with bind_run_id(run_id), _logfire.span(
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
                ds_outcome = await _run_deep_search(
                    major_input, supabase,
                    assistant_message_id=assistant_message_id,
                )
                if ds_outcome.kind == "paused":
                    # The pause-triggering decider's cost is emitted by
                    # handle_planner_turn (phase-1 pause path → deep_search.planner).
                    # Persist the pause row + question message, then skip the
                    # normal completed path (the run stays alive). Pass run_id so
                    # the paused_runs row + resume leg share this run's id.
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
                        run_id=run_id,
                    )
                    raise _SkipRunRecord()
                run_result = ds_outcome.result
            elif agent_family == "writing":
                # The writer_planner owns the loop: it may pause for ask_user
                # ('clarify') or present_plan_for_approval ('approve_plan'),
                # then on resume drives writing_executor + publish.
                wp_outcome = await _run_writer(
                    major_input, subtype, supabase,
                    assistant_message_id=assistant_message_id,
                )
                if wp_outcome.kind == "paused":
                    # The writer_planner decider self-captures via the tracking
                    # hook (track_stage.record_run → llm_calls), so no explicit
                    # feed here (that would double-count).
                    # Persist the pause row with pause_reason (migration 053).
                    # Same flow as the deep_search branch above; pass run_id so
                    # the resume leg shares this run's id.
                    _record_deferred(
                        supabase=supabase,
                        planner_result=wp_outcome.planner_result,
                        planner_output=wp_outcome.deferred,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        case_id=case_id,
                        user_message_id=user_message_id,
                        agent_family=agent_family,
                        describe_query=describe_query,
                        task_label=task_label,
                        pause_reason=wp_outcome.pause_reason or "clarify",
                        run_id=run_id,
                    )
                    raise _SkipRunRecord()
                run_result = wp_outcome.result
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
            # pause_reason (migration 053) tells the frontend whether to render
            # a plain question ('clarify') or a plan_md block with inline
            # approve/reject affordances ('approve_plan').
            deferred_row = _find_awaiting_user(supabase, conversation_id, user_id)
            if deferred_row:
                yield {
                    "type": "agent_question",
                    "run_id": str(deferred_row.get("run_id", "")),
                    "question": deferred_row.get("question_text", ""),
                    "pause_reason": deferred_row.get("pause_reason", "clarify"),
                }
            yield {"type": "done", "usage": _zero_usage("paused")}
            return  # run stays alive (paused_runs row written); just emit SSE

        except asyncio.CancelledError:
            # Convo-1 forensics bug #2: SSE consumer / Railway gateway / explicit
            # client disconnect cancels the pipeline task mid-run. The Exception
            # handler below does NOT catch CancelledError (it's BaseException in
            # py 3.8+), so without this branch the `finally` block records
            # status='ok' — silently lying about what happened. Set status here
            # then re-raise per the asyncio contract; the finally block writes
            # the row honestly. Partial-token capture from in-flight executor
            # stats is deferred (run_result is None on cancel — see note in
            # convo_1_report/SYNTHESIS.md §4 bug #2).
            logger.info(
                "specialist %s cancelled mid-run (SSE disconnect, gateway, or stop)",
                agent_family,
            )
            status = "cancelled"
            err_payload = {
                "type": "CancelledError",
                "message": "pipeline cancelled mid-run",
            }
            raise

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
            # deep_search cost self-emits per stage inside deep_search_v4
            # (expansion/reranker per executor, sector_picker, aggregator) and in
            # planner/runner.py (planner) — all inside this capture scope. Every
            # other family is captured per-call by the tracking hook. Nothing to
            # feed here.
            # No completed-run DB row is written anymore: cost lives in llm_calls
            # and nothing reads completed agent_runs rows. The run's outcome still
            # matters for forensics, so stamp it onto the dispatch Logfire span.
            try:
                _dispatch_span.set_attributes({
                    "dispatch.status": status,
                    "dispatch.duration_ms": int((perf_counter() - t0) * 1000),
                    "dispatch.error": (err_payload or {}).get("message") if err_payload else None,
                    "dispatch.output_item_id": getattr(run_result, "output_item_id", None),
                })
            except Exception:
                pass

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
    *,
    assistant_message_id: str | None = None,
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

    # Migration 052 / agent communication protocol: build the seq → UUID
    # alias map the planner's referenced_wi resolver and unfold_workspace_item
    # tool consult. Includes every WI the planner could see in prompts —
    # prior searches AND attached items.
    wi_alias_map: dict[int, str] = {}
    for prior in prior_searches:
        if prior.wi_seq is not None and prior.item_id:
            wi_alias_map[int(prior.wi_seq)] = prior.item_id
    for snap in input.attached_items:
        if snap.wi_seq is not None and snap.item_id:
            wi_alias_map[int(snap.wi_seq)] = snap.item_id

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
            wi_alias_map=wi_alias_map,
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
        # The planner's faithful, zero-bias restatement (when produced) is the
        # canonical query for this turn — prefer it over the raw user message
        # for the artifact's stored query so prior_searches comprehension in
        # future turns reflects the corrected intent. Falls back to the raw
        # query when the planner left it empty (query already clean).
        effective_describe_query = (
            getattr(turn.decision, "query_restatement", "") or ""
        ).strip() or input.describe_query
        if should_publish:
            try:
                publish_input = SearchPublishInput(
                    user_id=input.user_id,
                    conversation_id=input.conversation_id,
                    case_id=input.case_id,
                    message_id=assistant_message_id,
                    agg_output=agg_output,
                    original_query=effective_describe_query,
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
                    describe_query=effective_describe_query,
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

    # Aggregate token usage from per-executor stats. Every phase that runs
    # an LLM populates ``deps._per_executor_stats[<phase>]`` with a
    # ``per_tier`` breakdown — covered phases (Wave-N cost-ledger fix):
    #   - ``reg_search`` / ``compliance_search`` / ``case_search`` (expander
    #     tier_1 + reranker tier_2; written by each loop)
    #   - ``planner`` (decider + responder, both tier_1; written by
    #     ``_finalize_planner_stats`` in ``deep_search_v4/planner/runner.py``)
    #   - ``aggregator`` (tier_1; written at the end of
    #     ``deep_search_v4/orchestrator.run_full_loop``)
    # ``estimate_run_cost`` walks ``per_tier`` first → prices each phase at
    # its real tier. Without this aggregation the planner-decider /
    # planner-responder / aggregator LLM calls were silently uncounted,
    # producing the ~10× under-count observed in conv accbc49c.
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
    assistant_message_id: str | None = None,
    *,
    message_history: Any = None,
    deferred_results: Any = None,
):
    """Run the writer_planner → writing_executor pipeline.

    Replaces the legacy direct-writer invocation per
    ``.claude/plans/writer_planner.md`` § Orchestrator wiring change. The
    writer_planner (Layer-2 Major) owns the loop: it may pause for
    ``ask_user`` or ``present_plan_for_approval``, then on resume drives
    the writing_executor (Layer-3 Task) and publishes the workspace_item.

    ``assistant_message_id`` is the assistant-message FK that the publisher
    stamps onto ``workspace_items.message_id`` so the chat ↔ artifact
    linkage is queryable. Threaded through from message_service →
    handle_message → _dispatch → here → handle_writer_planner_turn →
    publish_input.

    ``message_history`` + ``deferred_results`` are set ONLY on resume (see
    ``_resume_major_agent_inner``): the rehydrated pydantic-ai
    ``list[ModelMessage]`` and the ``DeferredToolResults({tool_call_id:
    user_reply})``. On fresh dispatch both are ``None`` and the planner runs
    from the user prompt. Forwarded verbatim to ``handle_writer_planner_turn``
    (the writer_planner runner is resume-ready — it branches on whether
    ``message_history`` is set).

    Returns:
        ``WriterPlannerTurnResult`` (see ``agents.writer_planner.runner``).
        Callers must branch on ``.kind``:
          - ``'completed'`` → ``.result`` is the SpecialistResult to stream.
          - ``'paused'``    → ``.planner_result`` + ``.deferred`` + ``.pause_reason``
            feed ``_record_deferred``; the run stays alive.

    ``subtype`` is forwarded as a hint but the planner derives the final
    subtype from the user's intent — the router's hint is advisory only.
    """
    from agents.writer_planner import handle_writer_planner_turn

    return await handle_writer_planner_turn(
        major_input=input,
        supabase=supabase,
        subtype_hint=subtype,
        assistant_message_id=assistant_message_id,
        message_history=message_history,
        deferred_results=deferred_results,
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


