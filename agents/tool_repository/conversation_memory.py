"""update_conversation_memory: the router's write access to the hidden memory.

Spec: ``.claude/plans/conversation_memory.md`` §4.4. The logic lives in
:func:`agents.utils.conversation_memory.apply_memory_ops`; this module is the
model-facing tool.

Design notes:
  * **Router only.** Registered on ``router_agent`` and nowhere else (D1).
  * **Failure never blocks the turn.** Unlike ``save_memo``, a DB error comes
    back as a plain string, never ``ModelRetry``: exhausted retries would replace
    the user's answer with the router's error fallback, and the correction is
    still in the chat history. The argument types are loose for the same reason.
    A wrong ``kind`` reaches ``apply_memory_ops``, which answers with a plain
    message, where a strict schema would spend a tool retry on it.
  * **Sync body, on purpose.** Pydantic AI runs a response's tool calls
    concurrently. With no ``await`` between reading ``deps.conversation_memory``
    and storing the result, two calls in one response cannot interleave.
  * **Same-turn update.** The tool replaces ``deps.conversation_memory``;
    ``run_router`` re-renders it into ``RouterRunResult.conversation_memory_md``,
    so a specialist dispatched in the same response reads the new entry.
  * **Hidden when off.** The ``prepare`` hook drops the tool from the model's
    tool list while ``CONVERSATION_MEMORY_ENABLED`` is false.
  * **A refusal is recorded on deps.** With ``end_strategy="exhaustive"`` a run
    ends once the output tool is valid, so a call batched with the final output
    returns to nobody. ``memory_fixable_calls`` is how ``run_router`` learns that
    a "fix this" went unread, and it then runs one repair restricted to this
    tool: :data:`update_conversation_memory_tool` is that restricted form.
  * **Telemetry.** Our own events and logs carry counts only. LLM spans carry
    the prompt, memory text included, as for every prompt:
    ``instrument_pydantic_ai()`` records content, and ``/convo-monitor`` depends
    on that.

Registration::

    from agents.tool_repository.conversation_memory import (
        register_update_conversation_memory,
    )
    register_update_conversation_memory(agent)  # deps: HasConversationMemory
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic_ai import Agent, RunContext
from pydantic_ai.tools import Tool, ToolDefinition
from typing_extensions import TypedDict  # pydantic refuses typing.TypedDict below 3.12

from agents.utils.conversation_memory import apply_memory_ops, memory_enabled
from shared.observability import get_logfire

_logfire = get_logfire()


class MemoryAdd(TypedDict, total=False):
    """A new memory entry."""

    kind: str
    content: str
    insisted: bool


class MemoryUpdate(TypedDict, total=False):
    """A change to an existing entry: ``id`` plus only the fields that change."""

    id: str
    kind: str
    content: str
    insisted: bool


@runtime_checkable
class HasConversationMemory(Protocol):
    """Structural deps contract for ``update_conversation_memory``.

    ``RouterDeps`` satisfies this. ``conversation_memory`` holds ENCODED entries
    and is replaced by the tool on every call. ``memory_fixable_calls`` collects
    the tool_call_ids of calls that validation refused.
    """

    supabase: object
    user_id: str
    conversation_id: str
    conversation_memory: list
    memory_fixable_calls: set


async def _visible_when_enabled(
    _ctx: RunContext[Any], tool_def: ToolDefinition
) -> ToolDefinition | None:
    return tool_def if memory_enabled() else None


async def update_conversation_memory(  # noqa: RUF029 — see "Sync body" above
    ctx: RunContext[HasConversationMemory],
    add: list[MemoryAdd] | None = None,
    update: list[MemoryUpdate] | None = None,
    remove: list[str] | None = None,
) -> str:
    """Add, correct or remove entries in this conversation's memory.

    The memory holds what must not be forgotten in this conversation, and
    every agent reads it. Your instructions say what belongs in it. One call
    may combine all three arguments; pass only the ones you need.

    Args:
        add: New entries. Each has `kind` and `content`. `kind` is "request"
            (what the user wants done, or the direction of the matter),
            "fact" (a name, amount, date, event, or what a document really
            says) or "legal_position" (the user's statement about the law).
            `content` is the entry written to stand alone, at most 1,500
            characters. Split a mixed statement into two entries.
            `insisted` is optional and true only on a legal_position the user
            repeated after a reply warned them it conflicts with the sources.
        update: Corrections to existing entries. Each has `id` (such as
            "M-2") plus only the fields that change: `content`, `kind`,
            `insisted`. Prefer this to adding a near-duplicate.
        remove: Ids of entries to delete, such as ["M-3"].

    Returns:
        One line saying what was saved. If it asks you to fix something, fix
        it and call again. If it says a database error stopped the save, do
        not retry; continue your reply.
    """
    result = apply_memory_ops(
        ctx.deps.supabase,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        entries=ctx.deps.conversation_memory,
        add=add,
        update=update,
        remove=remove,
    )
    ctx.deps.conversation_memory = result.entries
    if result.fixable:
        ctx.deps.memory_fixable_calls.add(ctx.tool_call_id)
    _logfire.info(
        "router.conversation_memory_update",
        conversation_id=ctx.deps.conversation_id,
        added=result.added,
        updated=result.updated,
        removed=result.removed,
        failed=result.failed,
        entry_count=len(result.entries),
    )
    return result.summary


#: The same tool as an object, for a run restricted to it: ``router_agent.override``
#: takes tools, not names. Hidden by the same hook.
update_conversation_memory_tool = Tool(
    update_conversation_memory, takes_ctx=True, prepare=_visible_when_enabled
)


def register_update_conversation_memory(agent: Agent) -> None:
    """Register ``update_conversation_memory`` on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasConversationMemory`.
    """
    agent.tool(prepare=_visible_when_enabled)(update_conversation_memory)


__all__ = [
    "HasConversationMemory",
    "MemoryAdd",
    "MemoryUpdate",
    "register_update_conversation_memory",
    "update_conversation_memory",
    "update_conversation_memory_tool",
]
