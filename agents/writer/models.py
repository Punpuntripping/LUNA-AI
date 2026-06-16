"""Pydantic models for the agent_writer agent.

agent_writer drafts long-form Arabic legal documents (contract, memo,
defense brief, legal opinion, letter, summary) and publishes them as
workspace_items of kind='agent_writing' -- which the user can subsequently
edit (lock semantics defined in Wave 8 plan section "Permission
enforcement").

Inputs:
    - user request (free-form Arabic text)
    - optional research evidence (one or more agent_search workspace_items
      previously published by the deep_search_v4 pipeline)
    - workspace context (visible notes, attachments, convo_context)

Output: a workspace_item with structured Arabic markdown body that the user
can edit collaboratively (turn-locked).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


WriterSubtype = Literal[
    "contract",         # عقد
    "memo",             # مذكّرة قانونية
    "legal_opinion",    # رأي قانوني
    "defense_brief",    # مذكّرة دفاع
    "letter",           # خطاب رسمي
    "summary",          # ملخّص (e.g. summarizing an attachment)
]


# ---------------------------------------------------------------------------
# LLM-facing structured output
# ---------------------------------------------------------------------------


class CitationRef(BaseModel):
    """One disambiguated citation pick from the writer.

    `wi` is the WI-{seq} alias of the source WI; `n` is the reference number
    inside that WI (as it appears in workspace_item_references.n).
    """

    wi: str = Field(description="WI-{seq} alias of the source WI, e.g. 'WI-2'.")
    n: int = Field(ge=1, description="The [n] reference number inside that WI.")


class WriterSection(BaseModel):
    """One section of the drafted document."""

    heading_ar: str = Field(description="The section heading in Arabic (## level)")
    body_md: str = Field(description="The section text in Arabic markdown")


class WriterLLMOutput(BaseModel):
    """Raw structured output from the writer LLM.

    The runner concatenates sections into a single content_md before
    persisting. Keeping sections separate at the LLM layer lets us
    enforce per-section length limits and run lightweight per-section
    validation (e.g. each section must be >= 50 chars).
    """

    title_ar: str = Field(description="The full document title (in Arabic)")
    sections: list[WriterSection] = Field(
        ...,
        description="The order of the sections as they appear in the final document",
    )
    citations_used: list[CitationRef] = Field(
        default_factory=list,
        description=(
            "Every actual citation that appeared in body_md as (n) — written as a "
            "pair {wi: \"WI-N\", n: K} that pinpoints the source precisely. The number "
            "n is the same one shown in body_md; the wi field identifies the source "
            "workspace item (from <source wi=\"WI-N\"> inside the writing package) to "
            "remove ambiguity when the same n overlaps across more than one source."
        ),
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="The writer's estimate of the draft's quality"
    )
    notes_ar: list[str] = Field(
        default_factory=list,
        description="Notes for the user (in Arabic): points needing review, gaps in the inputs",
    )
    chat_summary: str = Field(
        default="",
        description=(
            "One or two Arabic sentences introducing the drafted document for the "
            "brief inline display in chat. 500 characters maximum."
        ),
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description=(
            "The top 3 to 5 points (in Arabic) the user should review or pay "
            "attention to in the document — 5 items maximum, each a short Arabic sentence."
        ),
    )

    def tracking_output(self) -> dict:
        """Bounded telemetry view (agents/utils/tracking.py protocol) — keeps the
        full document body (sections/body_md) out of span attributes."""
        return {
            "title_chars": len(self.title_ar or ""),
            "sections": len(self.sections),
            "citations": len(self.citations_used),
            "confidence": self.confidence,
            "notes": len(self.notes_ar),
            "key_findings": len(self.key_findings),
        }


# ---------------------------------------------------------------------------
# Orchestrator-facing dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceContextBlock:
    """Trimmed-down view of workspace items the writer can read.

    Built by the orchestrator (or unit tests) from
    backend.app.services.workspace_context.load_workspace_context.
    Kept dataclass-shape so callers can pass either this OR a plain dict
    interchangeably -- we duck-type access via .get() in the prompt builder.
    """

    notes: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    convo_context: Optional[dict] = None


@dataclass
class WriterInput:
    """Everything agent_writer needs to draft a document."""

    user_id: str
    conversation_id: str
    case_id: Optional[str]
    message_id: Optional[str]
    user_request: str            # The user's drafting brief (from router DispatchAgent.describe_query)
    subtype: WriterSubtype = "memo"
    # Optional research evidence -- agent_search items the user (or router)
    # selected to ground the writing on. Each entry is the loaded dict from
    # workspace_items where kind='agent_search'.
    research_items: list[dict] = field(default_factory=list)
    # Existing workspace context -- notes, attachments, convo_context.
    # Loaded by backend.app.services.workspace_context.load_workspace_context.
    # Accepts either a WorkspaceContextBlock or a plain dict shaped the same.
    workspace_context: Any | None = None
    # If editing an existing draft, its item_id (writer reads, revises,
    # writes a NEW row -- versioning via soft-delete of old, no in-place edit).
    revising_item_id: Optional[str] = None
    # Stylistic prefs (from user_preferences).
    detail_level: Literal["low", "medium", "high"] = "medium"
    tone: Literal["formal", "neutral", "concise"] = "formal"


class WriterOutput(BaseModel):
    """What handle_writer_turn returns to the orchestrator."""

    item_id: str
    kind: Literal["agent_writing"] = "agent_writing"
    subtype: WriterSubtype
    title: str
    content_md: str
    confidence: Literal["high", "medium", "low"]
    notes: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    sse_events: list[dict] = Field(default_factory=list)
    locked_until: Optional[str] = Field(
        default=None,
        description="ISO timestamp; None after unlock or on failure path.",
    )
    chat_summary: str = Field(
        default="",
        description=(
            "Short Arabic summary (≤ 500 chars) for inline chat display. "
            "Copied from WriterLLMOutput.chat_summary."
        ),
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 5 Arabic bullet-point findings for inline chat display. "
            "Copied from WriterLLMOutput.key_findings."
        ),
    )


# ---------------------------------------------------------------------------
# WriterPackage — the planner's payload to the writing executor
# ---------------------------------------------------------------------------
#
# These types are shared by the writer_planner (.claude/plans/writer_planner.md)
# and the executor's runner / prompt renderer. They live here (not in
# planner/models.py) because the executor needs them to consume packages and
# they're considered part of the executor's public input contract.
#
# Key invariant: `body_md` in an AnalyzedItem is the LITERAL text the executor
# will draft from -- either the workspace_item's full `content_md` (when
# `need='full'`) or the item_analyzer's distilled slice (when `need='partial'`).
# The planner never gates on word_count; the analyzer decides full/partial.


class AnalyzedItem(BaseModel):
    """One workspace_item the planner included in the WriterPackage.

    Built either by:
      - The verdict-walk algorithm (when item_analyzer was invoked) — the
        planner translates each non-`none` analyzer verdict into one of these.
      - The bypass path (when item_analyzer was skipped because the relevant
        items were already unambiguous) — the planner sets `need='full'` and
        `body_md = wi.content_md` directly.

    Verdict-`none` items never reach this class; they're dropped upstream.
    """

    item_id: str
    wi_seq: int | None = Field(
        default=None,
        description=(
            "WI-{seq} sequence number from workspace_items.wi_seq (migration 052). "
            "None only for items predating the migration or items without a conversation_id."
        ),
    )
    title: str
    kind: str = Field(
        description="Original workspace_items.kind (e.g. agent_search, agent_writer, attachment, notes)"
    )
    role: Literal["template", "source", "reference", "prior_draft"] = Field(
        description=(
            "Role assigned by the planner based on user wording + item title + "
            "item summary. Drives how the executor positions this item in the "
            "rendered prompt and how it uses the content."
        )
    )
    need: Literal["full", "partial"] = Field(
        description=(
            "How body_md was produced. 'full' = the workspace_item's entire "
            "content_md is in body_md (raw passthrough). 'partial' = an "
            "item_analyzer-distilled slice. 'none' verdicts are not built."
        )
    )
    body_md: str = Field(
        description="The text the executor drafts from — either raw content_md or distilled."
    )
    word_count_before: int = Field(
        ge=0,
        description="Original workspace_items.word_count (from migration 048).",
    )
    word_count_after: int = Field(
        ge=0,
        description=(
            "Word count of body_md. Equals word_count_before when need='full', "
            "lower when need='partial'."
        ),
    )

    # Refs-family partial only (kinds: agent_search, agent_writer).
    # When the analyzer returns need='partial' for a refs-family WI, it lists
    # which [n] reference tokens the distilled slice still depends on. The
    # planner resolves these via references_service.fetch_item_references and
    # renders them into resolved_refs_md.
    refs_needed: list[int] = Field(
        default_factory=list,
        description="[n] reference numbers the distilled body_md cites — refs-family partials only.",
    )
    resolved_refs_md: str | None = Field(
        default=None,
        description="Rendered string of the resolved references — refs-family partials only.",
    )

    # Meta-family partial only (kinds: attachment, notes).
    # When the analyzer returns need='partial' for a meta-family WI, it can
    # also extract verbatim key/value facts (parties, dates, amounts...) into
    # this dict. The executor's prompt renders these inside <facts>...</facts>.
    extracted_metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Verbatim key/value facts from a meta-family partial verdict.",
    )


class TemplateRef(BaseModel):
    """A template the planner chose for the executor to mimic.

    Carries one of the user's قوالبي (``user_templates``) rows the planner
    picked to draft from. ``template_type`` and ``score`` are optional —
    قوالبي rows have no type enum and no similarity score (titles are picked
    directly, not semantically ranked). The planner's prompt covers the
    no-template path («إن لم توجد قوالب، أنشئ هيكلاً مناسباً للنوع دون
    الاعتماد على قالب»).
    """

    template_id: str
    template_type: str | None = Field(
        default=None,
        description="Optional category label; None for قوالبي rows (no type enum).",
    )
    title: str
    body_md: str = Field(description="The full template content, ready to mimic.")
    score: float | None = Field(
        default=None,
        description="Optional similarity score (telemetry only); None for قوالبي picks.",
    )


class WriterStyle(BaseModel):
    """Stylistic preferences resolved from user_preferences."""

    detail_level: Literal["low", "medium", "high"] = "medium"
    tone: Literal["formal", "neutral", "concise"] = "formal"


class CaseParty(BaseModel):
    """One confirmed party in the case, validated by the planner before drafting."""

    name: str = Field(description="The full name of the party or the name of the body")
    role: str = Field(
        description=(
            "The party's role in the case — e.g.: موكّل المحامي، المدعي، المدعى عليه، "
            "القاضي، الشاهد، الطرف الأول، الطرف الثاني، الجهة الحكومية …"
        )
    )


class WriterPackage(BaseModel):
    """The planner's payload to the writing executor.

    Built by ``handle_writer_planner_turn`` once the user approves the plan
    (or the planner decides no approval is needed — the "clean turn" / clear
    strategy path). The executor's runner accepts this directly via
    ``handle_writer_turn(package, deps)`` and renders the
    XML-blocked user message in ``build_writer_user_message_from_package``.

    Excluded from this shape (carried separately on deps for the executor's
    runner): user_id, conversation_id, case_id, message_id, revising_item_id.
    """

    intent_ar: str = Field(
        description=(
            "One-paragraph distilled intent in Arabic. The planner crystallizes "
            "what the user wants into this single block — the executor's prompt "
            "uses it as the <user_request>...</user_request> block."
        )
    )
    subtype: WriterSubtype
    edit_mode: Literal["fresh", "revise", "instruct"] = Field(
        description=(
            "fresh = brand new draft. revise = rewrite a prior_draft with "
            "(potentially) new sources/templates. instruct = micro-edit to a "
            "prior_draft (tone, length, single section) with no new evidence."
        )
    )
    plan_md: str = Field(
        default="",
        description=(
            "The plan_md the user approved (or the planner committed to without "
            "asking, in the clean-turn path). Surfaced to the executor inside a "
            "<plan>...</plan> block so the executor knows the chosen scaffold."
        ),
    )
    parties: list[CaseParty] = Field(
        default_factory=list,
        description=(
            "Confirmed parties and their roles, validated by the planner "
            "(via ask_user or explicit statement). The executor receives "
            "these in a <parties> block and MUST use the exact names and "
            "roles throughout the document. Empty only when the document "
            "involves no named persons."
        ),
    )
    analyzed_items: list[AnalyzedItem] = Field(
        default_factory=list,
        description=(
            "All workspace_items the planner included in this package. "
            "Verdict-`none` items are NEVER here. Use the convenience views "
            "below to project by role."
        ),
    )
    templates: list[TemplateRef] = Field(
        default_factory=list,
        description=(
            "Chosen قوالبي template(s) for the executor to mimic (usually 0 or 1), "
            "as TemplateRef. Empty when the user attached a role='template' item "
            "(that rides in analyzed_items) or chose no template."
        ),
    )
    style: WriterStyle = Field(default_factory=WriterStyle)

    # ---- Convenience views (computed; not separate fields) ----


    def user_templates(self) -> list[AnalyzedItem]:
        """analyzed_items the planner labeled as `role='template'` (user-supplied)."""
        return [i for i in self.analyzed_items if i.role == "template"]

    def sources(self) -> list[AnalyzedItem]:
        """analyzed_items the planner labeled as `role='source'`."""
        return [i for i in self.analyzed_items if i.role == "source"]

    def references(self) -> list[AnalyzedItem]:
        """analyzed_items the planner labeled as `role='reference'`."""
        return [i for i in self.analyzed_items if i.role == "reference"]

    def prior_draft(self) -> AnalyzedItem | None:
        """At most one prior_draft item; None if this is a fresh draft."""
        for i in self.analyzed_items:
            if i.role == "prior_draft":
                return i
        return None


# Constructor extension: WriterInput.from_package
# Kept as a free-standing classmethod attached after the original dataclass
# definition so the legacy input shape stays a plain @dataclass.
def _from_package(
    cls,
    package: "WriterPackage",
    *,
    user_id: str,
    conversation_id: str,
    case_id: Optional[str] = None,
    message_id: Optional[str] = None,
    revising_item_id: Optional[str] = None,
) -> "WriterInput":
    """Legacy adapter: build a WriterInput from a WriterPackage.

    Prefer passing the WriterPackage directly to ``handle_writer_turn``
    — the runner branches on type. This adapter exists for callers that still
    expect the older flat-input shape (legacy tests, ad-hoc CLI smoke runs).

    Lossy conversion: drops `plan_md`, `templates`, the structured
    `analyzed_items` (flattens to a research_items dict list), and role
    information. The full-fidelity path is the WriterPackage runner branch.
    """
    research = [
        {
            "item_id": ai.item_id,
            "wi_seq": ai.wi_seq,
            "title": ai.title,
            "kind": ai.kind,
            "content_md": ai.body_md,
        }
        for ai in package.analyzed_items
        if ai.role in ("source", "reference", "prior_draft")
    ]
    return cls(
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        message_id=message_id,
        user_request=package.intent_ar,
        subtype=package.subtype,
        research_items=research,
        workspace_context=None,
        revising_item_id=revising_item_id or (
            package.prior_draft().item_id if package.prior_draft() else None
        ),
        detail_level=package.style.detail_level,
        tone=package.style.tone,
    )


WriterInput.from_package = classmethod(_from_package)  # type: ignore[attr-defined]


__all__ = [
    "WriterSubtype",
    "WriterSection",
    "WriterLLMOutput",
    "WriterInput",
    "WriterOutput",
    "WorkspaceContextBlock",
    "CitationRef",
    # New WriterPackage family (writer_planner integration)
    "AnalyzedItem",
    "TemplateRef",
    "WriterStyle",
    "CaseParty",
    "WriterPackage",
]
