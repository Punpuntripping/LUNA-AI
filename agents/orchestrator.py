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

logger = logging.getLogger(__name__)

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
}

# Task types that use real Pydantic AI agents (not mock functions)
_PYDANTIC_AI_AGENTS = {"deep_search"}


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
) -> AsyncGenerator[dict, None]:
    """Create task, pin agent, run first turn."""
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
    async for event in _run_task(briefing, task, supabase, user_id, conversation_id, case_id):
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
    async for event in _open_task(task_type, briefing, None, supabase, user_id, conversation_id, case_id):
        yield event


async def _run_task(
    question: str,
    task: TaskInfo,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
) -> AsyncGenerator[dict, None]:
    """Send message to pinned task agent — dispatches to Pydantic AI or mock."""
    if task.task_type in _PYDANTIC_AI_AGENTS:
        async for event in _run_pydantic_ai_task(question, task, supabase, user_id, conversation_id, case_id):
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
                    "type": "artifact_created",
                    "artifact_id": task.artifact_id,
                    "artifact_type": artifact_type,
                    "title": title,
                }
            except Exception as e:
                logger.warning("Error creating artifact for task %s: %s", task.task_id, e)
        else:
            # Update existing artifact
            try:
                supabase.table("artifacts").update(
                    {"content_md": result.artifact}
                ).eq("artifact_id", task.artifact_id).execute()
                yield {"type": "artifact_updated", "artifact_id": task.artifact_id}
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
                supabase.table("artifacts").update(
                    {"content_md": result.artifact}
                ).eq("artifact_id", task.artifact_id).execute()
            except Exception as e:
                logger.warning("Error persisting final artifact for task %s: %s", task.task_id, e)

        # Mark task completed
        complete_task(supabase, task.task_id, result.summary, status="completed")

        # Inject task summary into conversation history
        _inject_task_summary(supabase, conversation_id, user_id, task, result.summary)

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
) -> AsyncGenerator[dict, None]:
    """Run a Pydantic AI task agent (deep_search). Yields SSE events."""
    if task.task_type == "deep_search":
        import httpx

        from agents.deep_search_v3.orchestrator import FullLoopDeps, run_full_loop
        from agents.utils.embeddings import embed_regulation_query_alibaba
        from shared.config import get_settings

        settings = get_settings()
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            deps = FullLoopDeps(
                supabase=supabase,
                embedding_fn=embed_regulation_query_alibaba,
                jina_api_key=settings.JINA_RERANKER_API_KEY or "",
                http_client=http_client,
            )

            agg_output = await run_full_loop(
                query=question,
                query_id=0,
                deps=deps,
            )

            for event in deps._events:
                yield event

            yield {"type": "token", "text": agg_output.synthesis_md}
            yield {
                "type": "done",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": agg_output.model_used or "deep_search_v3"},
            }
    else:
        logger.error("Unknown Pydantic AI task type: %s", task.task_type)
        yield {"type": "token", "text": "حدث خطأ: نوع المهمة غير معروف"}
        yield {"type": "done", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "error"}}
        return

    # ── Handle result (shared for all Pydantic AI task agents) ──

    if isinstance(result, TaskContinue):
        # Stream response tokens
        words = result.response.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.03)

        # Update task state
        task.current_artifact = result.artifact
        task.history.append({"role": "user", "content": question})
        task.history.append({"role": "assistant", "content": result.response})
        update_task_history(supabase, task.task_id, task.history)

        yield {
            "type": "done",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(result.response), "model": task.task_type},
        }

    elif isinstance(result, TaskEnd):
        # Stream final response
        words = result.last_response.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.03)

        # Persist final artifact if we have one
        if task.artifact_id and result.artifact:
            try:
                supabase.table("artifacts").update(
                    {"content_md": result.artifact}
                ).eq("artifact_id", task.artifact_id).execute()
            except Exception as e:
                logger.warning("Error persisting final artifact for task %s: %s", task.task_id, e)

        # Mark task completed
        complete_task(supabase, task.task_id, result.summary, status="completed")

        # Inject task summary into conversation history
        _inject_task_summary(supabase, conversation_id, user_id, task, result.summary)

        yield {"type": "task_ended", "task_id": task.task_id, "summary": result.summary}
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(result.last_response), "model": task.task_type},
        }

        # If out-of-scope, re-route through the router
        if result.reason == "out_of_scope":
            async for event in _route(question, supabase, user_id, conversation_id, case_id):
                yield event
