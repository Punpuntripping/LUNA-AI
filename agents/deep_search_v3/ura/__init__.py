"""Unified Retrieval Artifact — schema + merger.

The URA is the canonical merged retrieval object that flows from the
executors (reg_search, case_search, compliance_search) into the aggregator.
"""
from .merger import merge_partial_ura, merge_to_ura
from .schema import PartialURA, UnifiedRetrievalArtifact, URAResult

__all__ = [
    "PartialURA",
    "URAResult",
    "UnifiedRetrievalArtifact",
    "merge_partial_ura",
    "merge_to_ura",
]
