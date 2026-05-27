"""Dependencies for the item_analyzer (Layer-4 Memory) runner.

The analyzer's LLM never sees ``user_id`` / ``conversation_id`` directly —
they're used only for RLS-scoped Supabase reads inside the runner. The
``caller_id`` is routed to ``prompt_registry`` at agent-build time; the LLM
never sees it as a tool argument either.

Mirrors the ``artifact_summarizer.deps.ArtifactSummaryDeps`` house pattern:
plain ``@dataclass``, optional ``logger``, single ``build_*_deps`` helper.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import CallerId


@dataclass
class AnalyzerDeps:
    """Injected by the caller's runner once per turn.

    Attributes:
        supabase: A Supabase client scoped to the calling user (RLS-enforcing).
            The loader uses this to SELECT ``workspace_items`` rows.
        http_client: Shared HTTP client (typed ``Any`` to dodge the
            httpx import at this layer); reserved for future tool calls.
        user_id: Calling user's ``users.user_id``. Used for RLS scoping and
            cost-tracking ``agent_runs`` rows. Never sent to the LLM.
        conversation_id: Current ``conversations.conversation_id``. Used for
            cost tracking and span attributes. Never sent to the LLM.
        caller_id: Identifies which caller package is invoking the analyzer.
            Drives prompt selection in ``prompt_registry`` at agent-build
            time. The LLM never sees this value either — it only sees the
            resolved prompt.
        logger: Optional per-run logger (mirrors the deep_search /
            artifact_summarizer pattern). ``None`` falls back to the module
            logger inside the runner.
    """

    supabase: Any
    http_client: Any
    user_id: str
    conversation_id: str
    caller_id: CallerId
    logger: Any | None = None


def build_analyzer_deps(
    *,
    supabase: Any,
    http_client: Any,
    user_id: str,
    conversation_id: str,
    caller_id: CallerId,
    logger: Any | None = None,
) -> AnalyzerDeps:
    """Construct ``AnalyzerDeps`` for a single ``analyze()`` invocation.

    Keyword-only by design — every field is semantically meaningful and
    positional ordering would be a footgun (especially the two id fields).
    """

    return AnalyzerDeps(
        supabase=supabase,
        http_client=http_client,
        user_id=user_id,
        conversation_id=conversation_id,
        caller_id=caller_id,
        logger=logger,
    )
