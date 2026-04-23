"""Turn runner for deep_search_v2 agent.

Thin wrapper that re-exports handle_deep_search_turn from graph.py
for backward compatibility and orchestrator integration.
"""
from __future__ import annotations

from .graph import build_search_deps, handle_deep_search_turn

__all__ = [
    "handle_deep_search_turn",
    "build_search_deps",
]
