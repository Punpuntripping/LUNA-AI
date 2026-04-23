"""Unified Retrieval Artifact (URA) schema.

URA is the canonical merged retrieval object that flows:
    reg_search → partial URA → compliance → full URA → aggregator.

A PartialURA contains only reg_search results. A UnifiedRetrievalArtifact
contains reg + compliance results merged by ref_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class URAResult:
    """One result inside a URA -- domain-agnostic citation carrier."""

    ref_id: str
    domain: Literal["regulations", "compliance"]
    source_type: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)
    relevance: str = "medium"
    reasoning: str = ""
    appears_in_sub_queries: list[int] = field(default_factory=list)
    rrf_max: float = 0.0
    triggered_by_ref_ids: list[str] = field(default_factory=list)
    cross_references: list[dict] = field(default_factory=list)


@dataclass
class PartialURA:
    """Intermediate URA containing only reg_search results."""

    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    sub_queries: list[dict] = field(default_factory=list)
    results: list[URAResult] = field(default_factory=list)
    # Canonical legal-sector names from prompt_2 expander (empty when prompt_1 used).
    # Carried forward so downstream agents (compliance, aggregator) can filter by sector.
    sector_filter: list[str] = field(default_factory=list)


@dataclass
class UnifiedRetrievalArtifact:
    """Full URA containing reg + compliance results ready for the aggregator."""

    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    produced_by: dict = field(default_factory=dict)
    sub_queries: list[dict] = field(default_factory=list)
    results: list[URAResult] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    sector_filter: list[str] = field(default_factory=list)


__all__ = [
    "URAResult",
    "PartialURA",
    "UnifiedRetrievalArtifact",
]
