"""Bridge module -- re-exports from agents.regulation_executor.

The planner's search_regulations tool imports from this path:
    from agents.deep_search.executors import run_regulation_search, RegulationSearchDeps

This module re-exports from the standalone regulation_executor package
so that import path continues to work.
"""
from agents.regulation_executor import (
    ExecutorResult,
    RegulationSearchDeps,
    run_regulation_search,
)

__all__ = [
    "ExecutorResult",
    "RegulationSearchDeps",
    "run_regulation_search",
]
