"""Unified Retrieval Artifact -- schema + merger.

URA 2.0 is the canonical merged retrieval object that flows from the
three executors (reg_search, compliance_search, case_search) into the
aggregator. It exposes:

- ``UnifiedRetrievalArtifact`` with ``high_results`` / ``medium_results``
  relevance tiers and per-domain result subclasses (``RegURAResult``,
  ``ComplianceURAResult``, ``CaseURAResult``) wired through a Pydantic
  discriminated union on the ``domain`` field.
- ``build_ura_from_phases`` -- single-pass merger that consumes the
  shared ``RerankerQueryResult`` lists from each domain adapter and
  returns a fully tiered URA 2.0 artifact (see ``ura/merger.py``).

The legacy two-stage ``merge_partial_ura`` / ``merge_to_ura`` pair and
the ``PartialURA`` intermediate have been removed.
"""
from .merger import DOMAIN_RANK, build_ura_from_phases
from .schema import (
    CaseURAResult,
    ComplianceURAResult,
    Domain,
    RegURAResult,
    URAResult,
    URAResultBase,
    UnifiedRetrievalArtifact,
)

__all__ = [
    "Domain",
    "URAResult",
    "URAResultBase",
    "RegURAResult",
    "ComplianceURAResult",
    "CaseURAResult",
    "UnifiedRetrievalArtifact",
    "build_ura_from_phases",
    "DOMAIN_RANK",
]
