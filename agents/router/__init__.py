"""Router agent — classifies intent, answers directly or dispatches a specialist."""

from agents.router.context import RouterContext, load_router_context
from agents.router.router import RouterDeps, RouterRunResult, router_agent, run_router

__all__ = [
    "RouterDeps",
    "RouterRunResult",
    "RouterContext",
    "load_router_context",
    "router_agent",
    "run_router",
]
