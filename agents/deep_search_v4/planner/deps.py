"""Runtime deps for the planner-driven loop (phases 2–3).

Three objects model the planner's runtime state — see PLANNER_REDESIGN_PLAN.md §6:

- :class:`PlannerDeps` — immutable infrastructure (Supabase, embedding fn, HTTP
  client, model overrides) plus the read-back slots that ``run_retrieval`` copies
  off the internal ``FullLoopDeps`` so artifact persistence and the monitor still
  work. Holds ``_agg_output`` — the ``AggregatorOutput`` stash read by phase 3
  and the degraded fallback.
- :class:`~.apply.RetrievalConfig` — the mode-derived knobs (lives in ``apply.py``).
- ``FullLoopDeps`` — the executor-config object assembled inside ``run_retrieval``.

**Invariant — ``PlannerDeps`` is never persisted and never survives a pause.**
It is rebuilt fresh by :func:`build_planner_deps` on every entry, including the
resume path. Only ``agent_runs.message_history`` (the decider's bytes) crosses
the pause boundary. ``planner_decider`` runs with ``deps_type=None`` — phase 1
needs no infra — so this object is used by phases 2–3 only.

Heavy types (Supabase, httpx, aggregator/URA models) are referenced under
``TYPE_CHECKING`` only, so importing this module stays cheap.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from supabase import Client as SupabaseClient

    from agents.deep_search_v4.aggregator.models import AggregatorOutput
    from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact

    from .models import PlannerDecision


DetailLevel = Literal["low", "medium", "high"]


@dataclass
class PlannerDeps:
    """Immutable infra + read-back slots for planner phases 2–3.

    Built fresh every turn by :func:`build_planner_deps`. The leading-underscore
    fields are populated by ``run_retrieval`` (read-back from ``FullLoopDeps``)
    so the orchestrator layer can persist the artifact and the monitor can
    render exactly what each phase produced.
    """

    # --- immutable infrastructure (set at construction) --------------------
    supabase: "SupabaseClient"
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    http_client: "httpx.AsyncClient | None" = None
    jina_api_key: str = ""
    model_override: str | None = None
    detail_level: DetailLevel = "medium"
    query_id: int = 0
    concurrency: int = 10
    unfold_mode: str = "precise"
    aggregator_logger: Any | None = None

    # --- SSE event sink ----------------------------------------------------
    emit_sse: Callable[[dict], None] | None = None
    _events: list[dict] = field(default_factory=list)

    # --- read-back slots (populated by run_retrieval from FullLoopDeps) ----
    _per_executor_stats: dict[str, dict] = field(default_factory=dict)
    _ura: "UnifiedRetrievalArtifact | None" = None
    _reg_rqrs: list = field(default_factory=list)
    _comp_rqrs: list = field(default_factory=list)
    _case_rqrs: list = field(default_factory=list)
    _reg_log_dir: str | None = None
    _comp_log_dir: str | None = None
    _case_log_dir: str | None = None
    _aggregator_input: Any | None = None
    # The aggregator's output — read by phase 3 (planner_responder instructions)
    # and the §9 degraded fallback.
    _agg_output: "AggregatorOutput | None" = None
    # The phase-1 decision — set by handle_planner_turn before phase 3 so the
    # responder's dynamic instruction can branch its framing on decision.mode.
    _decision: "PlannerDecision | None" = None


def build_planner_deps(
    *,
    supabase: "SupabaseClient",
    embedding_fn: Callable[[str], Awaitable[list[float]]],
    http_client: "httpx.AsyncClient | None" = None,
    jina_api_key: str = "",
    model_override: str | None = None,
    detail_level: DetailLevel = "medium",
    query_id: int = 0,
    concurrency: int = 10,
    unfold_mode: str = "precise",
    aggregator_logger: Any | None = None,
    emit_sse: Callable[[dict], None] | None = None,
) -> PlannerDeps:
    """Construct a fresh :class:`PlannerDeps`.

    Called on every entry into the planner — both fresh dispatch and resume.
    The resume path opens a brand-new ``httpx.AsyncClient`` and calls this
    builder fresh; it never reuses a deps object across a pause (see the
    module-level invariant), which removes the dead-``http_client`` risk class.
    """
    return PlannerDeps(
        supabase=supabase,
        embedding_fn=embedding_fn,
        http_client=http_client,
        jina_api_key=jina_api_key,
        model_override=model_override,
        detail_level=detail_level,
        query_id=query_id,
        concurrency=concurrency,
        unfold_mode=unfold_mode,
        aggregator_logger=aggregator_logger,
        emit_sse=emit_sse,
    )


__all__ = ["DetailLevel", "PlannerDeps", "build_planner_deps"]
