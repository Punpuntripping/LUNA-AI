"""Dependencies for the template_ingester (Layer-4 Memory) runner.

The ingester's LLM never sees ``user_id`` — it's used only for the RLS-scoped
``workspace_items`` read and as the owner on the ``user_templates`` insert. The
HTTP client is reserved for parity with the other agent entry points (the
ingester doesn't currently make tool calls of its own).

Mirrors the ``item_analyzer.deps.AnalyzerDeps`` house pattern: plain
``@dataclass``, optional ``logger``, single ``build_*_deps`` helper.

NOTE: ``supabase`` + ``user_id`` together also satisfy the structural
``HasUserContext`` protocol from ``agents/tool_repository/add_user_template.py``,
so the runner can reuse that module's insert constants directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class IngesterDeps:
    """Injected by the caller's runner (the ingest endpoint) once per call.

    Attributes:
        supabase: A service-role Supabase client. The loader uses it to SELECT
            the ``workspace_items`` row and the insert primitive uses it to
            write the ``user_templates`` row. Service-role BYPASSES RLS, so the
            ``.eq("user_id", user_id)`` scoping in the runner is load-bearing.
        http_client: Shared HTTP client (typed ``Any`` to dodge the httpx
            import at this layer); reserved for future tool calls.
        user_id: Owner's ``users.user_id``. Used for RLS scoping on the read
            and as ``user_templates.user_id`` on insert. Never sent to the LLM.
        conversation_id: Optional ``conversations.conversation_id`` for span
            attributes / cost-ledger identity. The ingest endpoint has no
            conversation context, so this is usually ``None``.
        logger: Optional per-run logger (mirrors the item_analyzer pattern).
            ``None`` falls back to the module logger inside the runner.
    """

    supabase: Any
    http_client: Any
    user_id: str
    conversation_id: str | None = None
    logger: Any | None = None


def build_ingester_deps(
    *,
    supabase: Any,
    http_client: Any,
    user_id: str,
    conversation_id: str | None = None,
    logger: Any | None = None,
) -> IngesterDeps:
    """Construct ``IngesterDeps`` for a single ``handle_template_ingestion`` call.

    Keyword-only by design — every field is semantically meaningful and
    positional ordering would be a footgun.
    """

    return IngesterDeps(
        supabase=supabase,
        http_client=http_client,
        user_id=user_id,
        conversation_id=conversation_id,
        logger=logger,
    )


__all__ = ["IngesterDeps", "build_ingester_deps"]
