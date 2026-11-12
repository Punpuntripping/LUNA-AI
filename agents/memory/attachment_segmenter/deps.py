"""Dependencies for one segmenter LLM call.

LLM-only: the agent has no tools, so the deps carry identity for tracking and
nothing else. ``tracking_input`` keeps document text out of the span.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SegmenterDeps:
    item_id: str
    conversation_id: str | None = None
    case_id: str | None = None
    doc_type: str = ""
    part: int | None = None          # 1 / 2 in split mode, None for a whole document
    from_p: int = 1
    to_p: int = 0

    def tracking_input(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "doc_type": self.doc_type,
            "part": self.part or 0,
            "from_p": self.from_p,
            "to_p": self.to_p,
        }

    def tracking_input_full(self) -> dict[str, Any]:
        return self.tracking_input()
