"""Conversation compactor — tier_2 DeepSeek-primary Layer-4 memory agent that
turns the oldest span of a conversation into one carry-forward summary.

Runs at most once per turn, in the pre-router hook, and only when the
conversation is over the token threshold. Its output replaces the compacted
messages for every downstream context surface (router, writer, attachment
summarizer), and it SUPERSEDES the previous summary rather than sitting
alongside it — the system keeps only the most recent ``convo_context``.

Fail-closed: ``CompactionOutput.failed`` defaults to ``True`` and clears only
when a real summary was produced. A caller must not advance
``conversations.compacted_through_message_id`` while it is set. See
``runner.py`` and ``.claude/plans/memory_compaction_agent.md`` §3.5.
"""
from __future__ import annotations

from .agent import COMPACTOR_LIMITS, create_convo_compactor
from .deps import CompactionDeps, build_compactor_deps
from .models import CompactionInput, CompactionLLMOutput, CompactionOutput
from .runner import handle_compaction_turn, run_convo_compaction

__all__ = [
    "COMPACTOR_LIMITS",
    "CompactionDeps",
    "CompactionInput",
    "CompactionLLMOutput",
    "CompactionOutput",
    "build_compactor_deps",
    "create_convo_compactor",
    "handle_compaction_turn",
    "run_convo_compaction",
]
