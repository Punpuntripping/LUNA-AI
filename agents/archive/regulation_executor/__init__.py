"""Regulation search executor agent -- stateless delegation agent for deep search."""
from .agent import ExecutorResult, regulation_executor, EXECUTOR_LIMITS
from .deps import RegulationSearchDeps
from .runner import run_regulation_search

# Trigger tool registration when tools.py exists.
# tools.py is created by the tool-integrator agent in a later wave.
try:
    from . import tools as _tools  # noqa: F401
except ImportError:
    pass

# Import after tools to avoid circular import
from .tools import run_retrieval_pipeline  # noqa: E402

__all__ = [
    "ExecutorResult",
    "RegulationSearchDeps",
    "regulation_executor",
    "EXECUTOR_LIMITS",
    "run_regulation_search",
    "run_retrieval_pipeline",
]
