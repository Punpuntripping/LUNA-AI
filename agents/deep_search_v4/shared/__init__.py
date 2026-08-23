"""Shared layer for the 3 deep_search_v3 executors.

Exposes:
    - ``RerankerQueryResult`` -- unified per-sub-query container (all 3 domains).
    - ``reranker_contracts`` -- Protocol types for unfold interfaces.
    - ``reranker_loop`` -- Loop helpers: dedup, cap truncation, usage logging.
    - ``DEFAULT_SEARCH_CONCURRENCY`` -- shared Semaphore size for per-query
      search fan-out across case/reg/compliance (per-executor, NOT the DB cap).
    - ``db_gate`` -- process-wide admission gate for Supabase search RPCs; this
      is the actual ceiling on concurrent database load. Import it directly
      (``from agents.deep_search_v4.shared.db_gate import search_gate``).

Each domain now owns its reranker LLM-output schema (CaseRerankerClassification,
RegRerankerClassification, ServiceRerankerOutput) — there is no shared output
model anymore (the old ``reranker_models`` was retired 2026-06-08 to remove
schema-vs-domain field leakage).
"""
from .models import Domain, DomainResult, RerankerQueryResult

# Per-phase async fan-out cap for the per-sub-query search/RPC tasks. Used by
# case_search and reg_compliance_search so they bound concurrency identically
# against Supabase + the embedding endpoint.
#
# THIS IS NOT THE DATABASE CEILING. It bounds pipeline tasks *within one
# executor*, and each executor builds its own Semaphore from it — so with
# case_search and reg_compliance_search running in parallel the peak in-flight
# against Postgres was ~20, which collapsed a whole turn on 2026-08-22 (all 16
# concurrent RPCs hit httpx.ReadTimeout at once). The real, process-wide cap on
# concurrent Supabase search RPCs is ``db_gate.SEARCH_RPC_CONCURRENCY``; every
# search RPC queues through ``db_gate.search_gate()`` above and beyond whatever
# this constant allows. Raising the value below does NOT raise database load —
# it only lets more pipeline tasks queue at the gate.
DEFAULT_SEARCH_CONCURRENCY = 10

__all__ = [
    "DEFAULT_SEARCH_CONCURRENCY",
    "Domain",
    "DomainResult",
    "RerankerQueryResult",
]
