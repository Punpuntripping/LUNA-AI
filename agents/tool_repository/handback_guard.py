"""Hide the router's write tools on its talk-only hand-back turn.

When the deep_search planner hands a request back as too general to search,
the router runs a second time on the same turn and may only talk with the user
(``RouterDeps.planner_handback``, see ``orchestrator._route_after_handback``).
Its first run of the turn may already have pinned a memo, and nothing here asked
for an edit, so ``save_memo`` and ``edit_artifact`` are withheld from the model
for that run. Every other run, and every other agent, sees them as before.
"""
from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition


async def hide_on_planner_handback(
    ctx: RunContext[Any], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """``prepare`` hook: drop the tool while ``deps.planner_handback`` is set."""
    if getattr(ctx.deps, "planner_handback", None) is not None:
        return None
    return tool_def


__all__ = ["hide_on_planner_handback"]
