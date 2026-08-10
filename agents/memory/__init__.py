"""Memory family — summarizers, extractors, and the conversation compactor.

Three entry points, all Layer-4 (system-side; none of them talk to the user):

- ``run_convo_compaction`` — folds the oldest span of a long conversation into
  one carry-forward summary (see ``agents/memory/convo_compactor``). Driven by
  ``compact_conversation`` in ``agents/memory/agent.py``, which owns the DB
  side: cutoff pointer, ``convo_context`` insert, fail-closed contract.
- ``run_artifact_summary`` — per-artifact agent-facing summary written to
  ``workspace_items.summary`` right after a publisher returns (see
  ``agents/memory/artifact_summarizer``).
- ``run_ocr_extraction`` — text extraction for freshly uploaded attachments
  (see ``agents/memory/ocr_extractor``).

The conversation hooks themselves — ``compact_conversation`` and
``resummarize_dirty_items`` — are NOT re-exported here; the orchestrator
imports ``agents.memory.agent`` as a module.
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
from .convo_compactor import (
    CompactionInput,
    CompactionOutput,
    build_compactor_deps,
    run_convo_compaction,
)
from .ocr_extractor import run_ocr_extraction

__all__ = [
    "ArtifactSummaryDeps",
    "ArtifactSummaryInput",
    "ArtifactSummaryOutput",
    "CompactionInput",
    "CompactionOutput",
    "build_artifact_summary_deps",
    "build_compactor_deps",
    "handle_artifact_summary_turn",
    "run_artifact_summary",
    "run_convo_compaction",
    "run_ocr_extraction",
]
