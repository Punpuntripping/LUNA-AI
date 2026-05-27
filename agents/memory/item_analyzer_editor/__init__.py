"""item_analyzer_editor — Layer-4 WI editor. v2.1 — not yet implemented.

See ``.claude/plans/item_analyzer_v2.md`` §8 for the carried-forward design;
the v2.1 plan will be the full spec. This stub exists so the writer-planner
sprint can't accidentally take a dependency on the editor before it lands.

When implemented, this package owns the ONLY allowed mutation of
``workspace_items.content_md`` post-insert (via a ``commit_item_revision``
service chokepoint enforced by service-layer + CI lint).
"""
from __future__ import annotations


async def edit(*args, **kwargs):  # noqa: D401, ANN002, ANN003
    """v2.1 stub — raises ``NotImplementedError`` until the editor lands."""
    raise NotImplementedError(
        "item_analyzer_editor.edit not yet implemented — scheduled for v2.1. "
        "See .claude/plans/item_analyzer_v2.md §8."
    )
