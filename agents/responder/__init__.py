"""Unified responder — one voice for search + writing turns.

Plan: ``.claude/plans/unified_responder.md``. Layer 2 (user-facing voice) ·
model slot ``responder`` (tier_2 / ``_FLASH``). The planners decide; the
responder speaks.

P2 status: wired into deep_search (planner phase 3), simple_search
(``_finalise``) and writing (writer_planner completed path), each behind
``RESPONDER_UNIFIED_FAMILIES`` (``wiring.responder_unified``) — default empty,
so every family still runs its old responder until flagged on.
"""
from __future__ import annotations

from .agent import RESPONDER_LIMITS, RESPONDER_SLOT, ResponderDeps, create_responder
from .context import render_context_block, render_router_context
from .models import (
    CardVerdict,
    DeepSearchArtifact,
    DeepSearchResponse,
    DocDigest,
    PriorSearch,
    ResponderBase,
    ResponderInput,
    ResponderMode,
    ResponderOutput,
    SimpleSearchArtifact,
    SimpleSearchResponse,
    WritingArtifact,
    WritingResponse,
)
from .prompts import CORE_PROMPT, MODE_BLOCKS, SYSTEM_PROMPTS, build_turn_message
from .runner import run_responder

__all__ = [
    "RESPONDER_LIMITS",
    "RESPONDER_SLOT",
    "ResponderDeps",
    "create_responder",
    "render_context_block",
    "render_router_context",
    "CardVerdict",
    "DeepSearchArtifact",
    "DeepSearchResponse",
    "DocDigest",
    "PriorSearch",
    "ResponderBase",
    "ResponderInput",
    "ResponderMode",
    "ResponderOutput",
    "SimpleSearchArtifact",
    "SimpleSearchResponse",
    "WritingArtifact",
    "WritingResponse",
    "CORE_PROMPT",
    "MODE_BLOCKS",
    "SYSTEM_PROMPTS",
    "build_turn_message",
    "run_responder",
]
