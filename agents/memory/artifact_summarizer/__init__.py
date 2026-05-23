"""Artifact summarizer — tier_2 DeepSeek-primary agent that produces an
agent-facing coverage summary for a published workspace item.

Runs at the tail of every artifact-producing pipeline (agent_search, writer,
…). Output is written to ``workspace_items.summary`` so downstream agents
can read it without re-fetching the full ``content_md``.
"""
from __future__ import annotations

from .agent import create_artifact_summarizer, create_attachment_summarizer
from .deps import ArtifactSummaryDeps, build_artifact_summary_deps
from .logger import ArtifactSummaryLogger
from .models import (
    ArtifactSummaryInput,
    ArtifactSummaryLLMOutput,
    ArtifactSummaryOutput,
    AttachmentSummaryInput,
    AttachmentSummaryLLMOutput,
    AttachmentSummaryOutput,
)
from .runner import (
    handle_artifact_summary_turn,
    handle_attachment_summary_turn,
    run_artifact_summary,
    run_attachment_summary,
)

__all__ = [
    "ArtifactSummaryDeps",
    "ArtifactSummaryInput",
    "ArtifactSummaryLLMOutput",
    "ArtifactSummaryLogger",
    "ArtifactSummaryOutput",
    "AttachmentSummaryInput",
    "AttachmentSummaryLLMOutput",
    "AttachmentSummaryOutput",
    "build_artifact_summary_deps",
    "create_artifact_summarizer",
    "create_attachment_summarizer",
    "handle_artifact_summary_turn",
    "handle_attachment_summary_turn",
    "run_artifact_summary",
    "run_attachment_summary",
]
