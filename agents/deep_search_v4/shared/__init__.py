"""Shared layer for the 3 deep_search_v3 executors.

Exposes:
    - ``RerankerQueryResult`` -- unified per-sub-query container (all 3 domains).
    - ``RerankerDecision`` / ``RerankerClassification`` -- shared LLM output shapes.
    - ``reranker_contracts`` -- Protocol types for unfold interfaces.
    - ``reranker_loop`` -- Loop helpers: dedup, cap truncation, usage logging.
"""
from .models import Domain, DomainResult, RerankerQueryResult
from .reranker_models import RerankerClassification, RerankerDecision

__all__ = [
    "Domain",
    "DomainResult",
    "RerankerClassification",
    "RerankerDecision",
    "RerankerQueryResult",
]
