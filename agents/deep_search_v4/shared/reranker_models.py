"""Shared Pydantic models for LLM reranker output across all 3 executors.

These models define the *LLM output shape* — what the classification agent
is asked to produce. Domain-specific kept-result models (RerankedResult,
RerankedCaseResult, RerankedServiceResult) live in each executor's models.py
because their field sets differ legitimately across domains.

Divergences preserved by design:
  - reg_search uses all three action values ("keep", "drop", "unfold").
  - case_search + compliance use only "keep" / "drop"; "unfold" is treated
    as "drop" by their reranker loops.
  - weak_axes is optional and meaningful only to compliance_search.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class RerankerDecision(BaseModel):
    """LLM decision for one search result (position-indexed).

    Used by all three domain rerankers.  The ``unfold`` action value is
    only acted upon by reg_search; case and compliance rerankers treat it
    as ``drop`` in their processing loops.
    """

    position: int = Field(
        description="1-based position matching [N] in the result header",
    )
    action: Literal["keep", "drop", "unfold"] = Field(
        description=(
            "keep: relevant, drop: unrelated, "
            "unfold: needs deeper context (reg only — treated as drop by others)"
        ),
    )
    relevance: Optional[Literal["high", "medium"]] = Field(
        default=None,
        description="Relevance level (only when action='keep')",
    )
    reasoning: str = Field(
        default="",
        description="Short Arabic note explaining the decision",
    )
    unfold_mode: Optional[str] = Field(
        default=None,
        description=(
            "Unfold strategy hint for reg_search "
            "(article_precise | section_detailed | regulation_detailed). "
            "Ignored by case and compliance."
        ),
    )


class RerankerClassification(BaseModel):
    """Output of one reranker LLM call — decisions only, no content.

    Used by all three domain rerankers.  ``weak_axes`` is an extension
    point populated only by compliance_search to drive the retry loop.
    """

    sufficient: bool = Field(
        description=(
            "True if kept results are >=80% sufficient to answer "
            "the sub-query (or the fused pool for compliance)"
        ),
    )
    decisions: list[RerankerDecision] = Field(
        default_factory=list,
        description="Per-result classification decisions",
    )
    summary_note: str = Field(
        default="",
        description="Brief Arabic note on collective sufficiency assessment",
    )
    weak_axes: list[Any] = Field(
        default_factory=list,
        description=(
            "Compliance-only: identified gaps for the expander retry loop. "
            "Each entry should be a WeakAxis-shaped dict or Pydantic object. "
            "Ignored by reg and case rerankers."
        ),
    )


__all__ = [
    "RerankerDecision",
    "RerankerClassification",
]
