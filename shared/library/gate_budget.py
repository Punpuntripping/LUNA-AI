"""The exposure budget — gate on a FRACTION of the document.

Moved here from ``backend/app/services/library_service.py`` (which re-exports
both names unchanged) so that ``agents/`` can spend the same budget without
importing ``backend/``: the workspace-item preview lock
(``shared/workspace/preview_lock.py``, ``.claude/plans/wi_preview_lock.md`` §4)
truncates for the web/app AND for the router's ``unfold_workspace_item``, and
both must cut byte-identical previews from one definition.

Plan: ``.claude/plans/gate_exposure_budget.md`` §3.1 — one budget computed ONCE
from the document's own length, never granted per section.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateBudget:
    """A wing's exposure dial. ``ratio`` is the policy; the bounds keep it sane.

    ``floor`` exists for SEO — thin content ranks badly, and an over-tight gate
    costs the traffic these pages are published for. ``ceiling`` exists for the
    opposite reason: a 45k-char نظام must not leak 7k just because it is long.
    """

    ratio: float
    floor: int
    ceiling: int


def free_budget(total_chars: int, budget: GateBudget) -> int:
    """The document-wide free allowance in characters. Pure, no DB."""
    return min(
        max(round(budget.ratio * max(0, int(total_chars))), budget.floor),
        budget.ceiling,
    )


__all__ = ["GateBudget", "free_budget"]
