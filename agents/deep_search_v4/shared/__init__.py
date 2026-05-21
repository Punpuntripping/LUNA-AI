"""Shared layer for the 3 deep_search_v3 executors.

Exposes:
    - ``RerankerQueryResult`` -- unified per-sub-query container (all 3 domains).
    - ``RerankerDecision`` / ``RerankerClassification`` -- shared LLM output shapes.
    - ``reranker_contracts`` -- Protocol types for unfold interfaces.
    - ``reranker_loop`` -- Loop helpers: dedup, cap truncation, usage logging.
    - ``DEFAULT_SEARCH_CONCURRENCY`` -- shared Semaphore size for per-query
      search fan-out across case/reg/compliance.
"""
from .models import Domain, DomainResult, RerankerQueryResult
from .reranker_models import RerankerClassification, RerankerDecision

# Per-phase async fan-out cap for the per-sub-query search/RPC tasks. Used by
# case_search, reg_search, and compliance_search so they bound concurrency
# identically against Supabase + the embedding endpoint.
DEFAULT_SEARCH_CONCURRENCY = 10

__all__ = [
    "DEFAULT_SEARCH_CONCURRENCY",
    "Domain",
    "DomainResult",
    "RerankerClassification",
    "RerankerDecision",
    "RerankerQueryResult",
]
