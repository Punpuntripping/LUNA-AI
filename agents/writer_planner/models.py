"""Pydantic schemas for the writer_planner decider.

The decider's ``output_type`` is the discriminated list
``[PlannerDecision, DeferredToolRequests]``:

- Normal final emission → :class:`PlannerDecision`. The runner fetches the
  full ``content_md`` (plus the used-reference manifest) for every WI in
  ``selected_wis`` and assembles a ``WriterPackage`` from this decision —
  one deterministic path, no LLM triage.
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

from agents.writer.models import CaseParty, WriterSubtype


PlannerRole = Literal["template", "source", "reference", "prior_draft"]
EditMode = Literal["fresh", "revise", "instruct"]


class PlannerDecision(BaseModel):
    """Final decision the writer_planner-decider emits when planning is done.

    The runner consumes this to assemble the ``WriterPackage``: it takes
    ``selected_wis``, fetches each WI's ``content_md`` from Supabase (plus the
    used-reference manifest for refs-family items), and builds ``AnalyzedItem``
    records with ``need='full'`` and the raw content as ``body_md``. One
    deterministic path — the old item_analyzer triage step was removed; the
    planner inspects content live via ``unfold_workspace_item`` instead.

    ``role_assignments`` maps ``WI-{seq}`` alias → role. The decider is
    responsible for assigning a role to every alias it puts in
    ``selected_wis``; the runner falls back to ``'source'`` if a mapping is
    missing (lenient, but logged).

    **Alias contract** (see ``.claude/plans/agent_communication_protocol.md``):
    ``selected_wis`` and the keys of ``role_assignments`` are ``WI-{seq}``
    aliases (e.g. ``"WI-3"``) — never raw UUIDs. The runner resolves these
    against ``deps.wi_alias_map`` before invoking walkers, which stay
    UUID-based against the DB.
    """

    parties: list[CaseParty] = Field(
        default_factory=list,
        description=(
            "Confirmed case parties extracted or validated this turn "
            "(via ask_user or explicit user statement). Each entry has "
            "name + role (Arabic). Populated ONLY after the party-validation "
            "check; left empty when the document involves no named persons. "
            "Passed verbatim to WriterPackage.parties and rendered as a "
            "<parties> block for the executor — it MUST use these names."
        ),
    )
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
    chosen_template: str | None = Field(
        default=None,
        description=(
            "A قوالبي template to draft FROM, as a ``TPL-{n}`` alias (e.g. "
            '"TPL-2") drawn from the <my_templates> block — never a raw UUID. '
            "None when no library template applies (the user attached their own "
            "role='template' item, or no قوالب fit). The runner resolves the "
            "alias → template_id and fetches the body into the WriterPackage."
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
    offer_save: bool = Field(
        default=False,
        description=(
            "Set True to offer the user (non-blocking) to save an attached "
            "document as a reusable قوالبي template. Surfaces an «احفظ كقالب؟» "
            "chip in chat AFTER the draft publishes — it NEVER pauses. Only set "
            "when a document attached THIS turn looks reusable AND the user "
            "did not already ask to save it."
        ),
    )
    offer_item_id: str | None = Field(
        default=None,
        description=(
            "The ``WI-{seq}`` alias of the attached item to offer for saving "
            "(required when offer_save=True). The runner resolves it to the "
            "workspace_item UUID it puts in the template_save_offer SSE event."
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
            "chosen_template": self.chosen_template,
            "aborted": self.aborted,
            "offer_save": self.offer_save,
            "intent_ar_chars": len(self.intent_ar or ""),
            "parties_count": len(self.parties),
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


__all__ = ["PlannerDecision", "PlannerRole", "EditMode", "CaseParty"]
