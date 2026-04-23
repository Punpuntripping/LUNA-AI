"""Aggregator / synthesizer agent — consumes reranker output, produces cited Arabic synthesis.

Assumes reranker has already ranked + deduplicated results. This agent's sole job is
final synthesis with numbered inline citations and a reference list.

Input: list of RerankerQueryResult (one per sub-query) + original user query
Output: AggregatorOutput with synthesis_md (inline (N) citations) + references list + artifact

Primary model: qwen3.6-plus
Fallback model: gemini-3-flash
"""
from __future__ import annotations

from .models import (
    AggregatorInput,
    AggregatorOutput,
    Reference,
)
from .deps import AggregatorDeps, build_aggregator_deps
from .prompts import AGGREGATOR_PROMPTS, DEFAULT_AGGREGATOR_PROMPT, get_aggregator_prompt

# Optional imports — agent.py / runner.py may not yet exist while the package
# is under active construction. Import lazily so preprocessor + models remain
# usable (and testable) independently.
try:
    from .agent import create_aggregator_agent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - construction-time only
    create_aggregator_agent = None  # type: ignore[assignment]
try:
    from .runner import handle_aggregator_turn  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - construction-time only
    handle_aggregator_turn = None  # type: ignore[assignment]

__all__ = [
    "AggregatorInput",
    "AggregatorOutput",
    "Reference",
    "AggregatorDeps",
    "build_aggregator_deps",
    "AGGREGATOR_PROMPTS",
    "DEFAULT_AGGREGATOR_PROMPT",
    "get_aggregator_prompt",
    "create_aggregator_agent",
    "handle_aggregator_turn",
]
