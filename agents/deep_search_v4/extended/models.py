"""Shared data shapes of «بحث شامل» — the contract between the build's parts.

Keep the field names stable: the planner side (budget / view / planner / card / brief),
the execution side (runner / merger / publish), the orchestrator wiring and the web +
mobile clients (frontend/lib/contracts/extended-plan.ts mirrors ``ExtendedPlanCard``)
all build against them. Add fields freely; rename or remove only with every consumer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SearchMode = Literal["reg_compliance_led", "case_led", "full"]
PlanOptionId = Literal["plan", "cheaper", "ordinary"]
PlanOutcome = Literal["plan", "cheaper", "ordinary", "typed", "expired", "insufficient"]
PlanSearchSides = Literal["regulations", "judgments", "both"]

#: ``paused_runs.pause_reason`` and ``messages.metadata.pause_reason`` of a plan card.
PAUSE_REASON = "extended_plan"
#: ``messages.metadata`` key of the card; ``paused_runs.deferred_payload`` key of the pause.
CARD_METADATA_KEY = "extended_plan"

SIDES_OF_MODE: dict[str, PlanSearchSides] = {
    "reg_compliance_led": "regulations",
    "case_led": "judgments",
    "full": "both",
}

#: Button labels (Arabic, shown on the card AND accepted as typed text, D32 fallback).
OPTION_LABELS_AR: dict[str, str] = {
    "plan": "نفّذ الخطة",
    "cheaper": "نفّذ الأوفر",
    "ordinary": "بحث عادي",
}


# --------------------------------------------------------------------------- budget


@dataclass(frozen=True)
class BudgetMode:
    """A budget mode (§4.3, D22) or no budget (D33: over 50 points left).

    ``points`` — 15 / 20 / 30 / 40 / 50, or ``None`` for no budget. The three limits are
    ``None`` under no budget (no limit from the budget; §7 Q18 technical ceiling: none).
    """

    points: Optional[int]
    max_searches: Optional[int]
    max_per_side: Optional[int]
    max_full: Optional[int]

    @property
    def no_budget(self) -> bool:
        return self.points is None

    @property
    def label(self) -> str:
        return "none" if self.points is None else str(self.points)


# --------------------------------------------------------------------------- the planner's view (D28)


@dataclass
class SectionView:
    """One section an angle cites, labelled by code relative to the angle's side."""

    ref: str                 # canonical "D1.S7"
    label: str               # الموكّل | الخصم | محايد | ?
    owner: str               # "owner، قدّمه brought_by" or "-"
    what: str = ""


@dataclass
class AngleCard:
    """One stored angle as the planner sees it (``attachment_angles`` row + labels)."""

    id: str                  # "A1" … in seq order
    kind: str                # strengthen | attack | answer
    strength: str            # strong | medium | weak
    for_side: str
    for_party: str
    title: str
    argument: str
    legal_question: str
    weakness: str
    ocr_suspect: bool = False
    sections: list[SectionView] = field(default_factory=list)


@dataclass
class DocView:
    """One attached document of the map (``attachment_angle_map_items`` member)."""

    id: str                  # "D1"
    item_id: str
    title: str
    doc_type: str
    summary: str             # the summarizer's summary (analysis) — D29: every search gets it
    content_chars: int
    #: The whole document, every paragraph, with a code-written marker line at each section
    #: start: 〔D1·S7 · owner، قدّمه … · جوهري〕 (D17). "" when the segment map is missing —
    #: the runner then delivers ``content_md`` unmarked.
    marked_text: str = ""
    #: ``(seq, char_start, char_end)`` of every section marked in ``marked_text``, in
    #: ``seq`` order — positions on the stored text, as the ordinary deep search records
    #: them for a file tag (aggregator_file_refs.md §4.1). ``()`` when nothing is marked.
    section_spans: tuple[tuple[int, int, int], ...] = ()


@dataclass
class PlannerView:
    """Everything the extended planner and the searches need from a stored angle map."""

    map_id: str
    message_id: Optional[str]
    documents: list[DocView]
    angles: list[AngleCard]
    #: The map's client (side / party / by_document) — used by code for labels only
    #: (D28: not shown to the planner).
    map_client: dict[str, Any] = field(default_factory=dict)
    #: Every canonical section ref of the map's documents ("D1.S1" …) — the validator's set.
    section_refs: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- planner output (LLM)


class ExtendedRequest(BaseModel):
    title: str = Field(..., description="Short Arabic line the user reads on the plan card (≤ ~70 chars).")
    angles: list[str] = Field(default_factory=list, description="Angle ids (A1…).")
    sections: list[str] = Field(default_factory=list, description="Section refs (D1.S7…).")
    mode: SearchMode = "reg_compliance_led"
    max_sub_queries: Optional[int] = Field(
        None, ge=1,
        description="Most sub-queries per side this search runs. Required under a budget mode; may be null under no budget (= uncapped as today).",
    )
    focus: str
    why: str = ""


class CheaperOption(BaseModel):
    note: str = Field(..., description="One Arabic sentence, first person, no points or prices.")
    requests: list[ExtendedRequest] = Field(..., min_length=1)


class LeftOut(BaseModel):
    angle: str
    why: str = ""


class ExtendedPlan(BaseModel):
    understanding: str
    client_side: str = "unknown"
    requests: list[ExtendedRequest] = Field(..., min_length=1)
    cheaper: Optional[CheaperOption] = None
    left_out: list[LeftOut] = Field(default_factory=list)
    rationale: str = ""


class ExtendedSearchPlan(ExtendedPlan):
    """«بحث شامل» — two or more searches run at the same time, shown to the user as a
    plan card first. Only when the angles the answer depends on turn on at least two
    different legal questions; one search is a ``PlannerDecision``."""

    request_quotes: list[str] = Field(
        default_factory=list,
        description=(
            "Same rule as the decision's `request_quotes`: only when the latest message "
            "cannot be searched on its own, the passages of earlier messages that carry "
            "the request, each copied character for character. Empty otherwise."
        ),
    )


# --------------------------------------------------------------------------- the plan card (mirror of extended-plan.ts)


class PlanSearchView(BaseModel):
    title: str
    sides: PlanSearchSides


class PlanOptionView(BaseModel):
    id: PlanOptionId
    label: str
    points: int
    searches: list[PlanSearchView] = Field(default_factory=list)


class ExtendedPlanCard(BaseModel):
    """``messages.metadata.extended_plan`` — see frontend/lib/contracts/extended-plan.ts."""

    version: Literal[1] = 1
    run_id: str
    #: Points left (tightest window), floored; None = unlimited account (gate ``remaining``
    #: None) — the card then omits «المتبقي لك». Mirrors ``points_left: number | null`` in
    #: extended-plan.ts. (Lead 11-24: replaces A's ``points_unlimited`` flag — one signal only.)
    points_left: Optional[int] = None
    options: list[PlanOptionView]
    cheaper_note: Optional[str] = None
    cheaper_saving: Optional[int] = None
    outcome: Optional[PlanOutcome] = None
    outcome_at: Optional[str] = None


# --------------------------------------------------------------------------- the stored pause (server only)


class StoredOption(BaseModel):
    """One runnable option, priced by code."""

    id: PlanOptionId
    requests: list[ExtendedRequest] = Field(default_factory=list)   # [] for "ordinary"
    estimate_points: float


class ExtendedPlanPause(BaseModel):
    """``paused_runs.deferred_payload["extended_plan"]`` — what a button resumes without re-planning."""

    run_id: str
    map_id: str
    budget_mode: str                     # BudgetMode.label: "15" … "50" | "none"
    points_left: Optional[float] = None
    describe_query: str                  # the user's message as the turn received it (intake-encoded)
    user_message_id: Optional[str] = None
    attached_item_ids: list[str] = Field(default_factory=list)
    task_label: str = ""
    plan: ExtendedPlan                   # decoded planner output
    options: list[StoredOption]          # "plan", ["cheaper"], "ordinary"
    planner_points: float = 0.0
    created_at: str = ""


# --------------------------------------------------------------------------- execution


@dataclass
class ExtendedSearchReport:
    """One finished (or failed) search of a plan."""

    index: int                           # 1-based, plan order
    request: ExtendedRequest
    ok: bool
    agg_output: Any = None               # aggregator.models.AggregatorOutput when ok
    error: str = ""
    seconds: float = 0.0
    item_id: Optional[str] = None        # its agent_search workspace item, once published
    # Read back off the search's own PlannerDeps by the runner (execution side) —
    # what publishing needs for the forensic rows and the reference rows.
    query_id: int = 0                    # the search's distinct query id (log ids)
    sources: int = 0                     # results its executors kept (progress total)
    ura: Any = None                      # UnifiedRetrievalArtifact
    reg_rqrs: list = field(default_factory=list)
    case_rqrs: list = field(default_factory=list)
    per_executor_stats: dict = field(default_factory=dict)
    # The documents every search of the plan read (the same list on every report) and,
    # with the file refs on, what each may cite (``FileRefDoc``; ``[]`` = off). The
    # merger reads the same documents under the same tags (aggregator_file_refs.md).
    documents: list = field(default_factory=list)
    file_ref_docs: list = field(default_factory=list)


class Resolution(BaseModel):
    topic: str = Field(..., description="Arabic, short: what the reports disagreed on.")
    reports: list[int] = Field(default_factory=list, description="Report numbers that disagreed (1-based).")
    decision: str = Field(..., description="Arabic, one or two sentences: what the merged answer says and on what basis.")


class MergedAnswer(BaseModel):
    """The merger's result after code renumbered its citations 1..k."""

    synthesis_md: str
    references: list[dict[str, Any]] = Field(default_factory=list)   # aggregator Reference dumps, renumbered
    gaps: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    resolved: list[Resolution] = Field(default_factory=list)
    report_titles: list[str] = Field(default_factory=list)          # the reports it used, plan order (D14)
    # The merger's whole output (``extended_merger.models.MergerOutput`` dump: angle
    # scoreboard, settled contradictions, angles left out, the reports) — the merged
    # item's publisher reads it. ``None`` on a single report's answer (no merged item).
    merger: Optional[dict[str, Any]] = None


__all__ = [
    "AngleCard",
    "BudgetMode",
    "CARD_METADATA_KEY",
    "CheaperOption",
    "DocView",
    "ExtendedPlan",
    "ExtendedPlanCard",
    "ExtendedPlanPause",
    "ExtendedRequest",
    "ExtendedSearchPlan",
    "ExtendedSearchReport",
    "LeftOut",
    "MergedAnswer",
    "OPTION_LABELS_AR",
    "PAUSE_REASON",
    "PlanOptionId",
    "PlanOptionView",
    "PlanOutcome",
    "PlanSearchSides",
    "PlanSearchView",
    "PlannerView",
    "Resolution",
    "SIDES_OF_MODE",
    "SearchMode",
    "SectionView",
    "StoredOption",
]
