"""Deep search planner agent -- multi-source Saudi legal research."""
from .agent import planner_agent, create_planner_agent, PLANNER_LIMITS, Citation
from .deps import SearchDeps, build_search_deps
from .runner import handle_deep_search_turn

# Register tools on the default module-level agent.
from .tools import register_tools as _register_tools

_register_tools(planner_agent)

__all__ = [
    "SearchDeps",
    "planner_agent",
    "create_planner_agent",
    "PLANNER_LIMITS",
    "Citation",
    "build_search_deps",
    "handle_deep_search_turn",
    "_register_tools",
]
