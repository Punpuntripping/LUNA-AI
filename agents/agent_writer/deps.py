"""Dependencies for agent_writer.

Model selection:
- Primary: qwen3.6-plus (Alibaba DashScope) -- same family as the aggregator
- Fallback: gemini-3-flash (Google)

Override via env: LUNA_WRITER_PRIMARY_MODEL, LUNA_WRITER_FALLBACK_MODEL.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from agents.models import WorkspaceItemSnapshot


PRIMARY_MODEL_DEFAULT = "qwen3.6-plus"
FALLBACK_MODEL_DEFAULT = "gemini-3-flash"
LOCK_TTL_SECONDS_DEFAULT = 30            # matches Wave 8 plan section "Agent lock during streaming"
LOCK_HEARTBEAT_SECONDS_DEFAULT = 10
TEMPERATURE_DEFAULT = 0.4                # higher than aggregator (0.2) -- generation, not grounding


@dataclass
class WriterDeps:
    """Dependencies injected into the writer runner.

    The agent itself is a single LLM call (pydantic_ai.Agent.run) -- no tools.
    Lock acquisition + release happen in the publisher, not the agent.

    Fields:
        supabase: Sync supabase-py client (project pattern: sync client used
            inside async coroutines). Optional so unit tests can stub it.
        model_registry: Module reference to agents.model_registry; injected
            so tests can swap the registry without monkey-patching globals.
            Defaults to the production module on first access.
        http_client: Optional aiohttp/httpx client for any external HTTP the
            agent or its tools might want. The current Cut-1 agent does not
            make HTTP calls itself; field reserved for parity with other agents.
        logger: Module-scoped logger by default; tests can inject silent loggers.

        briefing: The user's task statement forwarded from MajorAgentInput.
            Rendered into the dynamic system prompt by inject_workspace_context.
        attached_items: Router-selected workspace items (full content_md
            hydrated by the orchestrator).  Rendered into the dynamic system
            prompt.  When revising_item_id is set, the revision target MUST be
            the first item here — context.py does not fetch from DB.
        revising_item_id: item_id of the draft being revised; None for fresh
            output.  Triggers a "مسوّدة للمراجعة" header in the context block.
        detail_level: Stylistic hint passed through from user preferences.
            One of "low" | "standard" | "medium" | "high".
        tone: Stylistic hint. One of "formal" | "neutral" | "concise".
    """

    supabase: Any = None
    model_registry: Any = None
    http_client: Any = None
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("agents.agent_writer")
    )
    primary_model: str = PRIMARY_MODEL_DEFAULT
    fallback_model: str = FALLBACK_MODEL_DEFAULT
    temperature: float = TEMPERATURE_DEFAULT
    lock_ttl_seconds: int = LOCK_TTL_SECONDS_DEFAULT
    lock_heartbeat_seconds: int = LOCK_HEARTBEAT_SECONDS_DEFAULT
    emit_sse: Optional[Callable[[dict], None]] = None
    # Workspace context fields — populated from MajorAgentInput by the runner.
    # All default to empty/None so existing callers require no changes.
    briefing: str = ""
    attached_items: list[WorkspaceItemSnapshot] = field(default_factory=list)
    revising_item_id: Optional[str] = None
    detail_level: str = "standard"
    tone: str = "neutral"
    # Mutable run-state ------------------------------------------------------
    _events: list[dict] = field(default_factory=list)


def build_writer_deps(
    supabase: Any = None,
    primary_model: Optional[str] = None,
    fallback_model: Optional[str] = None,
    temperature: Optional[float] = None,
    emit_sse: Optional[Callable[[dict], None]] = None,
    http_client: Any = None,
    model_registry: Any = None,
    logger: Optional[logging.Logger] = None,
    # Workspace context fields (Task 5b) ------------------------------------
    briefing: str = "",
    attached_items: Optional[list[WorkspaceItemSnapshot]] = None,
    revising_item_id: Optional[str] = None,
    detail_level: str = "standard",
    tone: str = "neutral",
) -> WriterDeps:
    """Build deps with env override + kwarg precedence.

    Precedence: kwargs > env > defaults. Mirrors build_aggregator_deps so
    callers can swap one for the other without surprises.

    The workspace context kwargs (briefing, attached_items, revising_item_id,
    detail_level, tone) are all optional with safe defaults so existing call
    sites need no changes.
    """
    return WriterDeps(
        supabase=supabase,
        model_registry=model_registry,
        http_client=http_client,
        logger=logger or logging.getLogger("agents.agent_writer"),
        primary_model=(
            primary_model
            or os.getenv("LUNA_WRITER_PRIMARY_MODEL")
            or PRIMARY_MODEL_DEFAULT
        ),
        fallback_model=(
            fallback_model
            or os.getenv("LUNA_WRITER_FALLBACK_MODEL")
            or FALLBACK_MODEL_DEFAULT
        ),
        temperature=temperature if temperature is not None else TEMPERATURE_DEFAULT,
        emit_sse=emit_sse,
        briefing=briefing,
        attached_items=attached_items if attached_items is not None else [],
        revising_item_id=revising_item_id,
        detail_level=detail_level,
        tone=tone,
    )


__all__ = [
    "WriterDeps",
    "build_writer_deps",
    "PRIMARY_MODEL_DEFAULT",
    "FALLBACK_MODEL_DEFAULT",
    "LOCK_TTL_SECONDS_DEFAULT",
    "LOCK_HEARTBEAT_SECONDS_DEFAULT",
    "TEMPERATURE_DEFAULT",
]
