"""Unified Retrieval Artifact (URA) schema -- v2.0.

URA is the canonical merged retrieval object that flows from the three
domain executors (reg_search, compliance_search, case_search) into the
aggregator. A single ``UnifiedRetrievalArtifact`` now carries:

- ``high_results`` / ``medium_results`` -- relevance-tiered buckets so the
  aggregator can consume high-signal results first and escalate to medium
  only when needed.
- Per-domain result classes -- ``RegURAResult``, ``ComplianceURAResult``,
  ``CaseURAResult`` -- wired through a Pydantic discriminated union on the
  ``domain`` field. Domain-specific columns live directly on their concrete
  subclass rather than being smuggled through a generic ``metadata`` dict.

The old ``PartialURA`` intermediate has been removed; the merger builds URA
in a single pass from the three executor outputs (see ``ura/merger.py``).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Domain = Literal["regulations", "compliance", "cases"]


class URAResultBase(BaseModel):
    """Fields shared across every domain-specific URA result."""

    ref_id: str
    source_type: str
    title: str
    content: str
    relevance: Literal["high", "medium"]
    reasoning: str = ""
    appears_in_sub_queries: list[int] = Field(default_factory=list)
    rrf_max: float = 0.0


class RegURAResult(URAResultBase):
    """Regulations-domain URA result (article or section)."""

    domain: Literal["regulations"] = "regulations"
    regulation_title: str
    article_num: str | None = None
    section_title: str | None = None
    article_context: str = ""
    section_summary: str = ""
    references_content: str = ""


class ComplianceURAResult(URAResultBase):
    """Compliance-domain URA result (government service)."""

    domain: Literal["compliance"] = "compliance"
    service_ref: str
    provider_name: str = ""
    platform_name: str = ""
    service_url: str = ""
    service_markdown: str = ""
    target_audience: str = ""
    service_channels: str = ""
    is_most_used: bool = False


class CaseURAResult(URAResultBase):
    """Cases-domain URA result (court ruling)."""

    domain: Literal["cases"] = "cases"
    court: str | None = None
    city: str | None = None
    court_level: str | None = None
    case_number: str | None = None
    judgment_number: str | None = None
    date_hijri: str | None = None
    legal_domains: list[str] = Field(default_factory=list)
    referenced_regulations: list[dict] = Field(default_factory=list)
    appeal_result: str | None = None


URAResult = Annotated[
    Union[RegURAResult, ComplianceURAResult, CaseURAResult],
    Field(discriminator="domain"),
]


class UnifiedRetrievalArtifact(BaseModel):
    """Tiered, typed retrieval artifact consumed by the aggregator."""

    schema_version: str = "2.0"
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
    "URAResultBase",
    "RegURAResult",
    "ComplianceURAResult",
    "CaseURAResult",
    "URAResult",
    "UnifiedRetrievalArtifact",
]
