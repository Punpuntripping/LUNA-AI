"""Schemas + input contract for the unified responder.

Plan ``.claude/plans/unified_responder.md`` §4.4 (output) and §5.4 (input).

Output: one shared base (:class:`ResponderBase` — the voice) plus one subclass
per mode carrying that family's DECISION fields (D3): the deep_search publish
gate, the simple_search card verdicts, nothing extra for writing. Each mode's
Agent sees only its own schema (smaller tool schema, stable prefix);
:data:`ResponderOutput` is the discriminated union for typing and tests.

Input: :class:`ResponderInput` — the turn package one family hands
:func:`agents.responder.runner.run_responder`. It carries the router's context
object VERBATIM (D4 — never reloaded, never re-encoded), a narrower fallback
for headless dispatches, and one artifact package per mode (D5) that includes
the producing agent's internal notes (aggregator gaps / writer notes_ar /
per-document digests) — the only legitimate source of a follow-up question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from agents.models import MAX_NEXT_STEPS, NextStep

if TYPE_CHECKING:  # pragma: no cover — typing only
    from agents.router.context import RouterContext
    from agents.utils.welcome import WelcomeState


ResponderMode = Literal["deep_search", "simple_search", "writing"]
RESPONDER_MODES: tuple[ResponderMode, ...] = ("deep_search", "simple_search", "writing")

#: deep_search planner modes — the chat-summary framing is keyed on these.
SearchMode = Literal["case_led", "reg_compliance_led", "full"]

#: D6 — the follow-up question is one short sentence (two only when tightly
#: linked); anything longer is a paragraph, not a question.
FOLLOW_UP_QUESTION_MAX = 300

#: Workspace-item titles are capped at 80 chars upstream (``workspace_items.title``
#: is sized for the router's ``task_label``). Same cap the simple_search
#: responder enforces on its card titles.
WI_TITLE_MAX = 80

#: §5.4 — how much writing-draft body the responder may read: the full draft
#: up to this budget, else outline + first/last sections.
RESPONDER_BODY_CHARS = 12_000

#: deep_search synthesis window: its opening (the direct answer) and its close
#: (conclusion / caveats), with a marker between. 12k of synthesis roughly
#: doubled the responder's latency (~74 s vs ~35 s p50 on planner_responder,
#: which read a 1,600-char head) — the card holds the full report anyway.
SYNTHESIS_HEAD_CHARS = 1_500
SYNTHESIS_TAIL_CHARS = 500

#: simple_search per-document digest budget — unchanged from
#: ``simple_search.responder.RESPONDER_EXCERPT_CHARS`` (§5.4).
RESPONDER_EXCERPT_CHARS = 1600


def _coerce_none_string(v: Any) -> Any:
    """LLMs sometimes serialise ``None`` as ``"None"`` / ``"null"`` / ``""``."""
    if v is None:
        return None
    if isinstance(v, str):
        stripped = v.strip()
        if stripped == "" or stripped.lower() in ("none", "null"):
            return None
        return stripped
    return v


# ── Output ───────────────────────────────────────────────────────────────────


class ResponderBase(BaseModel):
    """The voice — shared by every mode."""

    chat_summary_md: str = Field(
        description=(
            "The Arabic message at the top of the chat bubble. Prose, no '##' "
            "headings, no [n]/(n) citation markers, and NEVER the follow-up "
            "question or any offer («إذا تحب…») — those have their own fields."
        ),
    )
    next_steps: list[NextStep] = Field(
        default_factory=list,
        description=(
            f"0–{MAX_NEXT_STEPS} clickable chips. With a follow-up question: up to "
            "2 `answer` chips (the user's half-written reply) + at most 1 action. "
            "Without one: actions only, each a different kind."
        ),
    )
    follow_up_question: str | None = Field(
        default=None,
        description=(
            f"≤ {FOLLOW_UP_QUESTION_MAX} chars. ONE Arabic question (two only when "
            "tightly linked) for a MATERIAL fact that lives in the user's own file "
            "and would change the legal conclusion — with the reason in the same "
            "sentence. null when no such fact is missing. Appended by code as its "
            "own paragraph after chat_summary_md."
        ),
    )
    question_basis: str | None = Field(
        default=None,
        description=(
            "Internal, never shown: which reported gap / note the question "
            "targets (e.g. «gap: شروط العقد»). null when no question."
        ),
    )

    # Telemetry only — set when invalid chips were salvaged instead of retried.
    _next_steps_salvaged: bool = PrivateAttr(default=False)

    @field_validator("follow_up_question", "question_basis", mode="before")
    @classmethod
    def _coerce_none_strings(cls, v: Any) -> Any:
        return _coerce_none_string(v)

    def tracking_output(self) -> dict:
        """Bounded telemetry view (``agents/utils/tracking.py`` protocol).

        Per-turn fields of plan §8 (``responder.mode``, ``asked_question``,
        ``question_basis``, ``chips_n``) — the text itself stays out of spans.
        """
        return {
            "mode": getattr(self, "mode", None),
            "chat_summary_chars": len(self.chat_summary_md or ""),
            "asked_question": bool(self.follow_up_question),
            "question_basis": (self.question_basis or "")[:200],
            "chips_n": len(self.next_steps),
            "chips_kinds": [s.kind for s in self.next_steps],
            "next_steps_salvaged": self._next_steps_salvaged,
        }


class DeepSearchResponse(ResponderBase):
    """deep_search — the voice + the publish gate (maps onto ``PlannerResponse``)."""

    mode: Literal["deep_search"] = "deep_search"
    build_artifact: bool = Field(
        default=True,
        description=(
            "False only when (a) the outcome is the empty-results sentinel, or "
            "(b) a prior high-confidence card in <prior_searches> already answers "
            "this question. Otherwise true."
        ),
    )
    referenced_wi: str | None = Field(
        default=None,
        description=(
            'Alias of the prior card that covers the question (e.g. "WI-3"), '
            "paired with build_artifact=false. Never a UUID."
        ),
    )
    # Resolver-filled from ``referenced_wi`` by the agent's output validator.
    # The LLM must not emit it.
    referenced_item_id: str | None = Field(
        default=None,
        description=(
            "Resolved workspace_items.item_id — filled by the output validator "
            "from referenced_wi. Do not emit."
        ),
    )

    @field_validator("referenced_item_id", "referenced_wi", mode="before")
    @classmethod
    def _coerce_none_ref(cls, v: Any) -> Any:
        return _coerce_none_string(v)


class CardVerdict(BaseModel):
    """One simple_search document's card decision (D1..Dn label, never an id).

    Duplicated from ``agents/simple_search/responder.py`` (plan §5.2 — the
    original is deleted in §9 cleanup, at which point this is the only copy).
    """

    doc: str = Field(
        description=(
            "The document's label exactly as it appears in <documents>: D1, D2, "
            "D3. Never a UUID, a title, or any other identifier."
        ),
    )
    card: bool = Field(
        description=(
            "Whether this answer deserves a durable workspace card. Default "
            "true; false for one-line answers, pointers, not-founds, bodies too "
            "truncated to stand as a document, and anything already delivered "
            "earlier in this turn."
        ),
    )
    title: str = Field(
        default="",
        max_length=WI_TITLE_MAX,
        description=(
            "Short Arabic content-derived card title naming the object "
            f"(≤{WI_TITLE_MAX} chars, no verbs). Empty when card is false."
        ),
    )


class SimpleSearchResponse(ResponderBase):
    """simple_search — the voice + one card verdict per dispatched document."""

    mode: Literal["simple_search"] = "simple_search"
    cards: list[CardVerdict] = Field(
        default_factory=list,
        description=(
            "One verdict per document label shown in <documents>. A label with "
            "no verdict defaults to no card."
        ),
    )

    def verdict_for(self, doc: str) -> CardVerdict | None:
        """This document's verdict, or ``None`` (= no card) when not ruled on."""
        key = str(doc or "").strip().upper()
        if not key:
            return None
        for verdict in self.cards:
            if str(verdict.doc or "").strip().upper() == key:
                return verdict
        return None


class WritingResponse(ResponderBase):
    """writing — the voice only (the draft is already published)."""

    mode: Literal["writing"] = "writing"


ResponderOutput = Annotated[
    Union[DeepSearchResponse, SimpleSearchResponse, WritingResponse],
    Field(discriminator="mode"),
]

OUTPUT_TYPES: dict[ResponderMode, type[ResponderBase]] = {
    "deep_search": DeepSearchResponse,
    "simple_search": SimpleSearchResponse,
    "writing": WritingResponse,
}


# ── Input: artifact packages (§5.4) ──────────────────────────────────────────


@dataclass
class PriorSearch:
    """A prior search card — what the deep_search publish gate needs to judge
    «a prior card already answers this». Duck-type compatible with the
    planner's ``PriorSearchSnapshot`` (any object with these attributes works).
    """

    wi_seq: int | None
    title: str = ""
    describe_query: str = ""
    confidence: str = "medium"
    summary: str = ""


@dataclass
class DeepSearchArtifact:
    """deep_search turn package: the aggregator's product + its internal notes.

    ``synthesis_md is None`` together with ``degraded=True`` means retrieval
    produced nothing — the responder writes an honest "did not finish" line and
    the gate must be false.
    """

    query: str
    search_mode: SearchMode = "reg_compliance_led"
    synthesis_md: str | None = None
    confidence: str = "medium"
    gaps: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    planner_brief: str = ""
    #: ``{"regulations": n, "compliance": n, "cases": n}``
    ref_counts: dict[str, int] = field(default_factory=dict)
    prior_searches: list[Any] = field(default_factory=list)
    degraded: bool = False


@dataclass
class DocDigest:
    """ONE answered simple_search document, reduced to what the responder needs.

    Same fields as ``simple_search.models.ResponderDocDigest`` (which moves here
    in §9 cleanup); the renderer duck-types, so either class works.
    """

    label: str
    level: str
    object_title: str
    excerpt: str
    body_chars: int
    truncated: bool = False
    summary_payload: bool = False
    already_delivered: bool = False


@dataclass
class SimpleSearchArtifact:
    """simple_search turn package: per-document digests + the turn's shape."""

    question: str
    docs: list[Any] = field(default_factory=list)
    dispatched: int = 0
    unselected_candidates: list[str] = field(default_factory=list)
    #: The pause leg — a clarifying question follows immediately, so no chips
    #: and no follow-up question this turn (the runner forces both empty).
    suppress_next_steps: bool = False


@dataclass
class WritingArtifact:
    """writing turn package: the planner's decision + the executor's product.

    Planner side (``writer_planner`` ``PlannerDecision``): ``intent_ar``,
    ``plan_md``, ``chosen_template``, ``parties``, ``edit_mode``, ``subtype``.
    Executor side (``WriterLLMOutput``): ``title_ar``, ``draft_md``,
    ``notes_ar``, ``confidence``, ``key_findings`` (appended to the bubble by
    code — the responder must not repeat them), and the placeholders the draft
    left unfilled.
    """

    intent_ar: str
    title_ar: str = ""
    draft_md: str = ""
    subtype: str = "memo"
    edit_mode: str = "fresh"
    plan_md: str = ""
    chosen_template: str | None = None
    parties: list[Any] = field(default_factory=list)
    notes_ar: list[str] = field(default_factory=list)
    confidence: str = "medium"
    key_findings: list[str] = field(default_factory=list)
    unfilled_placeholders: list[str] = field(default_factory=list)
    #: ``WI-{n}`` alias of the published draft, when known.
    item_wi: str | None = None


ArtifactPackage = Union[DeepSearchArtifact, SimpleSearchArtifact, WritingArtifact]

_ARTIFACT_FOR_MODE: dict[ResponderMode, type] = {
    "deep_search": DeepSearchArtifact,
    "simple_search": SimpleSearchArtifact,
    "writing": WritingArtifact,
}


@dataclass
class ResponderInput:
    """Everything one responder call needs — built by the family runner.

    Attributes:
        mode: which mode block + schema to run.
        artifact: the mode's artifact package (must match ``mode``).
        router_context: the router's ``RouterContext`` for this turn, passed
            through VERBATIM (D4). Already encoded with the turn codec — never
            re-encode, never reload. ``None`` on headless / pinned dispatches.
        fallback_context: pre-rendered narrower context used ONLY when
            ``router_context`` is None (e.g. the planner's
            ``render_responder_context(deps)`` output) — today's behaviour.
        welcome: the turn's ``WelcomeState`` (``agents/utils/welcome.py``), or
            None on every turn that opens with no greeting.
        welcome_instruction: the same greeting pre-rendered (wins when set).
        profession_group: ``users.profession_group`` (D9 — «موكلك» wording only
            when ``"legal"`` or the user said so).
        style_profile: the rendered style profile (P4 — ``load_style_profile_md``).
        follow_up_status: whether the previous reply's follow-up question was
            answered (§6.1 rule 7) — see :meth:`effective_follow_up_status`.
        conversation_id / case_id: span identity (``tracking._identity_from_deps``).
        wi_alias_map: ``wi_seq → item_id`` for resolving deep_search
            ``referenced_wi``.
    """

    mode: ResponderMode
    artifact: Any
    router_context: "RouterContext | None" = None
    fallback_context: str | None = None
    welcome: "WelcomeState | None" = None
    #: The ALREADY-RENDERED welcome block (``render_welcome_instruction``
    #: output), for callers that only carry the rendered string
    #: (``PlannerDeps.welcome_instruction``, ``MajorAgentInput``). Wins over
    #: ``welcome`` when set; same bytes either way.
    welcome_instruction: str | None = None
    profession_group: str | None = None
    style_profile: str | None = None
    #: §6.1 rule 7 (no nag): the status of the follow-up question asked under
    #: the PREVIOUS assistant reply — ``None`` (none asked), ``"answered"`` or
    #: ``"unanswered"`` (the user moved on). ``None`` here defers to
    #: ``router_context.follow_up_status`` (stamped by ``orchestrator._route``
    #: after the router decided) — see :meth:`effective_follow_up_status`.
    follow_up_status: str | None = None
    conversation_id: str = ""
    case_id: str | None = None
    wi_alias_map: dict[int, str] = field(default_factory=dict)

    def effective_follow_up_status(self) -> str | None:
        """Explicit ``follow_up_status`` wins; else the router context's."""
        status = self.follow_up_status or getattr(
            self.router_context, "follow_up_status", None
        )
        return status if status in ("answered", "unanswered") else None

    def __post_init__(self) -> None:
        if self.mode not in RESPONDER_MODES:
            raise ValueError(f"unknown responder mode {self.mode!r}")
        expected = _ARTIFACT_FOR_MODE[self.mode]
        if not isinstance(self.artifact, expected):
            raise TypeError(
                f"mode {self.mode!r} needs a {expected.__name__}, "
                f"got {type(self.artifact).__name__}"
            )


__all__ = [
    "ResponderMode",
    "RESPONDER_MODES",
    "SearchMode",
    "FOLLOW_UP_QUESTION_MAX",
    "WI_TITLE_MAX",
    "RESPONDER_BODY_CHARS",
    "SYNTHESIS_HEAD_CHARS",
    "SYNTHESIS_TAIL_CHARS",
    "RESPONDER_EXCERPT_CHARS",
    "ResponderBase",
    "DeepSearchResponse",
    "CardVerdict",
    "SimpleSearchResponse",
    "WritingResponse",
    "ResponderOutput",
    "OUTPUT_TYPES",
    "PriorSearch",
    "DeepSearchArtifact",
    "DocDigest",
    "SimpleSearchArtifact",
    "WritingArtifact",
    "ArtifactPackage",
    "ResponderInput",
]
