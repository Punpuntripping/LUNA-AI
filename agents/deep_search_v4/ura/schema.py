"""Unified Retrieval Artifact (URA) schema -- v3.0.

URA is the canonical merged retrieval object that flows from the three
domain executors (reg_search, compliance_search, case_search) into the
aggregator. A single ``UnifiedRetrievalArtifact`` carries:

- ``high_results`` / ``medium_results`` -- relevance-tiered buckets.
- Per-domain result classes -- ``RegURAResult``, ``ComplianceURAResult``,
  ``CaseURAResult`` -- wired through a Pydantic discriminated union on the
  ``domain`` field.

v3.0 reshape (URA Two-View Reframe):
- Each result class holds the **full unfolded data** for its kept result.
- Two typed projections per result -- ``.for_aggregator()`` (synthesis input)
  and ``.for_reference()`` (citation metadata). The generic ``title`` /
  ``content`` fields are gone from the base; each domain names its own.
- The heavy fields (full content, cross-refs, landing urls, entity names) are
  filled post-merge by ``ura/enrich.py`` -- the adapters build lightweight
  shells, ``enrich_ura`` mutates them in place.

Mutation contract (load-bearing): these models MUST stay plain ``BaseModel`` --
no ``frozen=True``, no ``validate_assignment=True``. ``ura/enrich.py`` mutates
result instances in place after the merger builds them.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Domain = Literal["regulations", "compliance", "cases"]


# -- Cross-ref caps -- applied at projection time, not at fetch time ----------
# enrich.py fetches + dedups every cross-ref; the projections truncate.
MAX_CROSS_REFS_AGG_REG = 5     # reg aggregator view
MAX_CROSS_REFS_AGG_CASE = 3    # case aggregator view (referenced_regulations)
MAX_CROSS_REFS_REF = 10        # both domains, reference view


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------


class CrossRef(BaseModel):
    """One resolved cross-reference from a chunk (or case) to a target unit.

    ``target_type`` is an open ``str`` (NOT a ``Literal``) on purpose -- a new
    type (``appendix``) is being added and persisted artifacts must reload
    without a validation error. Renders as
    ``{target_reg_title}, {target_type}:{target_number}`` + ``content``.
    """

    target_type: str = ""
    target_reg_title: str = ""
    target_number: int | None = None
    relation: str = ""
    content: str = ""  # resolved body; "" when unresolved / null target_id


# ---------------------------------------------------------------------------
# Projection target models
# ---------------------------------------------------------------------------


class AggregatorItem(BaseModel):
    """Trimmed projection a URA result exposes to the aggregator prompt builder.

    Flat and ``domain``-tagged -- each domain fills only its own subset of
    fields. The prompt builder switches on ``domain``.

    ``n`` is the **shared citation index** -- the same 1-based number the
    preprocessor stamps on the matching ``Reference``. ``for_aggregator(n)``
    receives it from the preprocessor (the URA result cannot know its own tier
    position). It is what the aggregator cites inline as ``[n]``; both
    projections are keyed by this single index.
    """

    ref_id: str
    n: int = 0
    domain: Domain
    relevance: Literal["high", "medium"]
    # regulations
    reg_title: str = ""
    reg_scope: str = ""
    chunk_content: str = ""
    cross_refs: list[CrossRef] = Field(default_factory=list)
    # compliance
    service_name: str = ""
    service_context: str = ""
    provider_name: str = ""
    # cases
    case_number: str | None = None
    case_content: str = ""
    referenced_regulations: list[dict] = Field(default_factory=list)


class ReferenceView(BaseModel):
    """Trimmed projection a URA result exposes to the citation builder.

    The aggregator preprocessor stamps the 1-based ``n`` and attaches the
    ``source_view`` to turn this into a final ``Reference`` -- those two are
    preprocessor concerns, not something a URA result can produce.
    """

    ref_id: str
    domain: Domain
    source_type: str
    relevance: Literal["high", "medium"]
    # regulations
    reg_title: str = ""
    landing_url: str = ""
    cross_refs: list[CrossRef] = Field(default_factory=list)
    # compliance
    service_name: str = ""
    provider_name: str = ""
    service_url: str = ""
    url: str = ""
    # cases
    case_number: str | None = None
    judgment_number: str | None = None
    court: str | None = None
    city: str | None = None
    details_url: str | None = None
    entity_name: str = ""
    referenced_regulations: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# URA result classes
# ---------------------------------------------------------------------------


class URAResultBase(BaseModel):
    """Cross-domain plumbing shared by every URA result.

    No generic ``title`` / ``content`` -- each domain names its own content
    fields (v3.0). ``domain`` (the discriminator) lives on each subclass.
    """

    ref_id: str
    source_type: str
    relevance: Literal["high", "medium"]
    reasoning: str = ""
    appears_in_sub_queries: list[int] = Field(default_factory=list)
    rrf_max: float = 0.0


class RegURAResult(URAResultBase):
    """Regulations-domain URA result (one kept chunk).

    The adapter builds the shell (base fields only); ``ura/enrich.py`` fills
    every field below post-merge.
    """

    domain: Literal["regulations"] = "regulations"
    reg_title: str = ""
    reg_scope: str = ""
    chunk_content: str = ""
    chunk_context: str = ""        # stored only
    cross_refs: list[CrossRef] = Field(default_factory=list)
    landing_url: str = ""
    pdf_url: str = ""              # stored only
    owns: dict = Field(default_factory=dict)  # stored only

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="regulations",
            relevance=self.relevance,
            reg_title=self.reg_title,
            reg_scope=self.reg_scope,
            chunk_content=self.chunk_content,
            cross_refs=list(self.cross_refs[:MAX_CROSS_REFS_AGG_REG]),
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="regulations",
            source_type=self.source_type,
            relevance=self.relevance,
            reg_title=self.reg_title,
            landing_url=self.landing_url,
            cross_refs=list(self.cross_refs[:MAX_CROSS_REFS_REF]),
        )


class ComplianceURAResult(URAResultBase):
    """Compliance-domain URA result (one government service).

    The compliance adapter already carries every field below -- ``enrich_ura``
    is a no-op for this domain.
    """

    domain: Literal["compliance"] = "compliance"
    service_name: str = ""
    service_context: str = ""
    provider_name: str = ""
    service_url: str = ""
    url: str = ""                 # fallback link for service_url
    service_ref: str = ""         # stored only -- mints ref_id upstream
    sectors: list[str] = Field(default_factory=list)  # stored only
    is_most_used: bool = False    # stored only
    is_proactive: bool = False    # stored only

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="compliance",
            relevance=self.relevance,
            service_name=self.service_name,
            service_context=self.service_context,
            provider_name=self.provider_name,
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="compliance",
            source_type=self.source_type,
            relevance=self.relevance,
            service_name=self.service_name,
            provider_name=self.provider_name,
            service_url=self.service_url,
            url=self.url,
        )


class CaseURAResult(URAResultBase):
    """Cases-domain URA result (one court ruling).

    The case adapter carries case content / metadata; ``enrich_ura`` adds the
    reference-view fields the reranker output lacks (``details_url`` and the
    resolved ``entity_name``).
    """

    domain: Literal["cases"] = "cases"
    case_number: str | None = None
    case_content: str = ""
    referenced_regulations: list[dict] = Field(default_factory=list)
    judgment_number: str | None = None
    court: str | None = None
    city: str | None = None
    details_url: str | None = None
    entity_name: str = ""
    entity_id: str | None = None  # stored only -- entity_name resolve key
    title: str = ""               # stored only
    court_level: str | None = None  # stored only
    date_hijri: str | None = None   # stored only
    legal_domains: list[str] = Field(default_factory=list)  # stored only
    appeal_result: str | None = None  # stored only

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="cases",
            relevance=self.relevance,
            case_number=self.case_number,
            case_content=self.case_content,
            referenced_regulations=list(
                self.referenced_regulations[:MAX_CROSS_REFS_AGG_CASE]
            ),
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="cases",
            source_type=self.source_type,
            relevance=self.relevance,
            case_number=self.case_number,
            judgment_number=self.judgment_number,
            court=self.court,
            city=self.city,
            details_url=self.details_url,
            entity_name=self.entity_name,
            referenced_regulations=list(
                self.referenced_regulations[:MAX_CROSS_REFS_REF]
            ),
        )


URAResult = Annotated[
    Union[RegURAResult, ComplianceURAResult, CaseURAResult],
    Field(discriminator="domain"),
]


class UnifiedRetrievalArtifact(BaseModel):
    """Tiered, typed retrieval artifact consumed by the aggregator."""

    schema_version: str = "3.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    produced_by: dict = Field(
        default_factory=lambda: {
            "reg_search": False,
            "compliance_search": False,
            "case_search": False,
        }
    )
    sub_queries: list[dict] = Field(default_factory=list)
    high_results: list[URAResult] = Field(default_factory=list)
    medium_results: list[URAResult] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)
    sector_filter: list[str] = Field(default_factory=list)


__all__ = [
    "Domain",
    "CrossRef",
    "AggregatorItem",
    "ReferenceView",
    "URAResultBase",
    "RegURAResult",
    "ComplianceURAResult",
    "CaseURAResult",
    "URAResult",
    "UnifiedRetrievalArtifact",
    "MAX_CROSS_REFS_AGG_REG",
    "MAX_CROSS_REFS_AGG_CASE",
    "MAX_CROSS_REFS_REF",
]
