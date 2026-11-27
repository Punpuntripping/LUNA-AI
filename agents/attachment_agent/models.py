"""Contract of the attachment agent (.claude/plans/attachment_agent.md §2).

The router tool builds an :class:`AttachmentAgentCall`, awaits
:func:`agents.attachment_agent.runner.run_attachment_agent`, and hands the
router :func:`render_for_router` of the :class:`AttachmentAgentResult`.

These shapes are the seam between the router side and the agent package; change
them only together with both sides.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# found               – the passage(s) were found; ``points`` carry checked quotes
# not_found           – not in what the agent read (never "not in the file" unless
#                       the file was read whole — ``note`` says which)
# which_file          – the message does not say which file; ``ask_user`` asks
# edited              – at least one of the user's changes was applied; ``edits``
#                       lists each change (and any that failed, with why)
# reanalysis_started  – the user asked to re-analyze; the angle mapper is running
# not_edited          – a change was asked and none could be made; ``edits`` /
#                       ``note`` say why (wave 2; additive)
# needs_clarification – what to change / what is asked is unclear; ``ask_user``
# error               – the agent could not run (timeout, model error); ``note``
#                       is a plain line for the router, ``error`` is internal
Outcome = Literal[
    "found",
    "not_found",
    "which_file",
    "edited",
    "reanalysis_started",
    "not_edited",
    "needs_clarification",
    "error",
]


class AttachmentAgentCall(BaseModel):
    """What the router tool passes. The user's words are never reworded."""

    user_id: str
    conversation_id: str
    # The current user message (ledger rows, edit records). None in tests.
    message_id: Optional[str] = None
    # The user's message word for word, as the router received it
    # (``RouterDeps.user_message`` — already in the turn's masked form).
    user_message: str
    # workspace_items.item_id of kind=attachment the router picked (resolved
    # from WI-n aliases). Empty = every attachment in the conversation.
    item_ids: list[str] = Field(default_factory=list)
    # Optional pre-loaded rows {role, content}, oldest first. Empty = the runner
    # loads the last few rows itself.
    recent_messages: list[dict] = Field(default_factory=list)


class QuoteOut(BaseModel):
    """One quote, checked word for word by code. The location fields are set by
    code from where the quote sits — never taken from the model."""

    text: str
    item_id: str
    file_title: str
    wi: Optional[str] = None            # "WI-3", when the item has a wi_seq
    section_ref: Optional[str] = None   # human-readable section id, e.g. "S7"
    section_what: Optional[str] = None  # the section's «what» line
    owner: Optional[str] = None         # whose statement the section is
    page: Optional[int] = None          # 1-based page, when page rows exist


class PointOut(BaseModel):
    statement: str
    quotes: list[QuoteOut] = Field(default_factory=list)


class AttachmentAgentResult(BaseModel):
    outcome: Outcome
    points: list[PointOut] = Field(default_factory=list)
    # One Arabic line per change: applied ones in the past tense, failed ones as
    # «لم يُنفَّذ: … — reason». Masked (numbers / e-mails) like everything the
    # router reads.
    edits: list[str] = Field(default_factory=list)
    # One short question for the user (which_file / needs_clarification).
    ask_user: Optional[str] = None
    # A plain line for the router (e.g. what was read when nothing was found).
    note: Optional[str] = None
    # Internal only — logged, never shown to the router or the user.
    error: Optional[str] = None


__all__ = [
    "Outcome",
    "AttachmentAgentCall",
    "QuoteOut",
    "PointOut",
    "AttachmentAgentResult",
]
