"""Pydantic schemas for the writer_planner decider.

The decider's ``output_type`` is the discriminated list
``[PlannerDecision, DeferredToolRequests]``:

- Normal final emission → :class:`PlannerDecision`. The runner walks
  the analyzer verdicts (from ``deps.last_analyzer_output``) OR runs the
  bypass path (when the analyzer wasn't invoked) and assembles a
  ``WriterPackage`` from this decision.
- Pause emission → ``pydantic_ai.DeferredToolRequests``. The orchestrator
  persists the agent_runs row in ``status='awaiting_user'`` with
  ``pause_reason`` derived from the tool name (``ask_user`` → 'clarify',
  ``present_plan_for_approval`` → 'approve_plan').

This module is **pure** — it imports only ``pydantic`` + Pydantic types from
``writing_executor.models``. No ``pydantic_ai``, no Supabase. Tests can
construct ``PlannerDecision`` without the agent runtime installed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agents.writer.models import WriterSubtype


PlannerRole = Literal["template", "source", "reference", "prior_draft"]
EditMode = Literal["fresh", "revise", "instruct"]


class PlannerDecision(BaseModel):
    """Final decision the writer_planner-decider emits when planning is done.

    The runner consumes this to assemble the ``WriterPackage``:

    - When ``analyzer_invoked=True`` the runner reads
      ``deps.last_analyzer_output`` and walks verdicts; ``selected_wis``
      restricts which WIs survive into the package (the analyzer may have
      returned more verdicts than the planner ultimately wants).
    - When ``analyzer_invoked=False`` the runner takes ``selected_wis``
      directly, fetches each WI's ``content_md`` from Supabase, and builds
      ``AnalyzedItem`` records with ``need='full'`` and the raw content as
      ``body_md``. This is the bypass path — see § Two skippable phases.

    ``role_assignments`` maps ``WI-{seq}`` alias → role for both paths. The
    decider is responsible for assigning a role to every alias it puts in
    ``selected_wis``; the runner falls back to ``'source'`` if a mapping is
    missing (lenient, but logged).

    **Alias contract** (see ``.claude/plans/agent_communication_protocol.md``):
    ``selected_wis`` and the keys of ``role_assignments`` are ``WI-{seq}``
    aliases (e.g. ``"WI-3"``) — never raw UUIDs. The runner resolves these
    against ``deps.wi_alias_map`` before invoking walkers, which stay
    UUID-based against the DB.
    """

    intent_ar: str = Field(
        ...,
        description=(
            "One-paragraph Arabic distillation of what the user wants the "
            "writing executor to draft. Crystallizes subtype + key parameters "
            "(parties, amounts, dates...) — becomes WriterPackage.intent_ar."
        ),
    )
    subtype: WriterSubtype = Field(
        description="Drafting subtype — chosen from stated/implied user intent."
    )
    edit_mode: EditMode = Field(
        description=(
            "fresh = brand new draft. revise = rewrite a prior_draft with "
            "(potentially) new sources. instruct = micro-edit to a prior_draft "
            "(tone, length, single section) with no new evidence."
        )
    )
    plan_md: str = Field(
        default="",
        description=(
            "The plan the user approved via present_plan_for_approval, OR the "
            "plan the planner committed to without asking (clean-turn path). "
            "Becomes WriterPackage.plan_md. May be empty for trivial instruct edits."
        ),
    )
    selected_wis: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace item aliases the planner wants the executor to see, "
            'as ``WI-{seq}`` strings (e.g. ``["WI-1", "WI-3"]``). Order '
            "matters — the package rendering preserves it within each role. "
            "Use the labels shown in <attached_items> / <prior_artifacts>; "
            "never emit a raw UUID. The runner resolves aliases against "
            "deps.wi_alias_map before invoking walkers."
        ),
    )
    role_assignments: dict[str, PlannerRole] = Field(
        default_factory=dict,
        description=(
            'WI-{seq} alias → role (template / source / reference / '
            'prior_draft). Keys are the same ``"WI-{seq}"`` strings used in '
            "selected_wis. Every alias in selected_wis should have an entry; "
            "the runner defaults to 'source' for missing entries and logs a "
            "warning."
        ),
    )
    analyzer_invoked: bool = Field(
        default=False,
        description=(
            "True iff the planner called analyze_items during this turn. "
            "Drives the runner's package-assembly branch: True → verdict-walk "
            "from deps.last_analyzer_output; False → bypass path (need='full' "
            "with raw content_md). See § Two skippable phases."
        ),
    )
    aborted: bool = Field(
        default=False,
        description=(
            "Set True when the user's reply after a pause is so off-script that "
            "no plan can be built. The orchestrator returns the rationale as a "
            "chat message without invoking the executor."
        ),
    )
    rationale: str = Field(
        default="",
        description="Short Arabic justification — logged + surfaced in telemetry.",
    )

    def tracking_output(self) -> dict:
        """Bounded telemetry view (agents/utils/tracking.py protocol) — keeps the
        full plan_md text out of span attributes."""
        return {
            "subtype": self.subtype,
            "edit_mode": self.edit_mode,
            "plan_md_chars": len(self.plan_md or ""),
            "selected_wis": list(self.selected_wis),
            "analyzer_invoked": self.analyzer_invoked,
            "aborted": self.aborted,
            "intent_ar_chars": len(self.intent_ar or ""),
        }

    @field_validator("selected_wis")
    @classmethod
    def _dedupe_preserving_order(cls, v: list[str]) -> list[str]:
        """Drop duplicate WI-{seq} aliases while preserving first-seen order."""
        seen: set[str] = set()
        out: list[str] = []
        for alias in v:
            if alias and alias not in seen:
                seen.add(alias)
                out.append(alias)
        return out


__all__ = ["PlannerDecision", "PlannerRole", "EditMode"]
