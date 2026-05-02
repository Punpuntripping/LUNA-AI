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


class WriterSection(BaseModel):
    """One section of the drafted document."""

    heading_ar: str = Field(description="عنوان القسم بالعربية (## level)")
    body_md: str = Field(description="نص القسم بالماركداون العربي")


class WriterLLMOutput(BaseModel):
    """Raw structured output from the writer LLM.

    The runner concatenates sections into a single content_md before
    persisting. Keeping sections separate at the LLM layer lets us
    enforce per-section length limits and run lightweight per-section
    validation (e.g. each section must be >= 50 chars).
    """

    title_ar: str = Field(description="عنوان المستند الكامل")
    sections: list[WriterSection] = Field(
        ...,
        description="ترتيب الأقسام كما تظهر في المستند النهائي",
    )
    citations_used: list[int] = Field(
        default_factory=list,
        description=(
            "أرقام مراجع agent_search المستشهد بها داخل النص. "
            "تطابق الأرقام في agent_search.metadata.references."
        ),
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="تقدير الكاتب لجودة المسوّدة"
    )
    notes_ar: list[str] = Field(
        default_factory=list,
        description="ملاحظات للمستخدم: نقاط تحتاج مراجعة، فجوات في المعطيات",
    )
    chat_summary: str = Field(
        default="",
        description=(
            "جملة أو جملتان بالعربية تُقدِّمان المستند المُسوَّد للعرض المختصر في المحادثة. "
            "الحد الأقصى 500 حرف."
        ),
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description=(
            "أبرز 3 إلى 5 نقاط يجب على المستخدم مراجعتها أو الانتباه إليها في المستند — "
            "5 بنود كحد أقصى، كل بند جملة قصيرة بالعربية."
        ),
    )


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
    user_request: str            # The user's drafting brief (from router OpenTask.briefing)
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


__all__ = [
    "WriterSubtype",
    "WriterSection",
    "WriterLLMOutput",
    "WriterInput",
    "WriterOutput",
    "WorkspaceContextBlock",
]
