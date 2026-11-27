"""What the model emits. Code checks it and builds the router's
:class:`~agents.attachment_agent.models.AttachmentAgentResult` (``verify.finalize``).

The model never writes a location: each quote's file, section and page are set by
code from where the quote sits in the file.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: The outcomes the model may choose. ``edited`` = it staged the change(s) asked
#: for; code settles the final outcome from what was applied (``edited`` /
#: ``reanalysis_started`` / ``not_edited``). ``error`` only from the runner.
OutcomeLLM = Literal["found", "not_found", "which_file", "needs_clarification", "edited"]

#: What the user's message asks for. ``change`` / ``reanalyze`` let code apply the
#: staged edits after the output passes validation; ``read`` never does.
RequestKind = Literal["read", "change", "reanalyze"]


class QuoteLLM(BaseModel):
    file: str = Field(description="The file label the quote is from, e.g. F1.")
    text: str = Field(
        description=(
            "Copied word for word from text a tool returned or a file's <text>. "
            "One continuous passage: no rewording, no joining of separate pieces, no '…'."
        ),
    )


class PointLLM(BaseModel):
    statement: str = Field(
        description="One short Arabic sentence: what the files say on this point. No page or section numbers.",
    )
    quotes: list[QuoteLLM] = Field(
        default_factory=list,
        description="The passages that show it (at least one for a found answer).",
    )


class AttachmentOutputLLM(BaseModel):
    #: Required: a missing ``request`` must not silently turn a change into a read.
    request: RequestKind = Field(
        description=(
            "Always set. read = a question about what the files say; change = the user asks to "
            "change something in a file or its analysis; reanalyze = the user asks for the analysis "
            "to be redone."
        ),
    )
    outcome: OutcomeLLM
    points: list[PointLLM] = Field(default_factory=list)
    ask_user: str = Field(
        default="",
        description="which_file / needs_clarification only: one short Arabic question for the user.",
    )
    note: str = Field(
        default="",
        description=(
            "Short Arabic line for the router. not_found: what you looked for and what you read. "
            "A change you could not stage: why (e.g. the section is already core)."
        ),
    )
    rationale: str = Field(default="", description="Short reason. Logged, never shown.")


__all__ = ["AttachmentOutputLLM", "OutcomeLLM", "PointLLM", "QuoteLLM", "RequestKind"]
