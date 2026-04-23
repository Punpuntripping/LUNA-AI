"""Pydantic output models for router and task agents."""
from pydantic import BaseModel, Field
from typing import Literal, Optional


# ── Planner outputs (used as agent output_type) ──

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


# ── Router outputs ──

class ChatResponse(BaseModel):
    """Router responds directly to the user."""
    type: Literal["chat"] = "chat"
    message: str = Field(description="Response text to show the user")


class OpenTask(BaseModel):
    """Router opens a specialist task."""
    type: Literal["task"] = "task"
    task_type: Literal["deep_search", "end_services", "extraction"] = Field(
        description="Which task type to open"
    )
    briefing: str = Field(
        description="Context summary for the task agent. Must include: what the user wants, "
        "relevant conversation context, any specific requirements mentioned."
    )
    artifact_id: Optional[str] = Field(
        default=None,
        description="If editing an existing artifact, its UUID. None for new tasks."
    )


# ── Task agent outputs ──

class TaskContinue(BaseModel):
    """Task agent continues working."""
    type: Literal["continue"] = "continue"
    response: str = Field(description="What to show the user this turn")
    artifact: str = Field(description="Full markdown artifact — complete, not a diff")


class TaskEnd(BaseModel):
    """Task agent is done or detected out-of-scope message."""
    type: Literal["end"] = "end"
    reason: Literal["completed", "out_of_scope"] = Field(
        description="Why the task is ending"
    )
    summary: str = Field(
        description="Recap: key findings, user modifications, references used. "
        "Persisted in conversation memory."
    )
    artifact: str = Field(description="Final state of the markdown artifact")
    last_response: str = Field(
        description="Final message to show the user"
    )
