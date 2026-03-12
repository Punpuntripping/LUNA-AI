"""Main agent router — classifies, builds context, dispatches to agent families."""
from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

from supabase import Client as SupabaseClient

from shared.types import AgentFamily, AgentContext
from agents.router.classifier import classify
from agents.base.context import build_agent_context

logger = logging.getLogger(__name__)

# Lazy registry — populated on first call to avoid circular imports
_AGENT_REGISTRY: dict | None = None


def _get_registry() -> dict:
    global _AGENT_REGISTRY
    if _AGENT_REGISTRY is None:
        from agents.simple_search.agent import SimpleSearchAgent
        from agents.deep_search.agent import DeepSearchAgent
        from agents.end_services.agent import EndServicesAgent
        from agents.extraction.agent import ExtractionAgent
        from agents.memory.agent import MemoryAgent

        _AGENT_REGISTRY = {
            AgentFamily.SIMPLE_SEARCH: SimpleSearchAgent(),
            AgentFamily.DEEP_SEARCH: DeepSearchAgent(),
            AgentFamily.END_SERVICES: EndServicesAgent(),
            AgentFamily.EXTRACTION: ExtractionAgent(),
            AgentFamily.MEMORY: MemoryAgent(),
        }
    return _AGENT_REGISTRY


async def route_and_execute(
    question: str,
    context: dict,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient | None = None,
    case_id: str | None = None,
    explicit_agent: str | None = None,
    modifiers: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Main pipeline entry point.

    1. explicit_agent set → use that family (skip classifier)
    2. Else → classify() → pick family
    3. Build AgentContext
    4. If 'reflect' in modifiers → yield from agent.reflect()
    5. If 'plan' in modifiers → yield from agent.plan()
    6. Yield from agent.execute()
    """
    modifiers = modifiers or []

    # 1. Determine agent family
    if explicit_agent:
        try:
            family = AgentFamily(explicit_agent)
        except ValueError:
            logger.warning("Unknown agent family: %s, falling back to simple_search", explicit_agent)
            family = AgentFamily.SIMPLE_SEARCH
    else:
        # Build minimal context for classification
        ctx_for_classify = AgentContext(
            question=question, conversation_id=conversation_id, user_id=user_id
        )
        family = await classify(question, ctx_for_classify)

    # 2. Yield routing event (tells frontend which agent was selected)
    yield {"type": "agent_selected", "agent_family": family.value}

    # 3. Build full AgentContext
    if supabase:
        agent_ctx = await build_agent_context(
            supabase=supabase,
            question=question,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            agent_family=family,
            modifiers=modifiers,
        )
    else:
        agent_ctx = AgentContext(
            question=question,
            conversation_id=conversation_id,
            user_id=user_id,
            case_id=case_id,
            modifiers=modifiers,
        )

    # Attach supabase client for agents that create artifacts
    agent_ctx._supabase = supabase  # type: ignore[attr-defined]

    # 4. Get agent instance
    registry = _get_registry()
    agent = registry[family]

    # 5. Run modifiers first
    if "reflect" in modifiers:
        async for event in agent.reflect(agent_ctx):
            yield event

    if "plan" in modifiers:
        async for event in agent.plan(agent_ctx):
            yield event

    # 6. Run main execution
    async for event in agent.execute(agent_ctx):
        yield event
