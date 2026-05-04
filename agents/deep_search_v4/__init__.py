"""Deep search V3 -- URA pipeline.

Primary entry point (after Wave D lands):
    from agents.deep_search_v4.orchestrator import run_full_loop, FullLoopDeps

The pipeline:
    reg_search + compliance_search + case_search (parallel)
        -> build_ura_from_phases -> UnifiedRetrievalArtifact -> aggregator

Wave A (this change) has already reshaped the URA schema to 2.0 and
introduced per-domain adapters under each executor's package. ``merger.py``
and ``orchestrator.py`` are still on their legacy shape and will be
rewritten in Waves B + D; importing ``.orchestrator`` eagerly here is
deferred until that work lands so the domain CLIs stay importable.
"""
from .ura.schema import (
    CaseURAResult,
    ComplianceURAResult,
    RegURAResult,
    URAResult,
    UnifiedRetrievalArtifact,
)

__all__ = [
    "CaseURAResult",
    "ComplianceURAResult",
    "RegURAResult",
    "URAResult",
    "UnifiedRetrievalArtifact",
]
