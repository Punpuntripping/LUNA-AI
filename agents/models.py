"""Pydantic output models for router, dispatch, and specialist agents.

Wave 9 slim: TaskContinue/TaskEnd are gone (no consumer left after the
specialist refactor). The router now emits ChatResponse | DispatchAgent;
specialists return SpecialistResult; orchestrator passes MajorAgentInput
into Tier-2 agents which never read the DB directly.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ── Constants ─────────────────────────────────────────────────────────────

# Hard upper bound on how many workspace items the router may attach to a
# single specialist dispatch. Single source of truth — bump as needed.
# Field constraint enforces the bound; an output_validator on the router
# raising ModelRetry gives the LLM a guided retry when it overshoots.
MAX_ATTACHED_ITEMS = 7


# ── Planner outputs (used as agent output_type) ───────────────────────────

class PlannerResult(BaseModel):
    """Structured output from the deep search planner agent."""
    task_done: bool = Field(description="Whether the research task is complete this turn")
    end_reason: Literal["completed", "out_of_scope", "pending"] = Field(
        default="pending",
        description='Why the task ended. "pending" if task is not done yet.',
    )
    answer_ar: str = Field(
        description="Short Arabic summary for chat display. "
        "The full report goes in the artifact, not here.",
    )
    search_summary: str = Field(
        default="",
        description="Internal summary for router context — what was searched and found.",
    )
    artifact_md: str = Field(
        default="",
        description="Full markdown report content. Must be complete, not a diff.",
    )


# ── Router outputs ────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    """Router responds directly to the user."""
    type: Literal["chat"] = "chat"
    message: str = Field(description="Response text to show the user")


class DispatchAgent(BaseModel):
    """Router dispatches a specialist agent (Tier 2)."""
    type: Literal["dispatch"] = "dispatch"
    agent_family: Literal[
        "deep_search", "writing", "memory"
    ] = Field(
        description="Which specialist family to dispatch."
    )
    briefing: str = Field(
        description="Context summary for the specialist. Must include: what the user wants, "
        "relevant conversation context, any specific requirements mentioned."
    )
    target_item_id: str | None = Field(
        default=None,
        description="If editing/extending an existing workspace item, its UUID. "
        "None for fresh outputs."
    )
    attached_item_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_ATTACHED_ITEMS,
        description=(
            "Workspace item IDs the router selected as input for the specialist. "
            f"Hard cap of {MAX_ATTACHED_ITEMS} — pick the most relevant items. "
            "The orchestrator hydrates these into WorkspaceItemSnapshot objects "
            "before invoking the specialist."
        ),
    )
    subtype: str | None = Field(
        default=None,
        description=(
            "For agent_family='writing', the document subtype: "
            "contract (عقد), memo (مذكرة), legal_opinion (رأي قانوني), "
            "defense_brief (لائحة دفاع), letter (خطاب), summary (ملخص). "
            "Ignored for non-writing families. Defaults to 'memo' downstream if missing."
        ),
    )


# ── Tier-2 input contract ─────────────────────────────────────────────────

class ChatMessageSnapshot(BaseModel):
    """A single chat message frozen at dispatch time."""
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class WorkspaceItemSnapshot(BaseModel):
    """A workspace item frozen at dispatch time, with full content_md."""
    item_id: str
    kind: str
    title: str
    content_md: str
    metadata: dict = Field(default_factory=dict)


class MajorAgentInput(BaseModel):
    """Tier 2 input contract.

    Major agents NEVER read DB messages or workspace items directly — they
    receive only what the orchestrator passes here. This keeps specialists
    deterministic, testable in isolation, and immune to RLS/auth concerns.
    """
    briefing: str
    attached_items: list[WorkspaceItemSnapshot] = Field(
        default_factory=list,
        description="Router-selected workspace items, full content_md included.",
    )
    recent_messages: list[ChatMessageSnapshot] = Field(
        default_factory=list,
        description="Last N chat messages (default 3, per-agent overridable).",
    )
    target_item_id: str | None = Field(
        default=None,
        description="Existing workspace item to edit/extend; None for fresh output.",
    )
    user_id: str
    conversation_id: str
    case_id: str | None = None


# ── Tier-2 output contract ────────────────────────────────────────────────

class SpecialistResult(BaseModel):
    """Standardized specialist return shape, consumed by orchestrator._dispatch.

    The full body_md is no longer streamed to chat — it lives in the workspace
    item identified by output_item_id. Chat receives chat_summary and
    key_findings only.
    """
    output_item_id: str | None = None
    chat_summary: str = Field(
        description="≤ 500 chars, streamed to chat as the assistant body.",
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description="≤ 5 bullets, streamed after chat_summary.",
    )
    sse_events: list[dict] = Field(default_factory=list)
    model_used: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    per_phase_stats: dict = Field(default_factory=dict)
