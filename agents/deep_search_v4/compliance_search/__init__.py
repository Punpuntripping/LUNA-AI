"""Compliance search -- domain-specific government services search loop.

The compliance -> URA boundary converter (``compliance_to_rqr``) now lives in
``agents.deep_search_v4.ura.compliance_adapter`` alongside the other domain
adapters; import it from there.
"""
from .loop import run_compliance_search
from .models import (
    ComplianceSearchDeps,
    ComplianceSearchResult,
    ServiceDecision,
    ServiceRerankerOutput,
)

__all__ = [
    "run_compliance_search",
    "ComplianceSearchDeps",
    "ComplianceSearchResult",
    "ServiceDecision",
    "ServiceRerankerOutput",
]
