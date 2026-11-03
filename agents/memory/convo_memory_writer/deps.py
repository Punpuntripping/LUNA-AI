"""Dependencies for the convo_memory_writer agent.

The agent has no tools. Its deps exist for the output validator, which dry-runs
the ops against ``entries`` and checks their numbers against ``source``, and for
``run_tracked``, which reads the identity off them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.utils.conversation_memory import MemoryEntry


@dataclass
class MemoryWriterDeps:
    conversation_id: str
    case_id: str | None = None
    entries: list[MemoryEntry] = field(default_factory=list)
    #: The prompt the model read, encoded. Never in the tracking dumps below.
    source: str = ""
    #: The text a figure in an entry may come from, encoded. ``None`` skips that
    #: check. Never in the tracking dumps below.
    allowed: str | None = None

    def tracking_input(self) -> dict[str, Any]:
        """Counts only. Without this, the verbose dump would carry entry text."""
        return {"entry_count": len(self.entries)}

    def tracking_input_full(self) -> dict[str, Any]:
        return self.tracking_input()
