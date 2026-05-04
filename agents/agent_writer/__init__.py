"""agent_writer -- drafts long-form Arabic legal documents.

Public surface:
    WriterInput / WriterOutput / WriterLLMOutput / WriterSubtype  (models.py)
    WriterDeps + build_writer_deps                                (deps.py)
    create_writer_agent                                           (agent.py)
    publish_writer_result                                         (publisher.py)
    handle_writer_turn                                            (runner.py)
"""
from __future__ import annotations

from .deps import WriterDeps, build_writer_deps
from .models import (
    WorkspaceContextBlock,
    WriterInput,
    WriterLLMOutput,
    WriterOutput,
    WriterSection,
    WriterSubtype,
)
from .prompts import WRITER_PROMPTS, build_writer_user_message, get_writer_prompt

# Lazy imports -- agent.py + runner.py pull in pydantic_ai + supabase + the
# backend service layer transitively. The lazy block lets unit tests that
# only need models/deps/prompts stay fast and not crash if pydantic_ai isn't
# installed in some narrow environment (e.g. SQL-only tooling).
try:
    from .agent import create_writer_agent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    create_writer_agent = None  # type: ignore[assignment]

try:
    from .publisher import publish_writer_result  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    publish_writer_result = None  # type: ignore[assignment]

try:
    from .runner import handle_writer_turn  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    handle_writer_turn = None  # type: ignore[assignment]


__all__ = [
    "WriterInput",
    "WriterOutput",
    "WriterLLMOutput",
    "WriterSection",
    "WriterSubtype",
    "WorkspaceContextBlock",
    "WriterDeps",
    "build_writer_deps",
    "WRITER_PROMPTS",
    "get_writer_prompt",
    "build_writer_user_message",
    "create_writer_agent",
    "publish_writer_result",
    "handle_writer_turn",
]
