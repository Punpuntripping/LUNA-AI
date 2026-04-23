"""Deep search V3 — URA pipeline.

Primary entry point:
    from agents.deep_search_v3.orchestrator import run_full_loop, FullLoopDeps

The pipeline:
    reg_search → partial URA → compliance_search → full URA → aggregator
"""
from .orchestrator import FullLoopDeps, run_full_loop
from .ura.merger import merge_partial_ura, merge_to_ura
from .ura.schema import PartialURA, UnifiedRetrievalArtifact, URAResult

__all__ = [
    "FullLoopDeps",
    "PartialURA",
    "URAResult",
    "UnifiedRetrievalArtifact",
    "merge_partial_ura",
    "merge_to_ura",
    "run_full_loop",
]
