"""Conversation memory writer: the Layer-4 "memory agent".

It runs on every user message and keeps the conversation's hidden case record:
what the user said about their matter that must stay true on later turns. It turns
that into add / update / remove operations, with the rules in its own prompt, and
it is the only writer of that memory. It never talks to the user.

Spec: ``.claude/plans/conversation_memory_agent.md``. Failure never reaches the
turn: the runner returns ``failed=True`` and what the user said is still in the
chat.
"""
from __future__ import annotations

from .agent import MEMORY_WRITER_LIMITS, create_convo_memory_writer
from .deps import MemoryWriterDeps
from .models import MemoryOps, MemoryWriterInput, MemoryWriterOutput
from .runner import (
    MEMORY_WRITER_TIMEOUT_S,
    build_memory_writer_input,
    pending_memory_writer,
    run_memory_writer,
    schedule_memory_writer,
)

__all__ = [
    "MEMORY_WRITER_LIMITS",
    "MEMORY_WRITER_TIMEOUT_S",
    "MemoryOps",
    "MemoryWriterDeps",
    "MemoryWriterInput",
    "MemoryWriterOutput",
    "build_memory_writer_input",
    "create_convo_memory_writer",
    "pending_memory_writer",
    "run_memory_writer",
    "schedule_memory_writer",
]
