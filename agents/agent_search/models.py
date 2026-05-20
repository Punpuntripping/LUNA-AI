"""Input/output dataclasses for the agent_search publisher."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agents.deep_search_v4.shared.models import RerankerQueryResult
    from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact
    from agents.deep_search_v4.aggregator.models import AggregatorOutput


@dataclass
class SearchPublishInput:
    """Everything ``publish_search_result`` needs to persist a deep_search turn.

    Fields mirror the locals previously held in
    ``agents/orchestrator.py::_run_pydantic_ai_task`` -- nothing more, nothing
    less. Optional fields default to ``None`` / empty so callers can omit
    forensic-layer wiring without breaking the publish.
    """

    user_id: str
    conversation_id: str
    agg_output: "AggregatorOutput"
    original_query: str
    detail_level: str
    case_id: Optional[str] = None
    message_id: Optional[str] = None
    ura: Optional["UnifiedRetrievalArtifact"] = None
    reg_rqrs: list["RerankerQueryResult"] = field(default_factory=list)
    comp_rqrs: list["RerankerQueryResult"] = field(default_factory=list)
    case_rqrs: list["RerankerQueryResult"] = field(default_factory=list)
    per_executor_stats: dict[str, Any] = field(default_factory=dict)
    # Router-emitted short Arabic label (≤80 chars). When set, prefers this
    # over the aggregator artifact title / original_query fallback.
    # Optional during the Wave 1 migration; required path is router → orchestrator → here.
    task_label: Optional[str] = None
    # Router-emitted description of the user's query (typically 50–150 words).
    # Persisted to workspace_items.describe_query (migration 038). Used by the
    # planner to read prior task intent without parsing input_summary truncations.
    describe_query: Optional[str] = None


@dataclass
class SearchPublishOutput:
    """Result of publishing a deep_search turn.

    ``item_id`` is the new artifact_id (post-rename: workspace_items.item_id).
    ``sse_events`` is the list the orchestrator must yield into the SSE stream,
    in order. Wave 8 Cut-1 emits BOTH the new ``workspace_item_created`` event
    and the legacy ``artifact_created`` so the existing frontend keeps working
    until 8B drops the alias.
    """

    item_id: str
    sse_events: list[dict]
