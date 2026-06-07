"""Loader for the writer_planner's per-turn dynamic instructions.

Returns a list of ``ArtifactSummaryView`` rows — the planner's signal set for
triaging which workspace_items belong in the WriterPackage. Per the core
invariant in `.claude/plans/writer_planner.md`, the planner LLM works from
**summaries only**, never raw ``content_md``. This loader enforces that by
selecting exactly:

    item_id, kind, title, summary, word_count, created_at

…and intentionally NOT selecting ``content_md``. Any caller that needs raw
content goes through ``item_analyzer`` (verdict path) or, on the bypass
path, fetches ``content_md`` from the runner just before assembling the
WriterPackage — never inside the planner's prompt context.

The returned shape is a plain dataclass so callers can render it into a
Pydantic AI dynamic instruction without serialization overhead and so unit
tests can construct fixture rows without round-tripping through Supabase.

This loader never raises into agent code: any DB error returns ``[]`` and
logs a warning. The planner's prompt covers the empty-conversation path
(it just emits a final PlannerDecision based on attached_items alone).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactSummaryView:
    """One workspace_item rendered into the planner's dynamic instructions.

    Mirrors the columns the planner is allowed to read. Frozen so it round-trips
    safely as a dependency snapshot across pause/resume.

    Migration 052 / agent communication protocol: ``wi_seq`` is the
    conversation-scoped integer alias behind ``WI-{wi_seq}``. The planner's
    prompts render this label and emit it in ``selected_wis`` /
    ``role_assignments`` instead of raw UUIDs. ``None`` for legacy /
    pre-migration rows; those items are skipped from the alias-bearing
    prompt surface (the resolver can't reach them anyway).
    """

    item_id: str
    kind: str
    title: str
    summary: str
    word_count: int
    created_at: str  # ISO timestamp; used only for ordering in dynamic instructions
    wi_seq: int | None = None

    def is_summary_thin(self, min_chars: int = 40) -> bool:
        """True when the summary is missing or too short to triage from.

        The planner's prompt is instructed to invoke ``analyze_items`` on
        thin-summary WIs (see § Stage 1 protocol — examine before asking).
        Keeping the threshold conservative (40 chars) avoids over-triggering
        the analyzer on lightly-summarized rows.
        """
        return not self.summary or len(self.summary.strip()) < min_chars


async def load_writer_planner_context(
    supabase: Any,
    user_id: str,
    conversation_id: str,
    *,
    limit: int = 50,
) -> list[ArtifactSummaryView]:
    """Load conversation-scope workspace_items as summary-only views.

    Returns rows newest-first, capped at ``limit`` so the planner's dynamic
    instructions stay bounded even on long conversations. The cap is
    intentionally generous — 50 rows × ~200-token-summary ≈ 10k tokens, well
    within tier_1 context budgets — but configurable for tests / future
    history-processor experiments.

    Args:
        supabase: Sync Supabase client (async-context per project pattern).
        user_id: Owning user (RLS already filters, but we pass for clarity).
        conversation_id: The conversation whose workspace items the planner
            should triage. Cross-conversation context is NOT loaded — the
            router already pre-selects items for the turn via
            ``MajorAgentInput.attached_items``.
        limit: Max rows to return (newest first). Defaults to 50.

    Returns:
        List of ``ArtifactSummaryView`` (newest first). Empty on DB error
        or empty conversation — never raises.
    """
    try:
        result = (
            supabase
            .table("workspace_items")
            .select("item_id, kind, title, summary, word_count, created_at, wi_seq")
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", None)
            .order("created_at", desc=True)
            .limit(max(int(limit), 1))
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "writer_planner_context: load failed for conv=%s user=%s (%s) → []",
            conversation_id, user_id, exc,
        )
        return []

    rows = getattr(result, "data", None) or []
    views: list[ArtifactSummaryView] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            raw_wi_seq = row.get("wi_seq")
            wi_seq: int | None
            try:
                wi_seq = int(raw_wi_seq) if raw_wi_seq is not None else None
            except (TypeError, ValueError):
                wi_seq = None
            views.append(
                ArtifactSummaryView(
                    item_id=str(row["item_id"]),
                    kind=str(row.get("kind") or ""),
                    title=str(row.get("title") or ""),
                    summary=str(row.get("summary") or ""),
                    word_count=int(row.get("word_count") or 0),
                    created_at=str(row.get("created_at") or ""),
                    wi_seq=wi_seq,
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug(
                "writer_planner_context: skip malformed row item_id=%r (%s)",
                row.get("item_id"), exc,
            )
            continue
    return views


@dataclass(frozen=True)
class UserTemplateTitle:
    """One قوالبي (user_templates) row as the planner sees it — title only.

    The planner reads these passively from its ``<my_templates>`` context block
    and picks one by its ``TPL-{n}`` alias. The body (``content_md``) is NOT
    loaded here — the runner fetches it only after the planner commits to a
    ``chosen_template`` (same summary-only discipline as workspace items).
    """

    template_id: str
    title: str


async def load_user_template_titles(
    supabase: Any,
    user_id: str,
    *,
    limit: int = 50,
) -> list[UserTemplateTitle]:
    """Load the user's active قوالبي template titles (newest-updated first).

    Scoped to ``user_templates.user_id = user_id`` (the internal users.user_id —
    the same value the planner carries on ``deps.user_id``). Titles only; the
    body is fetched later by the runner for the one template the planner picks.

    Capped at ``limit`` (default 50) so the planner's context stays bounded even
    for users with large libraries. Never raises — any DB error returns ``[]``.
    """
    try:
        result = (
            supabase
            .table("user_templates")
            .select("template_id, title")
            .eq("user_id", user_id)
            .is_("deleted_at", None)
            .order("updated_at", desc=True)
            .limit(max(int(limit), 1))
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "writer_planner_context: load_user_template_titles failed for "
            "user=%s (%s) → []",
            user_id, exc,
        )
        return []

    rows = getattr(result, "data", None) or []
    out: list[UserTemplateTitle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = row.get("template_id")
        if tid is None:
            continue
        out.append(
            UserTemplateTitle(template_id=str(tid), title=str(row.get("title") or ""))
        )
    return out


__all__ = [
    "ArtifactSummaryView",
    "load_writer_planner_context",
    "UserTemplateTitle",
    "load_user_template_titles",
]
