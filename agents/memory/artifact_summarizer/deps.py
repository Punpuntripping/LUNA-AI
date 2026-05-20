"""Dependencies for the artifact_summarizer runner.

LLM-only: the agent's run is pure text-in / text-out. The optional
``logger`` mirrors the deep_search per-run logger pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ArtifactSummaryDeps:
    """Injected deps. Both fields are optional — pass nothing for a no-frills run."""

    logger: Any | None = None


def build_artifact_summary_deps(logger: Any | None = None) -> ArtifactSummaryDeps:
    return ArtifactSummaryDeps(logger=logger)
