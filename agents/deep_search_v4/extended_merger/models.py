"""Contracts of the extended-search merger («بحث موسّع» — the report merger).

Plan: ``.claude/plans/extended_search.md`` §4.6 (the merger), §4.7 (publishing),
§4.9 c (strengths and weaknesses), D4 / D14 / D19.

An extended search runs several searches at the same time, each on one or more
ANGLES (أوجه) of the user's file chosen by the angle-aware planner; each search
ends in its own aggregator report (``AggregatorOutput``) published as its own
``agent_search`` workspace item. The merger reads those reports — with the
angles and the planner's request behind each one, the user's whole file and
every document's summary — and writes the ONE answer the user reads first.

Three groups:

- what CODE hands the merger (``MergerInput`` and its parts) — built by the
  extended-search runner (not built yet) or the offline harness;
- what the LLM returns (``MergerLLMOutput``) — the merged answer plus the
  angle scoreboard (``AngleStatus``) and the settled contradictions
  (``Resolution``);
- what the merger returns to its caller (``MergerOutput``) and what the
  publisher needs to write the merged workspace item (``MergedPublishInput``).

The merged item is an ``agent_search`` workspace item with
``metadata.subtype = "extended_synthesis"`` (owner, 2026-11-24: a new SUBTYPE,
not a new DB kind — every consumer of ``agent_search`` keeps working).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field

from agents.deep_search_v4.aggregator.models import Reference

if TYPE_CHECKING:
    from agents.deep_search_v4.aggregator.models import AggregatorOutput
    from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact


#: ``workspace_items.metadata.subtype`` of the merged answer.
SUBTYPE = "extended_synthesis"

# ---------------------------------------------------------------------------
# Vocabularies + the Arabic labels code writes (never the model)
# ---------------------------------------------------------------------------

#: Angle kinds (the angle map's ``KINDS``): ``strengthen`` / ``attack`` = a
#: strength (نقطة قوة) of the side in ``for_side``; ``answer`` = a weakness of
#: that side — the opponent's strong point it must answer.
KIND_AR: dict[str, str] = {"strengthen": "يقوّي", "attack": "يطعن", "answer": "يجب الرد"}
STRENGTH_AR: dict[str, str] = {"strong": "قوي", "medium": "متوسط", "weak": "ضعيف"}
#: A search's side(s) — no ``support`` (plan D21).
MODE_AR: dict[str, str] = {
    "reg_compliance_led": "الأنظمة",
    "case_led": "الأحكام",
    "full": "الأنظمة والأحكام",
}

AngleState = Literal["supported", "answered", "open", "against", "not_researched"]

#: The scoreboard's statuses (``AngleStatus.status``):
#: - ``supported``      — a strength (strengthen / attack) the research backs with a
#:                        legal basis applied to the file;
#: - ``answered``       — an opponent's point (answer) or a strength's weakness the
#:                        research found an answer to;
#: - ``open``           — no source settles it; the note says what would decide it;
#: - ``against``        — the research cuts against the user here: a strength the
#:                        file or the law defeats, or an opponent's point that
#:                        stands with a basis and no answer;
#: - ``not_researched`` — its search failed, or no report actually dealt with it.
ANGLE_STATES: tuple[str, ...] = ("supported", "answered", "open", "against", "not_researched")
ANGLE_STATE_AR: dict[str, str] = {
    "supported": "ثابت",
    "answered": "له ردّ",
    "open": "مفتوح",
    "against": "في غير صالح الموكّل",
    "not_researched": "لم يُبحث",
}


# ---------------------------------------------------------------------------
# Input — built by code
# ---------------------------------------------------------------------------


@dataclass
class MergerAngle:
    """One angle card as the planner and the searches saw it (angle map + code labels).

    ``id`` is the id the planner used for the angle (``A3``). ``sections`` are
    display strings already labelled by code — e.g. ``"D1.S7 (الموكّل: المدعي)"``
    — the same text the search's ``planner_brief`` carried (plan §4.9 b).
    Everything here is machine analysis, never the file's words.
    """

    id: str
    kind: str  # strengthen | attack | answer
    strength: str  # strong | medium | weak
    title: str
    argument: str
    legal_question: str
    weakness: str = ""
    for_side: str = "unknown"
    for_party: str = ""
    sections: list[str] = field(default_factory=list)
    ocr_suspect: bool = False


@dataclass
class MergerReport:
    """One search of the plan and the report its aggregator wrote.

    ``output`` is the aggregator's final ``AggregatorOutput``: its
    ``synthesis_md`` cites ``[n]`` against its OWN ``references`` (the merger's
    code renumbers them into one shared list before the model sees them).
    ``output=None`` = the search failed — the report is still listed so the
    merger knows its angles were not researched.
    """

    title: str  # the planner's request title
    mode: str  # reg_compliance_led | case_led | full
    focus: str  # the planner's focus for this search
    angle_ids: list[str] = field(default_factory=list)
    why: str = ""
    output: Optional["AggregatorOutput"] = None
    item_id: Optional[str] = None  # its own published agent_search item (D14)
    ura: Optional["UnifiedRetrievalArtifact"] = None  # compliance refs need it at persist time

    @property
    def ok(self) -> bool:
        return self.output is not None


@dataclass
class MergerDocument:
    """One document of the turn, as the searches read it.

    ``text`` = the file's own words with code-written section markers
    (``〔D1·S7 · owner، قدّمه … · value〕``) — no model text (plan §2.3 rule).
    ``summary`` = the summarizer's analysis (labelled as such in the prompt).
    """

    title: str
    doc_type: str = ""
    summary: str = ""
    text: str = ""
    item_id: Optional[str] = None
    ref: str = ""  # the angle map's document number ("D1") — angle sections read "D1.S7"


@dataclass
class LeftOutAngle:
    """An angle the planner chose not to search, with its reason (``AnglePlan.left_out``)."""

    angle_id: str
    why: str = ""


@dataclass
class MergerInput:
    """Everything code hands the merger."""

    query: str  # the user's message, VERBATIM
    reports: list[MergerReport]  # in the plan's order (report n = index + 1)
    angles: dict[str, MergerAngle] = field(default_factory=dict)  # every angle the plan names, by id
    documents: list[MergerDocument] = field(default_factory=list)
    recent_messages: list[Any] = field(default_factory=list)  # objects / dicts with role + content
    understanding: str = ""  # the planner's understanding (2–3 Arabic sentences)
    client_side: str = "unknown"  # the planner's side (wins over the map's)
    client_party: str = ""
    left_out: list[LeftOutAngle] = field(default_factory=list)
    conversation_memory: str = ""  # the rendered memory block the searches got, if any
    detail_level: Literal["low", "medium", "high"] = "medium"


# ---------------------------------------------------------------------------
# LLM output — no class docstrings on purpose (pydantic would put them in the schema)
# ---------------------------------------------------------------------------


class AngleStatus(BaseModel):
    angle: str = Field(..., description="The angle id from <angles>, e.g. A3.")
    status: AngleState = Field(..., description="supported | answered | open | against | not_researched")
    note: str = Field(..., description="Arabic, one sentence: the basis for the status, or what would decide an open angle.")


class Resolution(BaseModel):
    topic: str = Field(..., description="Arabic, short: what the reports disagreed on.")
    reports: list[int] = Field(default_factory=list, description="Numbers of the reports that disagreed (1-based).")
    decision: str = Field(..., description="Arabic, one or two sentences: what the merged answer says and on what basis.")
    settled: bool = Field(
        True,
        description="false when neither the file nor the references settle it and the answer presents both positions.",
    )


class MergerLLMOutput(BaseModel):
    angle_status: list[AngleStatus] = Field(default_factory=list, description="One entry per angle of the reports.")
    resolved: list[Resolution] = Field(default_factory=list, description="Every disagreement between reports and how it was handled.")
    synthesis_md: str = Field(..., description="The merged answer, Arabic markdown with [n] citations from <references>.")
    used_refs: list[int] = Field(default_factory=list, description="Reference numbers cited in synthesis_md.")
    gaps: list[str] = Field(default_factory=list, description="Arabic: what the research could not establish (merged, no repeats).")
    confidence: Literal["high", "medium", "low"] = Field(..., description="Coverage of the user's question as a whole.")


# ---------------------------------------------------------------------------
# Merger result — returned by the runner, consumed by the publisher
# ---------------------------------------------------------------------------


class MergedReportEntry(BaseModel):
    """One report the merged answer was built from (D14) — for ``metadata.reports``."""

    n: int  # 1-based, the plan's order
    title: str
    mode: str
    angles: list[str] = Field(default_factory=list)
    item_id: Optional[str] = None
    confidence: Optional[str] = None
    ok: bool = True


class AngleCard(BaseModel):
    """The angle card the WI's scoreboard renders next to its status (its full card
    opens on tap — machine analysis, labelled as such by the UI)."""

    id: str
    kind: str
    strength: str
    title: str
    for_party: str = ""
    for_side: str = "unknown"  # owner key — with client_side it tells whose strength the angle is
    argument: str = ""
    legal_question: str = ""
    weakness: str = ""
    sections: list[str] = Field(default_factory=list)  # code-labelled display strings
    ocr_suspect: bool = False


class LeftOutEntry(BaseModel):
    angle: str
    title: str = ""
    why: str = ""


class MergerOutput(BaseModel):
    """The merged answer, renumbered and ready to publish."""

    synthesis_md: str  # citations renumbered 1..k in order of first citation
    references: list[Reference] = Field(default_factory=list)  # only the cited ones, n = 1..k
    ref_reports: dict[int, list[int]] = Field(default_factory=dict)  # final n -> report numbers that cited it
    confidence: Literal["high", "medium", "low"]
    gaps: list[str] = Field(default_factory=list)
    angle_status: list[AngleStatus] = Field(default_factory=list)
    resolved: list[Resolution] = Field(default_factory=list)
    angles: list[AngleCard] = Field(default_factory=list)  # the reports' angles, plan order
    left_out: list[LeftOutEntry] = Field(default_factory=list)
    reports: list[MergedReportEntry] = Field(default_factory=list)
    disclaimer_ar: str = ""
    model_used: str = ""
    prompt_version: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)  # shared_refs, dropped_tags, input_chars, …


@dataclass
class MergedPublishInput:
    """Everything the publisher needs to write the merged workspace item."""

    user_id: str
    conversation_id: str
    output: MergerOutput
    original_query: str
    detail_level: str = "medium"
    case_id: Optional[str] = None
    message_id: Optional[str] = None
    task_label: Optional[str] = None  # router-emitted title, preferred
    describe_query: Optional[str] = None
    # URA result ROWS of every report (``ura.high_results + ura.medium_results``),
    # flattened — compliance refs need their ``service_ref`` at persist time.
    ura_results: list[Any] = field(default_factory=list)
    client_side: str = "unknown"  # the planner's side — stored so the UI can read each angle's for_side
    run_id: Optional[str] = None  # the extended-search run (metadata only)


__all__ = [
    "ANGLE_STATES",
    "ANGLE_STATE_AR",
    "KIND_AR",
    "MODE_AR",
    "STRENGTH_AR",
    "SUBTYPE",
    "AngleCard",
    "AngleStatus",
    "LeftOutAngle",
    "LeftOutEntry",
    "MergedPublishInput",
    "MergedReportEntry",
    "MergerAngle",
    "MergerDocument",
    "MergerInput",
    "MergerLLMOutput",
    "MergerOutput",
    "MergerReport",
    "Resolution",
]
