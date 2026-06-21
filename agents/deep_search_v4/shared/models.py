"""Shared models across the 3 deep_search_v3 executors.

Each domain (``reg_search``, ``compliance_search``, ``case_search``) wraps
its internal reranker output into a ``RerankerQueryResult`` whose
``results`` list is typed to that domain's URA result subclass. The
orchestrator (Wave D) then feeds these directly into the single-pass URA
merger (Wave B) without any intermediate conversion step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    ComplianceURAResult,
    RegURAResult,
)

Domain = Literal["regulations", "compliance", "cases"]
DomainResult = Union[RegURAResult, ComplianceURAResult, CaseURAResult]


@dataclass
class RerankerQueryResult:
    """Per-sub-query reranker output -- shared across all 3 executors.

    ``results`` is a list of domain-matched URA results (concretely one of
    ``RegURAResult``, ``ComplianceURAResult``, ``CaseURAResult``). Type
    checkers can narrow on the ``domain`` discriminator; at runtime the
    merger uses ``isinstance`` / ``.domain`` for dispatch.
    """

    query: str
    rationale: str
    sufficient: bool
    domain: Domain
    results: list  # list[DomainResult]
    dropped_count: int = 0
    summary_note: str = ""
    unfold_rounds: int = 0
    total_unfolds: int = 0
    caps_applied: dict = field(default_factory=dict)
    # ``caps_applied`` carries {"max_keep": int, "truncated_by_cap": int} when
    # the per-run flat keep cap was applied by the domain reranker. Empty dict
    # when the cap was not active or not supported.

    # -- Forensic projections (reranker_runs persistence) ---------------------
    # Self-contained dicts built by the domain adapters from the original
    # search rows (which still carry the real target-table UUID + title). These
    # decouple the forensic layer from the typed URA/citation results: the
    # citation ``ref_id`` (``reg:``/``case:``/``compliance:``) is load-bearing
    # downstream and is NOT what we persist here.
    #
    # kept_forensic   -- 1:1 with ``results``. Each entry:
    #   {source_table: "chunks"|"cases"|"services", ref_id: <bare UUID>,
    #    title: str, relevance: "high"|"medium", source_type: str, reasoning: str}
    # dropped_forensic -- LLM-dropped + cap-truncated results only. Each entry:
    #   {source_table, ref_id: <bare UUID>, title, drop_reason: "llm"|"cap",
    #    reasoning: str (""for cap), source_type: str}
    kept_forensic: list = field(default_factory=list)     # list[dict]
    dropped_forensic: list = field(default_factory=list)  # list[dict]


__all__ = ["Domain", "DomainResult", "RerankerQueryResult"]
