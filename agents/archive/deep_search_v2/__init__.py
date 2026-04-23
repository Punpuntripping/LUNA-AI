"""Deep search V2 (revised) -- hierarchical supervisor pattern.

PlanAgent (pydantic_ai Agent with tools) above a reusable Search Loop
(pydantic_graph with ExpanderNode, SearchNode, AggregateNode, ReportNode).
"""
from .graph import build_search_deps, handle_deep_search_turn
from .models import DeepSearchDeps

__all__ = [
    "DeepSearchDeps",
    "build_search_deps",
    "handle_deep_search_turn",
]
