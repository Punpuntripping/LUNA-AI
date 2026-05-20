"""Memory family — best-effort summarizers and conversation compactors.

Two distinct entry points:

- ``compact_conversation`` / ``resummarize_dirty_items`` — long-running
  conversation memory hooks (see ``agents/memory/agent.py``).
- ``run_artifact_summary`` — per-artifact agent-facing summary written to
  ``workspace_items.summary`` right after a publisher returns (see
  ``agents/memory/artifact_summarizer``).
"""
from __future__ import annotations

from .artifact_summarizer import (
    ArtifactSummaryDeps,
    ArtifactSummaryInput,
    ArtifactSummaryOutput,
    build_artifact_summary_deps,
    handle_artifact_summary_turn,
    run_artifact_summary,
)

__all__ = [
    "ArtifactSummaryDeps",
    "ArtifactSummaryInput",
    "ArtifactSummaryOutput",
    "build_artifact_summary_deps",
    "handle_artifact_summary_turn",
    "run_artifact_summary",
]
