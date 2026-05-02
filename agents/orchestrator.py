"""Task orchestrator — routes messages to router or pinned task agent."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from supabase import Client as SupabaseClient

from agents.models import ChatResponse, OpenTask, TaskContinue, TaskEnd
from agents.state import (
    TaskInfo, get_active_task, create_task,
    update_task_history, update_task_artifact, complete_task,
)
from agents.base.artifact import create_agent_artifact
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# Map task_type → mock agent function (for non-pydantic-ai agents)
_MOCK_AGENTS = {
    "end_services": None,
    "extraction": None,
}

# Map task_type → artifact type
_ARTIFACT_TYPES = {
    "deep_search": "report",
    "end_services": "contract",
    "extraction": "summary",
    "writing": "memo",
}

# Task types that use real Pydantic AI agents (not mock functions)
_PYDANTIC_AI_AGENTS = {"deep_search", "writing"}


def _inject_task_summary(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    task: TaskInfo,
    summary: str,
) -> None:
    """Persist task summary as an assistant message so the router sees it in history."""
    summary_text = f"[TASK COMPLETED — {task.task_type}]\n{summary}"
    if task.artifact_id:
        summary_text += f"\nArtifact: {task.artifact_id}"
    try:
        supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "assistant",
            "content": summary_text,
        }).execute()
    except Exception as e:
        logger.warning("Error injecting task summary: %s", e)


def _get_mock_agent(task_type: str):
    """Return mock agent function for a task type, or None if not implemented."""
    return _MOCK_AGENTS.get(task_type)


async def handle_message(
    question: str,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    explicit_task_type: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Main entry point — replaces route_and_execute().

    1. Check for active task on this conversation
    2. If active task → send message to pinned task agent
    3. If no active task → send message to router
       a. ChatResponse → yield response tokens
       b. OpenTask → create task, pin agent, run first turn
    """
    # Check for active task
    active_task = get_active_task(supabase, conversation_id)

    if active_task:
        # Pinned task agent handles the message
        async for event in _run_task(question, active_task, supabase, user_id, conversation_id, case_id):
            yield event
    elif explicit_task_type:
        # User explicitly chose a task type — skip router
        async for event in _open_task_explicit(question, explicit_task_type, supabase, user_id, conversation_id, case_id):
            yield event
    else:
        # No active task — route through router
        async for event in _route(question, supabase, user_id, conversation_id, case_id):
            yield event


async def _route(
    question: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
) -> AsyncGenerator[dict, None]:
    """Run router agent, handle ChatResponse or OpenTask."""
    from agents.router.router import run_router
    from agents.utils.history import messages_to_history

    # 1. Load conversation messages → Pydantic AI history
    msg_rows = (
        supabase.table("messages")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .execute()
    ).data or []
    message_history = messages_to_history(msg_rows)

    # 2. Load case context if case_id present
    case_memory_md = None
    case_metadata = None
    if case_id:
        case_row = (
            supabase.table("lawyer_cases")
            .select("case_name, case_type, status, parties, description")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if case_row and case_row.data:
            case_metadata = case_row.data

        memories = (
            supabase.table("case_memories")
            .select("content")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        ).data or []

        if case_metadata or memories:
            parts = []
            if case_metadata:
                parts.append(f"### معلومات القضية\n\n**اسم القضية:** {case_metadata.get('case_name', '')}\n**نوع القضية:** {case_metadata.get('case_type', '')}")
            if memories:
                parts.append("### الوقائع والمعلومات المحفوظة\n\n" + "\n".join(f"- {m['content']}" for m in memories))
            case_memory_md = "\n\n".join(parts)

    # 3. Load user preferences
    prefs_row = (
        supabase.table("user_preferences")
        .select("preferences")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    user_preferences = prefs_row.data.get("preferences") if prefs_row and prefs_row.data else None

    # 4. Call real router
    result = await run_router(
        question=question,
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        message_history=message_history,
    )

    if isinstance(result, ChatResponse):
        yield {"type": "agent_selected", "agent_family": "router"}

        # Fake-stream word-by-word
        words = result.message.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.03)

        yield {
            "type": "done",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(result.message), "model": "gemini-3-flash"},
        }

    elif isinstance(result, OpenTask):
        async for event in _open_task(
            result.task_type, result.briefing, result.artifact_id,
            supabase, user_id, conversation_id, case_id,
            subtype=result.subtype,
        ):
            yield event


async def _open_task(
    task_type: str,
    briefing: str,
    artifact_id: str | None,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    subtype: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Create task, pin agent, run first turn.

    ``subtype`` is forwarded to the task runner only; not persisted on
    task_state (no column for it pre-Wave 8A). For ``writing`` tasks the
    runner uses it to drive WriterInput.subtype.
    """
    # Create task_state row
    task = create_task(
        supabase,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_family=task_type,
        briefing=briefing,
        artifact_id=artifact_id,
    )

    # Yield agent_selected + task_started
    yield {"type": "agent_selected", "agent_family": task_type}
    yield {"type": "task_started", "task_id": task.task_id, "task_type": task_type}

    # Run first turn
    async for event in _run_task(
        briefing, task, supabase, user_id, conversation_id, case_id,
        subtype=subtype,
    ):
        yield event


async def _open_task_explicit(
    question: str,
    task_type: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
) -> AsyncGenerator[dict, None]:
    """User explicitly chose a task type — generate briefing from question, then open."""
    briefing = f"User request: {question}"
    async for event in _open_task(
        task_type, briefing, None, supabase, user_id, conversation_id, case_id,
        subtype=None,
    ):
        yield event


async def _run_task(
    question: str,
    task: TaskInfo,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    subtype: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Send message to pinned task agent — dispatches to Pydantic AI or mock."""
    with _logfire.span(
        "task.run",
        task_id=task.task_id,
        task_type=task.task_type,
        agent_family=task.agent_family,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        is_first_turn=len(task.history) == 0,
        artifact_id=task.artifact_id,
        subtype=subtype,
    ):
        if task.task_type in _PYDANTIC_AI_AGENTS:
            async for event in _run_pydantic_ai_task(
                question, task, supabase, user_id, conversation_id, case_id,
                subtype=subtype,
            ):
                yield event
            return

        # ── Mock path for end_services, extraction ──
        agent_fn = _get_mock_agent(task.task_type)
        if agent_fn is None:
            logger.error("No agent for task type: %s", task.task_type)
            yield {"type": "token", "text": "حدث خطأ: نوع المهمة غير معروف"}
            yield {"type": "done", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "error"}}
            return

        is_first_turn = len(task.history) == 0
        result = agent_fn(question, task.current_artifact, is_first_turn)

        if isinstance(result, TaskContinue):
            # Stream response tokens
            words = result.response.split(" ")
            for i, word in enumerate(words):
                token = word if i == 0 else f" {word}"
                yield {"type": "token", "text": token}
                await asyncio.sleep(0.03)

            # Update artifact
            task.current_artifact = result.artifact

            # Create or update artifact in DB
            if not task.artifact_id:
                # Create new artifact
                artifact_type = _ARTIFACT_TYPES.get(task.task_type, "report")
                title = f"مهمة {task.task_type}: {question[:40]}"
                try:
                    artifact = await create_agent_artifact(
                        supabase,
                        user_id,
                        conversation_id,
                        case_id,
                        agent_family=task.agent_family,
                        artifact_type=artifact_type,
                        title=title,
                        content_md=result.artifact,
                        is_editable=True,
                    )
                    task.artifact_id = artifact["artifact_id"]
                    update_task_artifact(supabase, task.task_id, task.artifact_id)
                    yield {
                        "type": "workspace_item_created",
                        "item_id": task.artifact_id,
                        "kind": "agent_writing",
                        "subtype": artifact_type,
                        "title": title,
                        "created_by": "agent",
                    }
                except Exception as e:
                    logger.warning("Error creating artifact for task %s: %s", task.task_id, e)
            else:
                # Update existing workspace item
                try:
                    supabase.table("workspace_items").update(
                        {"content_md": result.artifact}
                    ).eq("item_id", task.artifact_id).execute()
                    yield {
                        "type": "workspace_item_updated",
                        "item_id": task.artifact_id,
                    }
                except Exception as e:
                    logger.warning("Error updating artifact for task %s: %s", task.task_id, e)

            # Update task history
            task.history.append({"role": "user", "content": question})
            task.history.append({"role": "assistant", "content": result.response})
            update_task_history(supabase, task.task_id, task.history)

            yield {
                "type": "done",
                "usage": {"prompt_tokens": 500, "completion_tokens": len(result.response), "model": f"mock-{task.task_type}"},
            }

        elif isinstance(result, TaskEnd):
            # Stream final response
            words = result.last_response.split(" ")
            for i, word in enumerate(words):
                token = word if i == 0 else f" {word}"
                yield {"type": "token", "text": token}
                await asyncio.sleep(0.03)

            # Persist final artifact
            if task.artifact_id:
                try:
                    supabase.table("workspace_items").update(
                        {"content_md": result.artifact}
                    ).eq("item_id", task.artifact_id).execute()
                except Exception as e:
                    logger.warning("Error persisting final artifact for task %s: %s", task.task_id, e)

            # Mark task completed
            complete_task(supabase, task.task_id, result.summary, status="completed")

            # Inject task summary into conversation history
            _inject_task_summary(supabase, conversation_id, user_id, task, result.summary)

            _logfire.info(
                "task.ended",
                task_id=task.task_id,
                task_type=task.task_type,
                end_reason=getattr(result, "reason", None),
                artifact_id=task.artifact_id,
            )

            yield {"type": "task_ended", "task_id": task.task_id, "summary": result.summary}

            yield {
                "type": "done",
                "usage": {"prompt_tokens": 500, "completion_tokens": len(result.last_response), "model": f"mock-{task.task_type}"},
            }

            # If out-of-scope, re-route the original question through the router
            if result.reason == "out_of_scope":
                async for event in _route(question, supabase, user_id, conversation_id, case_id):
                    yield event


async def _run_pydantic_ai_task(
    question: str,
    task: TaskInfo,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    subtype: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Run a Pydantic AI task agent (deep_search, writing). Yields SSE events.

    For ``deep_search``:
      - loads the user's ``detail_level`` preference (default ``"medium"``)
      - runs the URA 2.0 pipeline via ``agents.deep_search_v4.orchestrator.run_full_loop``
      - persists the aggregator output to the ``artifacts`` table as
        ``artifact_type='legal_synthesis'``
      - emits an ``artifact_created`` SSE event before streaming the body

    For ``writing``:
      - loads the user's ``detail_level`` preference (default ``"medium"``)
      - assembles a workspace context stopgap from current artifacts rows
        (Wave 8A will replace this with workspace_context.load_workspace_context)
      - runs the agent_writer pipeline; persistence + lock + SSE events
        come out of agent_writer.publish_writer_result
    """
    if task.task_type == "deep_search":
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
            detail_level = get_detail_level(supabase, user_id)
        except Exception:
            logger.warning("get_detail_level failed; defaulting to 'medium'", exc_info=True)
            detail_level = "medium"

        # Look up the triggering user message's id for foreign-key wire-up on
        # both artifacts and retrieval_artifacts. The user message is always
        # persisted BEFORE the agent runs (CLAUDE.md rule #7), so the most
        # recent user row on this conversation IS this turn's user message.
        # TODO cleanup: thread message_id through send_message_stream →
        # handle_message → _run_pydantic_ai_task so we can drop this query.
        user_message_id: str | None = None
        try:
            row = (
                supabase.table("messages")
                .select("message_id")
                .eq("conversation_id", conversation_id)
                .eq("role", "user")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if row and row.data:
                user_message_id = row.data[0].get("message_id")
        except Exception:
            logger.warning("deep_search: could not resolve user_message_id", exc_info=True)

        settings = get_settings()
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            deps = FullLoopDeps(
                supabase=supabase,
                embedding_fn=embed_regulation_query_alibaba,
                jina_api_key=settings.JINA_RERANKER_API_KEY or "",
                http_client=http_client,
                detail_level=detail_level,
                enable_planner=False,
            )

            agg_output = await run_full_loop(
                query=question,
                query_id=0,
                deps=deps,
            )

            # Forward any accumulated SSE events first
            for event in deps._events:
                yield event

            # Persist the aggregator output via the agent_search publishing
            # adapter. Wraps create_artifact + retrieval_artifacts +
            # reranker_runs writes in one place; emits BOTH the new
            # workspace_item_created event and the legacy artifact_created
            # alias (Wave 8B drops the alias once the frontend rename lands).
            try:
                publish_input = SearchPublishInput(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    case_id=case_id,
                    message_id=user_message_id,
                    agg_output=agg_output,
                    original_query=question,
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
                for event in publish_result.sse_events:
                    yield event
            except Exception as exc:
                logger.warning("deep_search artifact persist failed: %s", exc, exc_info=True)

            yield {"type": "token", "text": agg_output.synthesis_md}
            yield {
                "type": "done",
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "model": agg_output.model_used or "deep_search_v3",
                },
            }
    elif task.task_type == "writing":
        import httpx

        from agents.agent_writer import (
            WorkspaceContextBlock,
            WriterInput,
            build_writer_deps,
            handle_writer_turn,
        )
        from backend.app.services.preferences_service import get_detail_level
        from backend.app.services.workspace_context import load_workspace_context

        # 1. Detail level (same pattern as deep_search).
        try:
            detail_level = get_detail_level(supabase, user_id)
        except Exception:
            logger.warning("get_detail_level failed; defaulting to 'medium'", exc_info=True)
            detail_level = "medium"

        # 2. Workspace context. Wave 8A: load all visible workspace_items
        # for this conversation via the dedicated helper. The helper falls
        # back to the pre-migration ``artifacts`` table automatically, so
        # this code path works both before and after migration 026.
        # research_items get the search-like agent_outputs (the writer
        # uses them to ground its draft on prior research). Note/
        # attachment/convo_context flow into WorkspaceContextBlock.
        ws_ctx = await load_workspace_context(supabase, conversation_id)
        # Partition agent_outputs: search-like outputs (agent_search and
        # subtype='legal_synthesis') become research_items the writer
        # cites; agent_writing outputs are NOT fed back as research --
        # the user uses revising_item_id explicitly to revise a draft.
        research_items: list[dict] = []
        for item in ws_ctx.get("agent_outputs", []) or []:
            subtype = (item.get("subtype") or "").lower()
            if subtype in {"legal_synthesis", "agent_search", "report"}:
                research_items.append({
                    "item_id": item.get("item_id"),
                    "title": item.get("title", ""),
                    "content_md": item.get("content_md", ""),
                    "metadata": {"subtype": item.get("subtype")},
                })

        # 3. Resolve the user message id for foreign-key wire-up (same trick
        # used by deep_search above).
        user_message_id: str | None = None
        try:
            row = (
                supabase.table("messages")
                .select("message_id")
                .eq("conversation_id", conversation_id)
                .eq("role", "user")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if row and row.data:
                user_message_id = row.data[0].get("message_id")
        except Exception:
            logger.warning("writing: could not resolve user_message_id", exc_info=True)

        # 4. Build WriterInput. Subtype defaults to "memo" if router didn't
        # supply one; this matches WriterInput.subtype's default but we keep
        # the assignment explicit so the orchestrator's intent is obvious.
        chosen_subtype = subtype or "memo"
        try:
            writer_input = WriterInput(
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                message_id=user_message_id,
                user_request=question,
                subtype=chosen_subtype,  # type: ignore[arg-type]
                research_items=research_items,
                workspace_context=WorkspaceContextBlock(
                    notes=list(ws_ctx.get("notes", []) or []),
                    attachments=list(ws_ctx.get("attachments", []) or []),
                    convo_context=ws_ctx.get("convo_context"),
                ),
                revising_item_id=task.artifact_id,
                detail_level=detail_level,
                tone="formal",
            )

            async with httpx.AsyncClient(timeout=60.0) as http_client:
                writer_deps = build_writer_deps(
                    supabase=supabase,
                    http_client=http_client,
                )

                writer_output = await handle_writer_turn(writer_input, writer_deps)

            # 5. Forward SSE events accumulated by the publisher
            # (workspace_item_created / locked / unlocked + legacy
            # artifact_created alias).
            for event in writer_output.sse_events:
                yield event

            # 6. Stream the body so the chat shows the assistant text.
            yield {"type": "token", "text": writer_output.content_md}
            yield {
                "type": "done",
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": len(writer_output.content_md or ""),
                    "model": writer_output.metadata.get("model_used", "agent_writer"),
                },
            }
        except Exception as exc:
            logger.error("writing task failed: %s", exc, exc_info=True)
            yield {
                "type": "token",
                "text": "عذراً، تعذّر إنشاء المسوّدة. يرجى المحاولة مرة أخرى.",
            }
            yield {
                "type": "done",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "error"},
            }
    else:
        logger.error("Unknown Pydantic AI task type: %s", task.task_type)
        yield {"type": "token", "text": "حدث خطأ: نوع المهمة غير معروف"}
        yield {"type": "done", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "error"}}
