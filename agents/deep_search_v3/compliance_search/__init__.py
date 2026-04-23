"""Compliance search -- domain-specific government services search loop."""
from .loop import run_compliance_search
from .models import (
    ComplianceSearchDeps,
    ComplianceSearchResult,
    ComplianceURASlice,
    RegHit,
    ServiceDecision,
    ServiceRerankerOutput,
)
from .slice_builder import build_compliance_slice

__all__ = [
    "build_compliance_slice",
    "run_compliance_search",
    "ComplianceSearchDeps",
    "ComplianceSearchResult",
    "ComplianceURASlice",
    "RegHit",
    "ServiceDecision",
    "ServiceRerankerOutput",
]
