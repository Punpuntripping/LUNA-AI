"""What deep search receives from the artifact_analyzer, and the on/off check.

A leaf module: the router, the orchestrator and the planner all use it, and the
analyzer package imports too much to sit under any of them.

The block carries its own instruction, so the expander and aggregator prompts
need no change: every forwarded block already reaches them as
``<block label="...">``. It is not ``planner_brief``, whose contract is that it
supplements and never steers. This one steers, on purpose.
"""
from __future__ import annotations

from typing import Any, Mapping

from shared.config import get_settings

PRIOR_COVERAGE_LABEL = "prior_coverage"

_HEADER = (
    "This turn continues an earlier search in this conversation. What follows is "
    "already established: take it as given, do not search for it again and do not "
    "repeat it. Work only on what is still needed, and keep every fact stated here."
)


def analyzer_enabled(user_id: str) -> bool:
    """Flag on, and either no allowlist or this user is on it. Never raises."""
    try:
        settings = get_settings()
        if not settings.ARTIFACT_ANALYZER_ENABLED:
            return False
        allowed = {
            u.strip() for u in (settings.ARTIFACT_ANALYZER_USERS or "").split(",") if u.strip()
        }
        return not allowed or str(user_id) in allowed
    except Exception:  # noqa: BLE001 — off is today's behaviour
        return False


def render_prior_coverage(handoff: Mapping[str, Any]) -> str:
    parts = [_HEADER]
    restatement = str(handoff.get("restatement") or "").strip()
    if restatement:
        parts.append(f"The question, with every fact established:\n{restatement}")
    covered = [
        f"- «{q.get('text', '')}» ({q.get('source_name', '')})"
        for q in handoff.get("covered_quotes") or []
    ]
    if covered:
        parts.append("Already established:\n" + "\n".join(covered))
    missing = [f"- {m}" for m in handoff.get("missing") or []]
    if missing:
        parts.append("Still needed:\n" + "\n".join(missing))
    return "\n\n".join(parts)
