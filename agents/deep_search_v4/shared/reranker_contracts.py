"""Shared unfold interface contracts for deep_search_v3 executors.

Each domain executor owns two unfold modules:

    unfold_reranker.py  -- compact markdown the LLM reranker grades.
    unfold_ura.py       -- rich content the aggregator synthesises from.

Every executor's unfold modules are expected to implement:

    build_reranker_view(row, position) -> str
        Compact markdown block for one result row.
        Governs what the LLM *sees* during the keep/drop classification step.

    build_ura_content(row, supabase=None, *, mode="precise") -> str
        Richer content for the aggregator / URA.
        May fetch additional DB fields (supabase optional for flat sources).

    build_ura_metadata(row) -> dict
        Domain-specific metadata fields for the URA result object
        (provider, URL, category, etc.).

Compliance ("no real unfold" note)
-----------------------------------
Compliance data is flat -- every service row already contains all fields
needed by both stages. The split between unfold_reranker and unfold_ura
is purely a *field selection* concern:

  Stage 1 (unfold_reranker): uses `service_context` (~600 chars) so a
      30-row pool stays within reranker token budget.
  Stage 2 (unfold_ura): uses `service_markdown` (full source text) so the
      aggregator synthesises from richer content.

There are no DB fetches in either compliance unfold module.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RerankerViewBuilder(Protocol):
    """Callable that formats a single result row for the reranker LLM."""

    def __call__(
        self,
        row: dict[str, Any],
        position: int = 1,
    ) -> str:
        """Return compact markdown for one result row at *position*."""
        ...


@runtime_checkable
class URAContentBuilder(Protocol):
    """Callable that builds the rich URA content string for one result row."""

    def __call__(
        self,
        row: dict[str, Any],
        supabase: Any = None,
        *,
        mode: str = "precise",
    ) -> str:
        """Return rich markdown for the aggregator.

        Args:
            row: Result row dict from the domain's search RPC / DB.
            supabase: Optional Supabase client. Required for domains that
                fetch extra fields (reg_search). Unused for flat sources
                (compliance, case -- all fields already in *row*).
            mode: ``"precise"`` (default) or ``"full"`` (more child rows).
        """
        ...


@runtime_checkable
class URAMetadataBuilder(Protocol):
    """Callable that extracts domain-specific metadata for the URA result."""

    def __call__(self, row: dict[str, Any]) -> dict[str, Any]:
        """Return a dict of domain-specific URA metadata fields."""
        ...


__all__ = [
    "RerankerViewBuilder",
    "URAContentBuilder",
    "URAMetadataBuilder",
]
