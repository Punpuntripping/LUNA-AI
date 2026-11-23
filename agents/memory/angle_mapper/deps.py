"""Dependencies for one angle-map LLM call (one message group, angles-v4).

LLM-only: the agent has no tools. The deps carry identity for tracking and the
group's section refs for the output validator. ``tracking_input`` keeps the
documents' words out of the span (counts and ids only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AngleMapperDeps:
    #: The item the run was started for (the frame's trigger; D1 for a message run).
    item_id: str
    message_id: str | None = None
    #: The group's members, D-order (``item_ids[0]`` is D1).
    item_ids: tuple[str, ...] = ()
    conversation_id: str | None = None
    case_id: str | None = None
    #: Each member's doc_type, D-order.
    doc_types: tuple[str, ...] = ()
    #: Every section ref of the group (``"D1.S7"``) — the only valid refs.
    section_refs: frozenset[str] = field(default_factory=frozenset)
    #: ``map`` (the first call and its re-attempt) or ``repair`` (the coverage call).
    call: str = "map"

    def tracking_input(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "message_id": self.message_id,
            "docs": len(self.item_ids),
            "doc_types": list(self.doc_types),
            "sections": len(self.section_refs),
            "call": self.call,
        }

    def tracking_input_full(self) -> dict[str, Any]:
        return self.tracking_input()


__all__ = ["AngleMapperDeps"]
