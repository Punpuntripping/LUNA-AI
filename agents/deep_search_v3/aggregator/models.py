"""Pydantic models + dataclasses for the aggregator agent.

Input/Output contract is stable across reg_search / case_search / compliance —
downstream agents only add to AggregatorInput's optional fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from agents.deep_search_v3.reg_search.models import RerankerQueryResult

if TYPE_CHECKING:
    from agents.deep_search_v3.compliance_search.models import ComplianceURASlice


# ---------------------------------------------------------------------------
# Pydantic models (LLM output + API contracts)
# ---------------------------------------------------------------------------


class Reference(BaseModel):
    """One numbered citation entry in the final reference list.

    `n` is the 1-based number used in the synthesis body as `(n)` or `(n,m)`.
    Numbers are assigned by the pre-processor (code), NOT by the LLM —
    this is the central anti-hallucination mechanism.
    """

    n: int = Field(description="1-based citation number used inline in synthesis")
    source_type: Literal["article", "section", "regulation", "gov_service", "form"] = Field(
        description='Type of source'
    )
    regulation_title: str = Field(description="Parent regulation name (Arabic)")
    article_num: Optional[str] = Field(
        default=None,
        description="Article number if source_type == 'article'",
    )
    section_title: Optional[str] = Field(
        default=None,
        description="Section path if source_type == 'section'",
    )
    title: str = Field(description="Human-readable title (Arabic)")
    snippet: str = Field(
        default="",
        description="Short Arabic excerpt (used for UI hover + validator grounding)",
    )
    relevance: Literal["high", "medium"] = Field(description="Upstream reranker tag")
    ref_id: str = Field(
        default="",
        description="URA ref_id -- reg:{uuid} or compliance:{hash}",
    )
    domain: Literal["regulations", "compliance"] = Field(
        default="regulations",
        description="Which executor produced this reference",
    )

    def render_label(self) -> str:
        """Human-readable reference label for the end-of-doc list.

        Example: "نظام الأحوال الشخصية — مادة 51"
        """
        if self.source_type == "article" and self.article_num:
            return f"{self.regulation_title} — مادة {self.article_num}"
        if self.source_type == "section" and self.section_title:
            return f"{self.regulation_title} — {self.section_title}"
        return self.regulation_title


class AggregatorLLMOutput(BaseModel):
    """Raw output expected from the LLM (before post-validation).

    The LLM receives pre-numbered references and must only:
    - write synthesis_md using `(n)` inline citations
    - list the reference numbers it actually used in `used_refs`
    - flag any gaps it noticed
    - self-rate confidence
    """

    synthesis_md: str = Field(
        description="Arabic markdown body with inline (n) or (n,m) citations"
    )
    used_refs: list[int] = Field(
        default_factory=list,
        description="List of reference numbers cited in synthesis_md",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Arabic short notes on aspects the references did not cover",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="LLM self-assessed confidence in synthesis"
    )


class AggregatorOutput(BaseModel):
    """Final output returned to callers (post-validation + artifact built)."""

    synthesis_md: str = Field(description="Arabic markdown with inline (n) citations")
    references: list[Reference] = Field(
        default_factory=list,
        description="Ordered list — references[i-1].n == i",
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Final confidence")
    gaps: list[str] = Field(
        default_factory=list,
        description="Unanswered aspects surfaced to user",
    )
    disclaimer_ar: str = Field(
        default="",
        description="Luna legal disclaimer appended at render time",
    )
    prompt_key: str = Field(
        default="prompt_1",
        description="Which prompt variant produced this (for A/B + logs)",
    )
    model_used: str = Field(
        default="",
        description='Final model that produced the synthesis (e.g. "qwen3.6-plus")',
    )
    validation: "ValidationReport | None" = Field(
        default=None,
        description="Post-validator findings (None if not run)",
    )
    artifact: "Artifact | None" = Field(
        default=None,
        description="Frontend artifact object (None if caller requested raw-only)",
    )


class ValidationReport(BaseModel):
    """Post-validator findings — attached to AggregatorOutput for debugging/A-B."""

    passed: bool
    cited_numbers: list[int] = Field(default_factory=list)
    dangling_citations: list[int] = Field(
        default_factory=list,
        description="(n) in synthesis that has no matching reference",
    )
    unused_references: list[int] = Field(
        default_factory=list,
        description="References assigned but never cited",
    )
    ungrounded_snippets: list[int] = Field(
        default_factory=list,
        description="Reference numbers whose snippet is not in any reranker result",
    )
    sub_query_coverage: float = Field(
        default=0.0,
        description="Fraction of sufficient sub-queries with >=1 reference cited",
    )
    query_anchoring_ok: bool = Field(default=True)
    arabic_only_ok: bool = Field(default=True)
    structure_ok: bool = Field(default=True)
    gap_honesty_ok: bool = Field(default=True)
    notes: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    """Frontend artifact object (matches backend/app/api/artifacts.py schema)."""

    kind: Literal["legal_synthesis"] = "legal_synthesis"
    title: str
    content: str = Field(description="Full synthesis_md + reference list + disclaimer")
    references_json: list[dict] = Field(
        default_factory=list,
        description="Serialized Reference list for interactive citation panel",
    )
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dataclasses (programmatic, not LLM output)
# ---------------------------------------------------------------------------


@dataclass
class AggregatorInput:
    """Everything the aggregator needs to synthesize a final answer.

    `sub_queries` comes from the reranker. `case_results` is out of scope
    for the URA pipeline. `compliance_results` carries URA-shaped compliance
    results when the URA pipeline is in use.
    """

    original_query: str
    sub_queries: list[RerankerQueryResult]
    domain: Literal["regulations", "cases", "compliance", "multi"] = "regulations"
    session_id: str = ""
    query_id: int = 0
    log_id: str = ""
    prompt_key: str = "prompt_1"
    enable_dcr: bool = False  # Draft-Critique-Rewrite chain (prompt_3 only)
    case_results: Any = None  # Out of scope
    compliance_results: "ComplianceURASlice | None" = None


AggregatorOutput.model_rebuild()
