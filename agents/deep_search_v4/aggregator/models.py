"""Pydantic models + dataclasses for the aggregator agent.

Input/Output contract is stable across reg_search / case_search / compliance —
downstream agents only add to AggregatorInput's optional fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from agents.deep_search_v4.shared.context import ContextBlock
from agents.deep_search_v4.shared.models import RerankerQueryResult
from agents.deep_search_v4.source_viewer import SourceView
from agents.deep_search_v4.ura.schema import CrossRef

if TYPE_CHECKING:
    from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact


# ---------------------------------------------------------------------------
# Pydantic models (LLM output + API contracts)
# ---------------------------------------------------------------------------


class Reference(BaseModel):
    """One numbered citation entry in the final reference list.

    `n` is the 1-based number used in the synthesis body as `[n]` or `[n,m]`.
    Numbers are assigned by the pre-processor (code), NOT by the LLM —
    this is the central anti-hallucination mechanism.
    """

    n: int = Field(description="1-based citation number used inline in synthesis as [n]")
    source_type: Literal[
        "article",
        "section",
        "chunk",
        "regulation",
        "gov_service",
        "form",
        "case",
    ] = Field(description='Type of source')
    regulation_title: str = Field(description="Parent regulation name (Arabic)")
    article_num: Optional[str] = Field(
        default=None,
        description="Legacy article number (pre-URA-v3.0 reranker path only)",
    )
    section_title: Optional[str] = Field(
        default=None,
        description="Legacy section path (pre-URA-v3.0 reranker path only)",
    )
    title: str = Field(description="Human-readable title (Arabic)")
    snippet: str = Field(
        default="",
        description=(
            "Short Arabic excerpt for UI hover only. Derived from the "
            "aggregator-view content (truncated). NOT the grounding source "
            "of truth -- the validator grounds against aggregator-view content."
        ),
    )
    relevance: Literal["high", "medium"] = Field(description="Upstream reranker tag")
    ref_id: str = Field(
        default="",
        description="URA ref_id -- reg:{uuid} | compliance:{hash} | case:{uuid}",
    )
    domain: Literal["regulations", "compliance", "cases"] = Field(
        default="regulations",
        description="Which executor produced this reference",
    )
    # -- Reference-view link/metadata fields (URA v3.0 two-view reframe) -----
    # Populated from ``URAResult.for_reference()`` -> ``ReferenceView``.
    # Display-only metadata for the citation panel / frontend.
    landing_url: str = Field(
        default="",
        description="Regulation landing URL (reg domain)",
    )
    service_url: str = Field(
        default="",
        description="Government service URL (compliance domain)",
    )
    url: str = Field(
        default="",
        description="National-platform URL fallback for service_url (compliance domain)",
    )
    details_url: str = Field(
        default="",
        description="Court ruling details URL (cases domain)",
    )
    entity_name: str = Field(
        default="",
        description="Resolved court/entity Arabic name (cases domain)",
    )
    cross_refs: list[CrossRef] = Field(
        default_factory=list,
        description="Resolved cross-references (reg domain, reference-view cap)",
    )
    source_view: SourceView | None = Field(
        default=None,
        description=(
            "Click-ready original-source payload for the user popup. "
            "Populated by the preprocessor via build_source_view()."
        ),
    )

    def render_label(self) -> str:
        """Human-readable reference label for the end-of-doc list.

        URA v3.0: reg references are chunk-shaped -- the article/section
        distinction is gone, so reg degrades to a bare ``regulation_title``.
        The legacy ``article``/``section`` types (pre-URA reranker path)
        still render the richer label.
        """
        if self.source_type == "article" and self.article_num:
            return f"{self.regulation_title} — مادة {self.article_num}"
        if self.source_type == "section" and self.section_title:
            return f"{self.regulation_title} — {self.section_title}"
        return self.regulation_title


class AggregatorLLMOutput(BaseModel):
    """Raw output expected from the LLM (before post-validation).

    The LLM receives pre-numbered references and must only:
    - write synthesis_md using `[n]` inline citations
    - list the reference numbers it actually used in `used_refs`
    - flag any gaps it noticed
    - self-rate confidence

    Chat-display summarization (the old ``chat_summary`` + ``key_findings``
    fields) was moved out of the aggregator in Wave 10 and is now produced
    by the dedicated ``artifact_summarizer`` agent on the published artifact.
    """

    synthesis_md: str = Field(
        description="Arabic markdown body with inline [n] or [n,m] citations"
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

    synthesis_md: str = Field(description="Arabic markdown with inline [n] citations")
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
    # Migration 049: per-WI ref state carried out of preprocess_references so
    # the agent_search publisher can persist it to workspace_item_references.
    # Empty default keeps legacy callers (replay tests, CLIs) working.
    ref_to_sub_queries: dict[int, list[int]] = Field(
        default_factory=dict,
        description="Reference.n -> sorted list of 0-based sub_query indices that produced it",
    )


class ValidationReport(BaseModel):
    """Post-validator findings — attached to AggregatorOutput for debugging/A-B."""

    passed: bool
    cited_numbers: list[int] = Field(default_factory=list)
    dangling_citations: list[int] = Field(
        default_factory=list,
        description="[n] in synthesis that has no matching reference",
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

    URA 2.0 (see ``agents/deep_search_v3/ura/schema.py``) is the canonical
    source of retrieval results. ``ura`` carries the tiered typed artifact;
    ``sub_queries`` is reconstructed from ``ura.sub_queries`` so the
    preprocessor / prompt builder keep their existing shape without having
    to walk the URA tiers themselves.

    Legacy callers that build ``sub_queries`` manually (older CLIs, replay
    tests) are still supported by leaving ``ura=None``. In that case the
    preprocessor falls back to iterating ``sub_queries`` directly.
    """

    original_query: str
    sub_queries: list[RerankerQueryResult]
    domain: Literal["regulations", "cases", "compliance", "multi"] = "regulations"
    session_id: str = ""
    query_id: int = 0
    log_id: str = ""
    prompt_key: str = "prompt_1"
    enable_dcr: bool = False  # Draft-Critique-Rewrite chain (prompt_3 only)
    detail_level: Literal["low", "medium", "high"] = "medium"
    ura: "UnifiedRetrievalArtifact | None" = None
    # Planner-curated context bundle (§4 / §5.3). Default empty preserves
    # pre-redesign behavior; the inner orchestrator wires this in run_retrieval
    # after the planner emits decision.context_labels. The bundle reaches the
    # aggregator user message (rendered before <references>) — NOT any reranker.
    context_blocks: list[ContextBlock] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ``Literal`` on a dataclass field is type-hint only -- not enforced at
        # runtime. This is the last line of defense before the prompt renderer
        # emits ``<detail_level>{value}</detail_level>``.
        if self.detail_level not in ("low", "medium", "high"):
            self.detail_level = "medium"

    @classmethod
    def from_ura(
        cls,
        ura: "UnifiedRetrievalArtifact",
        *,
        prompt_key: str = "prompt_1",
        detail_level: Literal["low", "medium", "high"] = "medium",
        session_id: str = "",
        enable_dcr: bool = False,
    ) -> "AggregatorInput":
        """Build an AggregatorInput from URA 2.0.

        Reconstructs ``sub_queries`` as a list of shared ``RerankerQueryResult``
        dataclasses (one per URA ``sub_queries`` entry), populating each
        entry's ``.results`` with every URA result (across both tiers) whose
        ``appears_in_sub_queries`` contains that sub-query's index.

        ``domain`` is derived from ``ura.produced_by``:
          - exactly one domain flagged True -> that domain
          - more than one (or zero) -> ``"multi"``
        """
        produced_by = ura.produced_by or {}
        flagged = [
            # Map produced_by keys to the aggregator domain literal.
            ("regulations", bool(produced_by.get("reg_search"))),
            ("compliance", bool(produced_by.get("compliance_search"))),
            ("cases", bool(produced_by.get("case_search"))),
        ]
        active = [name for name, on in flagged if on]
        if len(active) == 1:
            domain: Literal["regulations", "cases", "compliance", "multi"] = active[0]  # type: ignore[assignment]
        else:
            domain = "multi"

        all_results = list(ura.high_results or []) + list(ura.medium_results or [])

        # Build `sub_queries` using the shared RerankerQueryResult dataclass.
        sub_queries: list[RerankerQueryResult] = []
        for sq_meta in ura.sub_queries or []:
            sq_idx = sq_meta.get("index")
            if sq_idx is None:
                continue
            sq_idx_int = int(sq_idx)
            sq_domain = sq_meta.get("domain", "regulations")
            # Collect URA results whose appears_in_sub_queries contains this index.
            sq_results = [
                r
                for r in all_results
                if sq_idx_int in (r.appears_in_sub_queries or [])
            ]
            sub_queries.append(
                RerankerQueryResult(
                    query=sq_meta.get("query", "") or "",
                    rationale=sq_meta.get("rationale", "") or "",
                    sufficient=bool(sq_meta.get("sufficient", True)),
                    domain=sq_domain,  # type: ignore[arg-type]
                    results=sq_results,
                    dropped_count=int(sq_meta.get("dropped_count", 0) or 0),
                    summary_note=sq_meta.get("summary_note", "") or "",
                )
            )

        return cls(
            original_query=ura.original_query or "",
            sub_queries=sub_queries,
            domain=domain,
            session_id=session_id or (ura.log_id or ""),
            query_id=int(ura.query_id or 0),
            log_id=ura.log_id or "",
            prompt_key=prompt_key,
            enable_dcr=enable_dcr,
            detail_level=detail_level,
            ura=ura,
        )


AggregatorOutput.model_rebuild()
