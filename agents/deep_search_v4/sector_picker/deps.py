"""Runtime deps for the sector_picker agent.

The picker is stateless — no DB, no embedding, no HTTP. It only needs its
prompt input: the query, the planner_brief (when non-empty), the planner's
mode (so the picker can bias its choice slightly toward case-corpus vs
regulation-corpus sector intuitions), and the assembled
``ContextBlock`` list (same surface the executor expanders receive).

This deps object is constructed by ``run_retrieval`` and handed straight to
``run_sector_picker``. It is never persisted and never crosses a pause.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agents.deep_search_v4.shared.context import ContextBlock

Mode = Literal["case_led", "reg_led", "compliance_led", "full"]


@dataclass
class SectorPickerDeps:
    """Inputs to one sector_picker invocation."""

    query: str
    mode: Mode
    planner_brief: str = ""
    context_blocks: list[ContextBlock] = field(default_factory=list)
    # Optional override token / ModelPolicy threaded from the CLI / monitor.
    # Kept the same shape as the other Pydantic AI agents in this codebase.
    model_override: str | None = None


__all__ = ["Mode", "SectorPickerDeps"]
