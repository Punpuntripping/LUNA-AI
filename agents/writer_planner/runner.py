"""Turn orchestrator for the writer_planner.

Public entrypoint: :func:`handle_writer_planner_turn`. Called from the
orchestrator's ``_run_writer`` (replaces the legacy direct-writer
invocation per `.claude/plans/writer_planner.md` § Orchestrator wiring
change).

Returns :class:`WriterPlannerTurnResult` — either ``kind='completed'``
(planner finished; writing executor drafted; row published) or
``kind='paused'`` (planner called ``ask_user`` or
``present_plan_for_approval``; orchestrator must persist the deferred
state and surface the question_text to chat).

Pause-resume contract:
    Fresh dispatch:    handle_writer_planner_turn(major_input, supabase)
    Resume:            handle_writer_planner_turn(major_input, supabase,
                                                  message_history=...,
                                                  deferred_results=...)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from agents.models import MajorAgentInput, SpecialistResult
from agents.writer import (
    WriterInput,
    build_writer_deps,
    handle_writer_turn,
    publish_writer_result,
)
from agents.writer.models import (
    AnalyzedItem,
    TemplateRef,
    WriterPackage,
    WriterStyle,
    WriterSubtype,
)
from backend.app.services.preferences_service import get_detail_level
from backend.app.services.writer_planner_context import (
    load_writer_planner_context,
)

from .agent import WRITER_PLANNER_LIMITS, create_writer_planner_decider
from .deps import WriterPlannerDeps, build_writer_planner_deps
from .models import PlannerDecision, PlannerRole
from .walkers import (
    build_analyzed_items_direct,
    build_analyzed_items_from_verdicts,
)

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai import DeferredToolRequests
    from pydantic_ai.messages import ModelMessage
    from supabase import Client as SupabaseClient


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Turn-result envelope
# ---------------------------------------------------------------------------


@dataclass
class WriterPlannerTurnResult:
    """What :func:`handle_writer_planner_turn` returns.

    ``kind='completed'`` — the executor drafted + published. ``result`` is the
    SpecialistResult the orchestrator returns to the caller.

    ``kind='paused'`` — the planner's run ended with a DeferredToolRequests.
    ``planner_result`` (raw AgentRunResult) + ``deferred`` carry the pause
    state; ``pause_reason`` is the tool-name-derived value the orchestrator
    writes to ``agent_runs.pause_reason`` (migration 053).
    """

    kind: Literal["completed", "paused"]
    # completed
    result: SpecialistResult | None = None
    # paused
    planner_result: Any = None
    deferred: "DeferredToolRequests | None" = None
    pause_reason: Literal["clarify", "approve_plan"] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pause_reason_for_tool(tool_name: str) -> Literal["clarify", "approve_plan"]:
    """Map a deferred tool name to the agent_runs.pause_reason value."""
    if tool_name == "present_plan_for_approval":
        return "approve_plan"
    return "clarify"  # default — covers ask_user + any future deferred-with-no-mapping


def _resolve_decision_aliases(
    decision: PlannerDecision,
    deps: WriterPlannerDeps,
) -> tuple[list[str], dict[str, PlannerRole]]:
    """Convert WI-{seq} aliases on a PlannerDecision into UUIDs.

    The decider emits ``selected_wis`` (list of aliases) and
    ``role_assignments`` keyed by alias. Walkers, DB queries, and
    downstream agents all operate on UUIDs — this helper closes the gap.

    Behaviour:
      - Resolves each alias in ``decision.selected_wis`` via
        ``deps.resolve_wi_alias``.
      - Unknown aliases are dropped from the selection with a warning
        (defense-in-depth — the analyze_items tool already raises
        ModelRetry during the agent run for the same condition, but a
        decider that bypassed analyze_items can still produce a stale
        alias on the final emission).
      - The role map is re-keyed alias → UUID. Aliases without a UUID
        resolution are dropped silently; aliases that resolved to a UUID
        already present in the map (collision) keep the first role seen.

    Returns:
        (selected_uuids, role_assignments_by_uuid) — both ready for the
        walkers' UUID-based signatures.
    """
    selected_uuids: list[str] = []
    seen_uuids: set[str] = set()
    alias_to_uuid: dict[str, str] = {}
    for alias in decision.selected_wis:
        uuid = deps.resolve_wi_alias(alias)
        if uuid is None:
            logger.warning(
                "writer_planner: dropping unresolvable alias %r from "
                "selected_wis (not in conversation alias map)",
                alias,
            )
            continue
        alias_to_uuid[alias] = uuid
        if uuid in seen_uuids:
            # Duplicate after resolution — preserve first occurrence's order.
            continue
        seen_uuids.add(uuid)
        selected_uuids.append(uuid)

    role_map_by_uuid: dict[str, PlannerRole] = {}
    for alias, role in decision.role_assignments.items():
        uuid = alias_to_uuid.get(alias) or deps.resolve_wi_alias(alias)
        if uuid is None:
            logger.warning(
                "writer_planner: dropping role assignment for unresolvable "
                "alias %r (role=%s)",
                alias, role,
            )
            continue
        # First role wins on collision.
        role_map_by_uuid.setdefault(uuid, role)

    return selected_uuids, role_map_by_uuid


async def _build_writer_planner_deps_from_input(
    major_input: MajorAgentInput,
    supabase: "SupabaseClient",
    http_client: httpx.AsyncClient,
    *,
    assistant_message_id: str | None = None,
) -> WriterPlannerDeps:
    """Hydrate WriterPlannerDeps from MajorAgentInput + DB loaders.

    Loads conversation-scope prior_artifacts via writer_planner_context. The
    style fields fall back to user_preferences (detail_level). Never raises:
    a missing prior_artifacts load returns [].

    ``assistant_message_id`` is threaded onto ``WriterPlannerDeps.message_id``
    and downstream to ``publish_writer_result`` so the produced
    workspace_item gets ``message_id`` populated (chat ↔ artifact linkage).
    """
    # Style: detail_level from user_preferences; tone defaults to 'formal' (v1).
    try:
        detail_level = get_detail_level(supabase, major_input.user_id)
    except Exception:
        logger.warning(
            "writer_planner: get_detail_level failed; defaulting to 'medium'",
            exc_info=True,
        )
        detail_level = "medium"
    style = WriterStyle(detail_level=detail_level, tone="formal")  # type: ignore[arg-type]

    prior_artifacts = await load_writer_planner_context(
        supabase=supabase,
        user_id=major_input.user_id,
        conversation_id=major_input.conversation_id,
    )

    return build_writer_planner_deps(
        supabase=supabase,
        http_client=http_client,
        user_id=major_input.user_id,
        conversation_id=major_input.conversation_id,
        message_id=assistant_message_id,
        turn_number=0,    # incremented across resumes by orchestrator if needed
        intent=major_input.describe_query,
        recent_messages=list(major_input.recent_messages),
        case_brief=None,  # v1 — placeholder; case_brief loader is in a separate plan
        attached_items=list(major_input.attached_items),
        prior_artifacts=prior_artifacts,
        style=style,
    )


async def _build_package_from_decision(
    decision: PlannerDecision,
    deps: WriterPlannerDeps,
    *,
    selected_uuids: list[str],
    role_assignments_by_uuid: dict[str, PlannerRole],
    system_templates: list[TemplateRef] | None = None,
) -> WriterPackage:
    """Assemble a WriterPackage from a finalized PlannerDecision + deps.

    Branches on ``decision.analyzer_invoked``:
      - True  → walker reads ``deps.last_analyzer_output`` and builds
        AnalyzedItems from verdicts.
      - False → walker bypass: fetches content_md for each selected id and
        builds need='full' items directly.

    ``selected_uuids`` and ``role_assignments_by_uuid`` are the
    alias-resolver's outputs — walkers operate on UUIDs end-to-end (DB
    queries, last_analyzer_output verdict keys). The aliases the planner
    emitted in ``decision.selected_wis`` are decoupled from the walker
    surface at this seam.

    system_templates: optional pre-fetched TemplateRef list (the planner
    may have called search_templates during its run; the tool returns the
    refs but doesn't stash them on deps because there's no need — the
    runner re-fetches once at package-build time if the decider didn't
    surface a user template). For v1 we always pass an empty list; the
    plan ships with system_templates having zero ingested rows so this
    won't hurt.
    """
    if decision.analyzer_invoked:
        analyzed = await build_analyzed_items_from_verdicts(
            analyzer_output=deps.last_analyzer_output,
            selected_ids=selected_uuids,
            role_assignments=role_assignments_by_uuid,
            deps=deps,
        )
    else:
        analyzed = await build_analyzed_items_direct(
            selected_ids=selected_uuids,
            role_assignments=role_assignments_by_uuid,
            deps=deps,
        )

    return WriterPackage(
        intent_ar=decision.intent_ar,
        subtype=decision.subtype,
        edit_mode=decision.edit_mode,
        plan_md=decision.plan_md,
        analyzed_items=analyzed,
        system_templates=list(system_templates or []),
        style=deps.style,
    )


def _aborted_result(decision: PlannerDecision) -> SpecialistResult:
    """Return a SpecialistResult for the rare aborted path (off-script reply)."""
    return SpecialistResult(
        output_item_id=None,
        chat_summary=(
            decision.rationale
            or "تعذّر بناء خطّة كتابة من ردّك الأخير. حاول إعادة الصياغة بمعطيات أوضح."
        ),
        key_findings=[],
        sse_events=[],
        model_used="writer_planner_decider",
        tokens_in=0,
        tokens_out=0,
        per_phase_stats={},
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def handle_writer_planner_turn(
    major_input: MajorAgentInput,
    supabase: "SupabaseClient",
    *,
    message_history: "list[ModelMessage] | None" = None,
    deferred_results: Any = None,
    subtype_hint: str | None = None,
    assistant_message_id: str | None = None,
) -> WriterPlannerTurnResult:
    """Run one writer_planner turn — fresh dispatch OR resume.

    Args:
        major_input: Router-emitted MajorAgentInput (describe_query +
            attached_items + recent_messages + user/conversation ids).
        supabase: Sync Supabase client.
        message_history: Set on RESUME. The orchestrator loads
            ``agent_runs.message_history`` (bytes) and passes the deserialized
            ModelMessage list. None for fresh dispatches.
        deferred_results: Set on RESUME. The user's reply wrapped in a
            DeferredToolResults({tool_call_id: reply}) by the orchestrator.
        subtype_hint: Optional hint from the router (e.g. ``'contract'``).
            Currently unused — the planner derives subtype from the
            intent. Accepted for forward-compat with orchestrator wiring.
        assistant_message_id: FK to the assistant-message placeholder
            allocated by message_service. Threaded to
            ``WriterPlannerDeps.message_id`` and to the publisher's
            ``WriterInput.message_id`` so the published workspace_item
            carries the message_id linkage. ``None`` is tolerated (the row
            then publishes with NULL — same behavior as pre-Phase-3, kept
            for legacy / standalone-script entry points).

    Returns:
        WriterPlannerTurnResult.
    """
    t0 = time.perf_counter()
    _ = subtype_hint  # forward-compat; the decider picks subtype itself

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        # --- 1. Hydrate deps ----------------------------------------------
        deps = await _build_writer_planner_deps_from_input(
            major_input, supabase, http_client,
            assistant_message_id=assistant_message_id,
        )

        # --- 2. Run agent (fresh or resume) -------------------------------
        agent = create_writer_planner_decider()
        run_kwargs: dict[str, Any] = {
            "deps": deps,
            "usage_limits": WRITER_PLANNER_LIMITS,
        }
        if message_history is not None:
            run_kwargs["message_history"] = message_history
        if deferred_results is not None:
            run_kwargs["deferred_tool_results"] = deferred_results

        # Fresh dispatch sends the user prompt; resume relies on history.
        user_prompt = major_input.describe_query if message_history is None else None

        try:
            result = await agent.run(user_prompt, **run_kwargs) if user_prompt else await agent.run(**run_kwargs)
        except Exception as exc:
            logger.exception("writer_planner: agent.run() raised — %s", exc)
            # Degraded fallback: surface a chat-level error instead of crashing.
            return WriterPlannerTurnResult(
                kind="completed",
                result=SpecialistResult(
                    output_item_id=None,
                    chat_summary=(
                        "حدث خطأ أثناء التخطيط للكتابة. حاول إعادة الطلب بعد قليل."
                    ),
                    key_findings=[],
                    sse_events=[],
                    model_used="writer_planner_decider",
                    tokens_in=0,
                    tokens_out=0,
                    per_phase_stats={"error": str(exc)},
                ),
            )

        output = result.output

        # --- 3. Pause branch ---------------------------------------------
        # Lazy import — DeferredToolRequests is from pydantic_ai which the
        # type-check block also references.
        from pydantic_ai import DeferredToolRequests

        if isinstance(output, DeferredToolRequests):
            # Determine pause_reason from the (single) deferred tool call.
            tool_name = ""
            calls = getattr(output, "calls", None) or []
            if calls:
                tool_name = str(getattr(calls[0], "tool_name", "") or "")
            pause_reason = _pause_reason_for_tool(tool_name)
            logger.info(
                "writer_planner: paused (tool=%s pause_reason=%s present_count=%d)",
                tool_name, pause_reason, deps.present_count,
            )
            return WriterPlannerTurnResult(
                kind="paused",
                planner_result=result,
                deferred=output,
                pause_reason=pause_reason,
            )

        # --- 4. Final decision ------------------------------------------
        if not isinstance(output, PlannerDecision):
            logger.error(
                "writer_planner: unexpected output type %r — degrading",
                type(output).__name__,
            )
            return WriterPlannerTurnResult(
                kind="completed",
                result=SpecialistResult(
                    output_item_id=None,
                    chat_summary="تعذّر إكمال التخطيط للكتابة في هذه المحاولة.",
                    key_findings=[],
                    sse_events=[],
                    model_used="writer_planner_decider",
                    tokens_in=0,
                    tokens_out=0,
                    per_phase_stats={},
                ),
            )

        decision = output

        if decision.aborted:
            logger.info(
                "writer_planner: decision.aborted=True — surfacing rationale to chat"
            )
            return WriterPlannerTurnResult(
                kind="completed",
                result=_aborted_result(decision),
            )

        # --- 5. Resolve WI-{seq} aliases → UUIDs (alias protocol) --------
        # The decider speaks aliases (selected_wis is list[WI-{seq}],
        # role_assignments is dict[WI-{seq}, role]); walkers + DB queries
        # speak UUIDs. Defensive resolution at this seam — analyze_items
        # already rejects unknown aliases via ModelRetry during the run,
        # so this rarely drops anything in practice.
        selected_uuids, role_map_by_uuid = _resolve_decision_aliases(
            decision, deps
        )
        logger.info(
            "writer_planner: resolved %d/%d selected aliases → UUIDs",
            len(selected_uuids), len(decision.selected_wis),
        )

        # --- 6. Build WriterPackage from decision ------------------------
        package = await _build_package_from_decision(
            decision,
            deps,
            selected_uuids=selected_uuids,
            role_assignments_by_uuid=role_map_by_uuid,
            system_templates=None,
        )

        # --- 7. Run the writing executor on the package ------------------
        exec_deps = build_writer_deps(
            supabase=supabase,
            http_client=http_client,
            describe_query=decision.intent_ar,
            task_label=major_input.task_label,
            attached_items=list(major_input.attached_items),
            revising_item_id=(
                package.prior_draft().item_id
                if package.prior_draft()
                else major_input.target_item_id
            ),
            detail_level=deps.style.detail_level,
            tone=deps.style.tone,
        )

        llm_output = await handle_writer_turn(package, exec_deps)

        # --- 8. Publish ---------------------------------------------------
        # The package path of handle_writer_turn does NOT publish;
        # the planner runner owns publication per § Publisher relocation.
        # Build a WriterInput envelope via from_package for the
        # publisher's input contract.
        publish_input = WriterInput.from_package(  # type: ignore[attr-defined]
            package,
            user_id=major_input.user_id,
            conversation_id=major_input.conversation_id,
            case_id=major_input.case_id,
            message_id=assistant_message_id,
            revising_item_id=exec_deps.revising_item_id,
        )

        writer_output = await publish_writer_result(
            llm_output, publish_input, exec_deps
        )

        duration = time.perf_counter() - t0
        logger.info(
            "writer_planner: completed turn item_id=%s analyzer=%s "
            "present=%d items=%d duration=%.2fs",
            writer_output.item_id,
            decision.analyzer_invoked,
            deps.present_count,
            len(package.analyzed_items),
            duration,
        )

        return WriterPlannerTurnResult(
            kind="completed",
            result=SpecialistResult(
                output_item_id=writer_output.item_id,
                chat_summary=writer_output.chat_summary or "",
                key_findings=list(writer_output.key_findings or []),
                sse_events=list(writer_output.sse_events or []),
                model_used=writer_output.metadata.get(
                    "model_used", "writer_planner_decider"
                ),
                tokens_in=0,
                tokens_out=0,
                per_phase_stats={
                    "analyzer_invoked": decision.analyzer_invoked,
                    "present_count": deps.present_count,
                    "selected_aliases": len(decision.selected_wis),
                    "selected_uuids": len(selected_uuids),
                    "package_items": len(package.analyzed_items),
                },
            ),
        )


__all__ = [
    "WriterPlannerTurnResult",
    "handle_writer_planner_turn",
]
