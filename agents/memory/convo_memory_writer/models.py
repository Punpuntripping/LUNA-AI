"""Input / output contracts for the convo_memory_writer agent.

Every text field on :class:`MemoryWriterInput` is in the turn's ENCODED space:
the LLM never sees real identifiers, and ``apply_memory_ops`` decodes at the
moment of each write.

The op fields are loose on purpose. ``kind`` is a plain string and not a
``Literal``: the output validator answers a wrong value with the exact problem,
and ``_normalize_kind`` accepts the Arabic label the model sometimes copies from
the block. A strict schema would fail before either of those could help.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.models import ChatMessageSnapshot
from agents.utils.conversation_memory import MemoryEntry


@dataclass
class MemoryWriterInput:
    """Everything one run needs.

    - ``user_message``: the user's newest message, in full. It is the only new
      message: nothing older is read as new.
    - ``recent_messages``: the rows before it, oldest first, from
      ``orchestrator._load_recent_messages``. Context, so a one-word answer or a
      «نعم» can be understood.
    - ``today``: the date line, so «أمس» can become a real date.
    - ``entries`` and ``item_titles``: empty here. ``run_memory_writer`` fills them
      when the run starts, because a run queued behind another must see what that
      one wrote. Titles only, never bodies: a document cannot speak to this agent.
    - ``message_id`` / ``case_id``: identity for the ledger row.
    """

    conversation_id: str
    user_id: str
    user_message: str
    recent_messages: list[ChatMessageSnapshot] = field(default_factory=list)
    today: str = ""
    entries: list[MemoryEntry] = field(default_factory=list)
    item_titles: list[str] = field(default_factory=list)
    message_id: str | None = None
    case_id: str | None = None


class MemoryAddOp(BaseModel):
    """A new entry."""

    kind: str = Field(description='"request", "fact" or "legal_position".')
    content: str = Field(description="The entry, in Arabic, written to stand alone.")
    insisted: bool = Field(
        default=False,
        description="True only on a legal_position, under the rule in your instructions.",
    )


class MemoryUpdateOp(BaseModel):
    """A change to an existing entry: its id plus only the fields that change."""

    id: str = Field(description='The entry id, such as "M-2".')
    kind: str | None = None
    content: str | None = None
    insisted: bool | None = None


class MemoryOps(BaseModel):
    """What to do to the memory. All three empty means nothing to save.

    Unknown keys are refused. Every field has a default, so an object of any other
    shape would otherwise read as "nothing to save": the entry would be lost with
    no retry and no failure to see.
    """

    model_config = ConfigDict(extra="forbid")

    add: list[MemoryAddOp] = Field(default_factory=list)
    update: list[MemoryUpdateOp] = Field(default_factory=list)
    remove: list[str] = Field(
        default_factory=list, description='Ids of entries to delete, such as "M-3".'
    )

    @model_validator(mode="before")
    @classmethod
    def _unwrap_output_tool_name(cls, data: Any) -> Any:
        """deepseek-flash can finalise as text with the tool's name around the JSON."""
        if isinstance(data, dict) and data.keys() == {"final_result"}:
            return data["final_result"]
        return data

    def tracking_output(self) -> dict[str, int]:
        """Counts only. Without this, ``run_tracked`` dumps the entries to the span."""
        return {"add": len(self.add), "update": len(self.update), "remove": len(self.remove)}


@dataclass
class MemoryWriterOutput:
    """What the runner hands back. ``entries`` are encoded and as they now stand.

    ``failed`` is True on every path that did not reach a clean write. ``entries``
    then holds the rows as far as they are known. ``read_ok`` is False when they
    could not be read and ``entries`` is empty, so a caller keeps what it has. With
    ``read_ok`` True the entries are the freshest there are even on a failed run:
    the rows were read after any run ahead of this one.
    """

    entries: list[MemoryEntry]
    added: int = 0
    updated: int = 0
    removed: int = 0
    failed: bool = False
    read_ok: bool = True

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)
