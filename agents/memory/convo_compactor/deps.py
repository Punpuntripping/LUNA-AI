"""Dependencies for the convo_compactor runner.

LLM-only: the agent's run is pure text-in / text-out — loading the messages,
the workspace items and the prior summary is the caller's job
(``agents/memory/agent.py``), and persisting the result is too. The optional
``logger`` mirrors the deep_search per-run logger pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CompactionDeps:
    """Injected deps. The single field is optional — pass nothing for a
    no-frills run."""

    logger: Any | None = None


def build_compactor_deps(logger: Any | None = None) -> CompactionDeps:
    return CompactionDeps(logger=logger)
