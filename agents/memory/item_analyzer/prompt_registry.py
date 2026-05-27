"""Per-caller prompt + user-message renderer registry.

Adding a new caller (e.g. ``router`` or ``deep_search_planner``) is the only
diff this file ever sees: drop a ``<caller>/prompts/refs_kinds.py`` +
``<caller>/prompts/meta_kinds.py`` sub-package alongside ``writer/``, then
add four entries here (one per dict).

The four resolver functions are what ``agent.py`` and ``runner.py`` call —
they hide the dict lookups behind a typed signature and surface unknown
callers as ``NotImplementedError`` (programmer bug, not user-recoverable).

v2 scope: only ``writer_planner`` is registered. ``router`` and
``deep_search_planner`` callers are sketched out in
``.claude/plans/item_analyzer_v2.md`` §17 but not yet wired.
"""
from __future__ import annotations

from typing import Callable, Sequence

from .models import CallerId, WorkspaceItemRow
from .writer.prompts.meta_kinds import (
    ANALYZE_META_FOR_WRITER_SYSTEM_AR,
    render_meta_user_msg as _render_meta_writer,
)
from .writer.prompts.refs_kinds import (
    ANALYZE_REFS_FOR_WRITER_SYSTEM_AR,
    render_refs_user_msg as _render_refs_writer,
)


# ---------------------------------------------------------------------------
# Prompt + renderer tables — one entry per (caller, family) pair.
# v2 only registers the writer_planner caller; later callers add lines here.
# ---------------------------------------------------------------------------


_REFS_PROMPTS: dict[CallerId, str] = {
    "writer_planner": ANALYZE_REFS_FOR_WRITER_SYSTEM_AR,
    # "router":                ANALYZE_REFS_FOR_ROUTER_SYSTEM_AR,        # later
    # "deep_search_planner":   ANALYZE_REFS_FOR_DSP_SYSTEM_AR,           # later
}

_META_PROMPTS: dict[CallerId, str] = {
    "writer_planner": ANALYZE_META_FOR_WRITER_SYSTEM_AR,
    # later: "router", "deep_search_planner"
}

_REFS_RENDERERS: dict[CallerId, Callable[..., str]] = {
    "writer_planner": _render_refs_writer,
}

_META_RENDERERS: dict[CallerId, Callable[..., str]] = {
    "writer_planner": _render_meta_writer,
}


# ---------------------------------------------------------------------------
# Resolvers — public entry points for ``agent.py`` and ``runner.py``.
# Each one surfaces "caller not registered for this family" as a
# ``NotImplementedError`` so an unwired caller dies loud at agent-build
# time rather than silently producing wrong verdicts.
# ---------------------------------------------------------------------------


def refs_prompt_for_caller(caller_id: CallerId) -> str:
    """Return the refs-family system prompt registered for ``caller_id``."""
    try:
        return _REFS_PROMPTS[caller_id]
    except KeyError as exc:
        raise NotImplementedError(
            f"item_analyzer: refs prompt not registered for "
            f"caller_id={caller_id!r}"
        ) from exc


def meta_prompt_for_caller(caller_id: CallerId) -> str:
    """Return the meta-family system prompt registered for ``caller_id``."""
    try:
        return _META_PROMPTS[caller_id]
    except KeyError as exc:
        raise NotImplementedError(
            f"item_analyzer: meta prompt not registered for "
            f"caller_id={caller_id!r}"
        ) from exc


def render_refs_user_msg(
    *,
    caller_id: CallerId,
    query: str,
    wis: Sequence[WorkspaceItemRow],
) -> str:
    """Render the refs-family user message for ``caller_id``.

    Delegates to the caller-specific renderer registered above. Each
    renderer takes ``(*, query, wis)`` and returns the final user message
    string for the LLM.
    """
    try:
        renderer = _REFS_RENDERERS[caller_id]
    except KeyError as exc:
        raise NotImplementedError(
            f"item_analyzer: refs user-message renderer not registered for "
            f"caller_id={caller_id!r}"
        ) from exc
    return renderer(query=query, wis=wis)


def render_meta_user_msg(
    *,
    caller_id: CallerId,
    query: str,
    wis: Sequence[WorkspaceItemRow],
) -> str:
    """Render the meta-family user message for ``caller_id``."""
    try:
        renderer = _META_RENDERERS[caller_id]
    except KeyError as exc:
        raise NotImplementedError(
            f"item_analyzer: meta user-message renderer not registered for "
            f"caller_id={caller_id!r}"
        ) from exc
    return renderer(query=query, wis=wis)
