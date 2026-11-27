"""«بحث موسّع» — the orchestrator wiring (build spec package C; plan §4.1, §4.3, §4.4).

Two moments, each reached from ``agents/orchestrator.py`` through one small call site:

* **The planning turn** — :func:`plan_extended_turn`, called by ``_run_deep_search`` on a
  FRESH deep-search turn (no decision, no editorial pin, reached from ``_dispatch``) that
  the ROUTER chose as «بحث موسّع» (``DispatchAgent.extended``, D34 — 2026-11-27; the
  router is told whether it is available by ``router_view``).
  Flag off → ``None`` at once, no database read. On: eligibility (D30, D23, D24) → the
  planner's view → the angle-aware planner. One request → the plan is dropped and
  today's planner runs from scratch (``None``, D6). Two or more → room in the workspace
  for every report + the merged answer, or today's path; then the plan card: a ``paused_runs`` row
  (``pause_reason="extended_plan"``, ``deferred_payload["extended_plan"]`` = the stored
  :class:`ExtendedPlanPause`) and the assistant row carrying the card; the turn then ends
  on ``agent_question`` like an ``ask_user`` pause. Any failure → ``None`` = today's
  planner, silently (Q16).

* **The reply to a card** — :func:`answer_card_reply` + :func:`run_card_reply`, called by
  ``_handle_message_inner``'s pause check BEFORE routing. A button (``plan_choice`` with
  the card's ``run_id``) or the exact text of a button label runs that option — after
  the points are re-read and the option's budget mode still fits (§4.3 "at the click");
  anything else typed resolves the card ("typed") and the router takes the message
  (D32); an expired card resolves as "expired" and the router takes the message. A button whose card is no longer live
  (expired, resolved, another card) gets a short Arabic reply and runs nothing.

Every outcome is read-merged into the card row (``metadata.extended_plan.outcome`` /
``outcome_at``) and logged (``deep_search.extended.outcome``) — the data D30 says to
revisit the design with.

The A / B modules are imported lazily through the thin seams below, so the orchestrator
pays nothing for them while the flag is off and tests can replace each one.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, AsyncGenerator, Callable, Mapping, Sequence

from shared.observability import get_logfire

from .models import (
    CARD_METADATA_KEY,
    OPTION_LABELS_AR,
    PAUSE_REASON,
    ExtendedPlan,
    ExtendedPlanPause,
    ExtendedRequest,
    StoredOption,
)

logger = logging.getLogger(__name__)
_logfire = get_logfire()

AGENT_FAMILY = "deep_search"
#: Same life as an ``ask_user`` pause (``_record_deferred``).
PAUSE_TTL = timedelta(hours=24)
#: ``paused_runs.deferred_payload`` keys beside ``CARD_METADATA_KEY`` (server only).
CARD_MESSAGE_KEY = "card_message_id"
ANALYZER_KEY = "analyzer"
#: The Logfire event every outcome is logged under (D30's data).
OUTCOME_EVENT = "deep_search.extended.outcome"
#: A one-search plan was dropped for today's planner (D6).
SINGLE_SEARCH_EVENT = "deep_search.extended.single_search_fallback"

#: Progress poll interval — the orchestrator's ``_PROGRESS_POLL_S``.
_POLL_S = 0.2
#: The click's run lives under message_service's ``LUNA_PIPELINE_TIMEOUT_S`` (900 s by
#: default), and the reports are published only AFTER the merge — so the merge gets what
#: the run has left minus this reserve for publishing + the chat summary. Past it the merge
#: is dropped and the reports are published alone (the merger-failure path): a slow merge
#: must never cost the user the reports they paid for. Measured 2026-11-24 offline: merges
#: of 304–379 s, and one of 1,079 s on Wafer at peak (38.7k output tokens at ~36 tok/s).
MERGE_RESERVE_S = 150.0
#: With less than this left the merge is not started at all.
MERGE_MIN_S = 90.0


def merge_budget_s(started: float) -> float:
    """Seconds the merge may take for a run that started at ``started`` (perf_counter)."""
    try:
        from shared.config import get_settings

        total = float(get_settings().LUNA_PIPELINE_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        total = 900.0
    return total - (perf_counter() - started) - MERGE_RESERVE_S

DEFAULT_TASK_LABEL_AR = "بحث موسّع"

EXPIRED_REPLY_AR = "انتهت صلاحية هذه الخطة. أعد إرسال طلبك لأجهّز لك خطة جديدة."
#: §4.3 at the click: the option's budget mode no longer fits what is left (D23).
INSUFFICIENT_REPLY_AR = (
    "لم يعد رصيدك المتبقي من النقاط يغطي هذه الخطة، فلم يُنفَّذ أي بحث. "
    "يمكنك إعادة إرسال طلبك لإجراء «بحث عادي»، أو إرسال طلب جديد."
)
#: «بحث عادي» at the click with no points left at all.
INSUFFICIENT_ORDINARY_REPLY_AR = (
    "نفد رصيدك من النقاط، فلم يُنفَّذ أي بحث. أعد إرسال طلبك بعد تجدّد رصيدك."
)
#: ``_dispatch``'s own cap refusal, word for word (the conversation is already full).
WORKSPACE_CAP_REPLY_AR = (
    "وصلت إلى الحد الأقصى من المستندات في هذه المحادثة (15). "
    "يرجى حذف مستند قبل إنشاء جديد."
)
#: Room left, but less than the plan's reports + its merged answer.
WORKSPACE_FULL_REPLY_AR = (
    "هذه الخطة تنشئ {new} مستندات في مساحة العمل، وفي هذه المحادثة {count} من أصل {cap}. "
    "يرجى حذف بعض المستندات ثم إعادة إرسال طلبك."
)
MAP_GONE_REPLY_AR = (
    "تعذّر تنفيذ الخطة لأن المستندات المرفقة تغيّرت بعد عرضها. يرجى إعادة إرسال طلبك."
)
RUN_FAILED_REPLY_AR = "عذراً، تعذّر إكمال البحث الموسّع. يرجى المحاولة مرة أخرى."
SUMMARY_FALLBACK_AR = "اكتمل البحث الموسّع، والجواب المدمج وتقاريره محفوظة في مساحة العمل."


# --------------------------------------------------------------------------- seams (A / B, lazy)


def _eligibility(supabase: Any, *, user_id: str, gate: Any, attached_item_ids: Sequence[str]) -> Any:
    from .eligibility import check_eligibility

    return check_eligibility(supabase, user_id=user_id, gate=gate, attached_item_ids=attached_item_ids)


def _planner_view(supabase: Any, angle_map: dict, *, user_id: str) -> Any:
    from .view import build_planner_view

    return build_planner_view(supabase, angle_map, user_id=user_id)


async def _plan(view: Any, *, query: str, recent_messages: Any, mode: Any, user_id: str, supabase: Any) -> Any:
    from .planner import run_extended_planner

    return await run_extended_planner(
        view, query=query, recent_messages=recent_messages, mode=mode, user_id=user_id, supabase=supabase,
    )


def _card_parts(plan: ExtendedPlan, *, planner_points: float, run_id: str, points_left: float | None) -> tuple:
    """``(options, card, text)`` — A's card module."""
    from .card import build_card, build_options, card_text_ar

    options = build_options(plan, planner_points=planner_points)
    card = build_card(options, plan, run_id=run_id, points_left=points_left)
    return options, card, card_text_ar(card)


def _load_angle_map(supabase: Any, map_id: str) -> dict:
    from agents.memory.angle_mapper.store import _load_map

    return _load_map(supabase, map_id)


def _localize(supabase: Any, angle_map: dict, item_ids: list[str]) -> dict:
    from agents.memory.angle_mapper.library import localize_angle_map

    return localize_angle_map(supabase, angle_map, item_ids) or angle_map


async def _searches(requests: Sequence[ExtendedRequest], **kw: Any) -> list:
    from .runner import run_extended_searches

    return await run_extended_searches(requests, **kw)


async def _merge(reports: Sequence[Any], **kw: Any) -> Any:
    from .merger import merge_reports

    return await merge_reports(reports, **kw)


def _single_answer(report: Any) -> Any:
    from .merger import single_report_answer

    return single_report_answer(report)


async def _publish(reports: Sequence[Any], merged: Any, **kw: Any) -> Any:
    from .publish import publish_extended

    return await publish_extended(reports, merged, **kw)


async def _summary(merged: Any, **kw: Any) -> Any:
    from .publish import write_chat_summary

    return await write_chat_summary(merged, **kw)


def _flag_on(user_id: str | None) -> bool:
    from agents.utils.extended_search_flags import extended_search_enabled

    return extended_search_enabled(user_id)


def _turn_gate() -> Any:
    from shared import quota

    return quota.active_deep_search_gate()


def _orch() -> Any:
    from agents import orchestrator

    return orchestrator


def _set(span: Any, **attrs: Any) -> None:
    try:
        span.set_attributes({f"extended.{k}": v for k, v in attrs.items() if v is not None})
    except Exception:  # noqa: BLE001 - telemetry never breaks a turn
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- the planning turn


@dataclass
class ExtendedTurn:
    """What ``_dispatch`` knows about the turn that ``_run_deep_search`` does not."""

    run_id: str
    user_message_id: str | None = None
    task_label: str = ""


@dataclass
class PlannedTurn:
    """The turn paused on a plan card; ``question`` = the card's text (the
    ``agent_question`` event)."""

    question: str


def _attached_ids(items: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(str(s.item_id) for s in items or [] if getattr(s, "item_id", None)))


def _decoded(value: Any) -> Any:
    """Every string decoded with the turn codec — stored rows hold reals (store-real)."""
    from backend.app.services.masking_service import active_codec, decode_text

    codec = active_codec()
    if isinstance(value, str):
        return decode_text(codec, value, emit=False)
    if isinstance(value, list):
        return [_decoded(v) for v in value]
    if isinstance(value, dict):
        return {k: _decoded(v) for k, v in value.items()}
    return value


def record_extended_pause(
    supabase: Any,
    *,
    input: Any,
    turn: ExtendedTurn,
    pause: ExtendedPlanPause,
    card: Any,
    text: str,
) -> str | None:
    """Persist the card: the ``paused_runs`` row, then the assistant row that shows it.

    Returns the card text (the ``agent_question`` event's question) or ``None`` when
    either write failed — the caller then runs today's path (a card nobody can see must
    not hold the conversation's pause slot, so a failed message insert resolves the row).
    """
    from agents.paused_runs import PauseRecord, record_pause, resolve_pause
    from agents.utils.reply_guard import scrub_reply

    now = datetime.now(timezone.utc)
    card_message_id = str(uuid.uuid4())
    stored_text = scrub_reply(_decoded(text))
    run_id = record_pause(
        supabase,
        PauseRecord(
            user_id=input.user_id,
            conversation_id=input.conversation_id,
            case_id=input.case_id,
            agent_family=AGENT_FAMILY,
            task_label=turn.task_label or None,
            pause_reason=PAUSE_REASON,
            message_history=None,
            deferred_payload={
                CARD_METADATA_KEY: pause.model_dump(mode="json"),
                CARD_MESSAGE_KEY: card_message_id,
                # Deps do not survive a pause (same key as _record_deferred): «بحث عادي»
                # and the plan's searches keep the turn's analyzer state.
                ANALYZER_KEY: {
                    "handoff": getattr(input, "analyzer_handoff", None),
                    "checked": bool(getattr(input, "analyzer_checked", False)),
                },
            },
            question_text=stored_text,
            asked_at=now,
            expires_at=now + PAUSE_TTL,
        ),
        run_id=turn.run_id,
    )
    if not run_id:
        return None
    try:
        supabase.table("messages").insert({
            "message_id": card_message_id,
            "conversation_id": input.conversation_id,
            "role": "assistant",
            "content": stored_text,
            "metadata": {
                "kind": "agent_question",
                "run_id": run_id,
                "agent_family": AGENT_FAMILY,
                "pause_reason": PAUSE_REASON,
                CARD_METADATA_KEY: _decoded(card.model_dump(mode="json")),
            },
        }).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("extended_search: card message insert failed (%s) — today's path", exc)
        resolve_pause(supabase, run_id)
        return None
    return stored_text


async def plan_extended_turn(input: Any, supabase: Any, *, turn: ExtendedTurn) -> PlannedTurn | None:
    """The extended planner on a fresh deep-search turn, or ``None`` = today's planner.

    Flag off → ``None`` before anything else (no read). No attachments → ``None`` (no map
    can cover nothing). A one-search plan → ``None`` (D6: today's path exactly — today's
    planner decides from scratch). No room in the workspace for the plan's reports + its
    merged answer → ``None`` (no card the click could only refuse). Never raises except
    on cancellation.
    """
    if not _flag_on(getattr(input, "user_id", None)):
        return None
    item_ids = _attached_ids(getattr(input, "attached_items", None) or [])
    if not item_ids:
        return None
    with _logfire.span(
        "deep_search.extended.plan",
        conversation_id=input.conversation_id,
        attached_count=len(item_ids),
    ) as span:
        try:
            elig = await asyncio.to_thread(
                _eligibility, supabase, user_id=input.user_id, gate=_turn_gate(), attached_item_ids=item_ids,
            )
        except Exception as exc:  # noqa: BLE001 - eligibility never raises; belt and braces
            logger.warning("extended_search: eligibility raised (%s) — today's path", type(exc).__name__)
            _set(span, result="eligibility_error")
            return None
        mode = getattr(elig, "mode", None)
        _set(
            span,
            eligible=bool(elig.eligible),
            reason=str(elig.reason),
            plan_id=elig.plan_id,
            points_left=None if elig.points_left is None else round(float(elig.points_left), 2),
            budget_mode=getattr(mode, "label", None),
        )
        logger.info(
            "extended_search: eligibility=%s reason=%s plan=%s points_left=%s mode=%s",
            elig.eligible, elig.reason, elig.plan_id, elig.points_left, getattr(mode, "label", None),
        )
        if not elig.eligible:
            return None

        # The smallest extended plan adds 3 workspace items (2 reports + the merged
        # answer): with no room for that, skip the planner — its cost would buy a card
        # that can never be shown.
        orch = _orch()
        try:
            count_before = await asyncio.to_thread(orch._count_artifact_kinds, supabase, input.conversation_id)
        except Exception:  # noqa: BLE001 - the post-plan check below still guards the card
            count_before = 0
        if count_before + 3 > orch._WORKSPACE_CAP:
            _set(span, result="workspace_full_before_plan", workspace_count=count_before)
            logger.info(
                "extended_search: workspace holds %d of %d — no room for an extended plan — today's path",
                count_before, orch._WORKSPACE_CAP,
            )
            return None

        t0 = perf_counter()
        try:
            view = await asyncio.to_thread(_planner_view, supabase, elig.angle_map, user_id=input.user_id)
            run = await _plan(
                view,
                query=input.describe_query,
                recent_messages=list(input.recent_messages or []),
                mode=mode,
                user_id=input.user_id,
                supabase=supabase,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure = today's planner
            logger.warning("extended_search: planner failed (%s) — today's path", type(exc).__name__, exc_info=True)
            _set(span, result="planner_failed", error=type(exc).__name__)
            return None

        plan: ExtendedPlan = run.plan
        planner_points = float(getattr(run, "points", 0.0) or 0.0)
        _set(
            span,
            requests=len(plan.requests),
            cheaper=plan.cheaper is not None,
            planner_points=round(planner_points, 3),
            planner_s=round(perf_counter() - t0, 1),
        )

        if len(plan.requests) == 1:
            # D6 — "a one-search plan runs as today": the plan is dropped and today's
            # planner decides from scratch, with every phase-1 check it makes (the long-
            # message review, the already-covered revert, the hand-back to the router).
            single_mode = plan.requests[0].mode
            _set(span, result="single_search_fallback", mode=single_mode)
            try:
                _logfire.info(
                    SINGLE_SEARCH_EVENT,
                    conversation_id=input.conversation_id,
                    mode=single_mode,
                    planner_points=round(planner_points, 3),
                )
            except Exception:  # noqa: BLE001 - telemetry never breaks a turn
                pass
            logger.info("extended_search: one-search plan (mode=%s) dropped — today's planner", single_mode)
            return None

        # The click refuses a plan the workspace cannot hold (one item per report + the
        # merged answer — _run_option's pre-flight), so no card is shown for one.
        new_items = len(plan.requests) + 1
        count = await asyncio.to_thread(orch._count_artifact_kinds, supabase, input.conversation_id)
        if count + new_items > orch._WORKSPACE_CAP:
            _set(span, result="workspace_full", workspace_count=count)
            logger.info(
                "extended_search: no card — the workspace holds %d of %d and the plan adds %d — today's path",
                count, orch._WORKSPACE_CAP, new_items,
            )
            return None

        try:
            options, card, text = _card_parts(
                plan, planner_points=planner_points, run_id=turn.run_id, points_left=elig.points_left,
            )
            pause = ExtendedPlanPause(
                run_id=turn.run_id,
                map_id=str(view.map_id),
                budget_mode=str(getattr(mode, "label", "none")),
                points_left=elig.points_left,
                describe_query=input.describe_query,
                user_message_id=turn.user_message_id,
                attached_item_ids=item_ids,
                task_label=turn.task_label or "",
                plan=plan,
                options=list(options),
                planner_points=planner_points,
                created_at=_now_iso(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("extended_search: card build failed (%s) — today's path", exc, exc_info=True)
            _set(span, result="card_failed")
            return None

        question = record_extended_pause(supabase, input=input, turn=turn, pause=pause, card=card, text=text)
        if question is None:
            _set(span, result="record_failed")
            return None
        _set(
            span,
            result="card",
            options=",".join(o.id for o in options),
            estimates=",".join(f"{o.id}:{round(o.estimate_points, 1)}" for o in options),
        )
        logger.info(
            "extended_search: plan card run_id=%s searches=%d options=%s",
            turn.run_id, len(plan.requests), [o.id for o in options],
        )
        return PlannedTurn(question=question)


# --------------------------------------------------------------------------- the reply to a card


_TASHKEEL_TATWEEL = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ALEF_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})


def normalize_label(text: Any) -> str:
    """Typed text as compared with a button label (D32 fallback): trimmed, tashkeel and
    tatweel stripped, alef forms unified, inner whitespace collapsed."""
    t = _TASHKEEL_TATWEEL.sub("", str(text or "")).translate(_ALEF_FORMS)
    return " ".join(t.split())


def _plan_choice(raw: Any) -> tuple[str, str] | None:
    """``(run_id, option)`` from a ``plan_choice`` (mapping or model), or ``None``."""
    if raw is None:
        return None
    get = raw.get if isinstance(raw, Mapping) else (lambda k: getattr(raw, k, None))
    run_id, option = str(get("run_id") or "").strip(), str(get("option") or "").strip()
    if not run_id or option not in OPTION_LABELS_AR:
        return None
    return run_id, option


@dataclass
class CardReply:
    """What the message after a card does.

    ``kind``: ``"run"`` — run ``option`` of ``pause``; ``"expired"`` — a button of a card
    that is no longer live: short reply, nothing runs; ``"route"`` — a normal turn through
    the router (``pending_resolved`` = the card's pause row was resolved here).
    """

    kind: str
    run_id: str = ""
    option: str = ""
    via: str = ""
    pause: ExtendedPlanPause | None = None
    card_message_id: str | None = None
    analyzer: dict = field(default_factory=dict)
    pending_resolved: bool = False


def _find_card_message_id(supabase: Any, conversation_id: str, run_id: str) -> str | None:
    """The card row of ``run_id`` when its pause row is gone. Never raises."""
    try:
        rows = (
            supabase.table("messages")
            .select("message_id, metadata")
            .eq("conversation_id", conversation_id)
            .eq("role", "assistant")
            .eq("metadata->>run_id", run_id)
            .limit(5)
            .execute()
        ).data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("extended_search: card lookup failed (%s)", type(exc).__name__)
        return None
    for row in rows:
        meta = row.get("metadata") or {}
        if isinstance(meta, dict) and isinstance(meta.get(CARD_METADATA_KEY), dict):
            return str(row.get("message_id") or "") or None
    return None


def record_outcome(
    supabase: Any,
    *,
    conversation_id: str,
    run_id: str,
    outcome: str,
    card_message_id: str | None = None,
) -> bool:
    """Read-merge ``outcome`` / ``outcome_at`` into the card row's
    ``metadata.extended_plan``. The first outcome wins (an existing one is kept).
    Best effort — never raises; ``False`` when nothing was written."""
    message_id = card_message_id or _find_card_message_id(supabase, conversation_id, run_id)
    if not message_id:
        return False
    try:
        rows = (
            supabase.table("messages")
            .select("metadata")
            .eq("message_id", message_id)
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        ).data or []
        if not rows:
            return False
        meta = rows[0].get("metadata")
        meta = dict(meta) if isinstance(meta, dict) else {}
        card = meta.get(CARD_METADATA_KEY)
        card = dict(card) if isinstance(card, dict) else {}
        if card.get("outcome"):
            return False
        card["outcome"] = outcome
        card["outcome_at"] = _now_iso()
        meta[CARD_METADATA_KEY] = card
        supabase.table("messages").update({"metadata": meta}).eq("message_id", message_id).eq(
            "conversation_id", conversation_id
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("extended_search: outcome %s not saved on the card (%s)", outcome, type(exc).__name__)
        return False


def log_outcome(outcome: str, *, run_id: str, conversation_id: str, **attrs: Any) -> None:
    """One outcome, as a Logfire event + a log line (user_id stays off, as on dispatch)."""
    gate = _turn_gate()
    payload = {
        "outcome": outcome,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "plan_id": getattr(gate, "plan_id", None),
        **{k: v for k, v in attrs.items() if v is not None},
    }
    try:
        _logfire.info(OUTCOME_EVENT, **payload)
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "extended_search: outcome=%s run_id=%s %s",
        outcome, run_id, " ".join(f"{k}={v}" for k, v in payload.items() if k not in ("outcome", "run_id")),
    )


def tag_user_reply(supabase: Any, user_message_id: str | None, *, run_id: str, option: str) -> None:
    """The user row that answered a card with a button (or a label's exact text):
    ``{"kind": "agent_answer", "run_id", "plan_choice"}`` — the same overwrite today's
    resume does. Best effort."""
    if not user_message_id:
        return
    try:
        supabase.table("messages").update(
            {"metadata": {"kind": "agent_answer", "run_id": run_id, "plan_choice": option}}
        ).eq("message_id", user_message_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("extended_search: could not tag the reply %s (%s)", user_message_id, exc)


def answer_card_reply(
    supabase: Any,
    pending: dict | None,
    *,
    reply: str,
    plan_choice: Any,
    user_id: str,
    conversation_id: str,
    user_message_id: str | None,
    current_placeholder_id: str | None,
) -> CardReply:
    """Decide what the message after a plan card does (sync; DB writes best effort).

    Called when the conversation's open pause is a card OR the message carries a
    ``plan_choice``. Records every outcome but the option's own (``run_card_reply`` does
    that once the points are re-read).
    """
    orch = _orch()
    choice = _plan_choice(plan_choice)
    card_pending = (
        pending if pending and str(pending.get("pause_reason") or "") == PAUSE_REASON else None
    )

    def _expired_button(clicked_run_id: str, option: str, *, why: str, resolved: bool) -> CardReply:
        record_outcome(supabase, conversation_id=conversation_id, run_id=clicked_run_id, outcome="expired")
        tag_user_reply(supabase, user_message_id, run_id=clicked_run_id, option=option)
        log_outcome("expired", run_id=clicked_run_id, conversation_id=conversation_id, via="button", why=why)
        return CardReply(kind="expired", run_id=clicked_run_id, option=option, via="button", pending_resolved=resolved)

    if card_pending is None:
        if choice is None:  # a malformed plan_choice beside some other pause: today's flow
            return CardReply(kind="route")
        return _expired_button(*choice, why="no_live_card", resolved=False)

    run_id = str(card_pending.get("run_id") or "")
    payload = card_pending.get("deferred_payload") or {}
    payload = payload if isinstance(payload, Mapping) else {}
    card_message_id = str(payload.get(CARD_MESSAGE_KEY) or "") or None

    def _close(outcome: str, where: str, **attrs: Any) -> None:
        record_outcome(
            supabase, conversation_id=conversation_id, run_id=run_id, outcome=outcome,
            card_message_id=card_message_id,
        )
        orch._resolve_pause_loud(supabase, run_id, where=where)
        log_outcome(outcome, run_id=run_id, conversation_id=conversation_id, **attrs)

    dead: str | None = None
    pause: ExtendedPlanPause | None = None
    if orch._expired(card_pending):
        dead = "expired"
    elif not _flag_on(user_id):
        dead = "flag_off"
    elif not orch._pause_is_current(supabase, card_pending, conversation_id, current_placeholder_id):
        _logfire.warning("paused_runs.stale_pause_dropped", run_id=run_id, conversation_id=conversation_id)
        dead = "stale"
    else:
        try:
            pause = ExtendedPlanPause.model_validate(payload.get(CARD_METADATA_KEY) or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("extended_search: unreadable card pause %s (%s)", run_id, type(exc).__name__)
            dead = "unreadable"

    if dead is not None:
        _close("expired", f"extended_plan_{dead}", via="button" if choice else "typed", why=dead)
        if choice is None:
            return CardReply(kind="route", run_id=run_id, pending_resolved=True)
        if choice[0] != run_id:
            record_outcome(supabase, conversation_id=conversation_id, run_id=choice[0], outcome="expired")
        tag_user_reply(supabase, user_message_id, run_id=choice[0], option=choice[1])
        return CardReply(kind="expired", run_id=choice[0], option=choice[1], via="button", pending_resolved=True)

    assert pause is not None
    option_ids = [o.id for o in pause.options]
    raw_analyzer = payload.get(ANALYZER_KEY)
    analyzer = dict(raw_analyzer) if isinstance(raw_analyzer, Mapping) else {}

    if choice is not None:
        if choice[0] != run_id:
            # A button of another card while this card waits: the click runs nothing,
            # and the reply ends this card too (it is no longer the last reply).
            _close("expired", "extended_plan_other_button", via="button", why="other_card")
            return _expired_button(*choice, why="other_card", resolved=True)
        if choice[1] not in option_ids:
            # An option this card never offered (no cheaper one): runs nothing.
            _close("expired", "extended_plan_unknown_option", via="button", why="unknown_option")
            tag_user_reply(supabase, user_message_id, run_id=run_id, option=choice[1])
            return CardReply(kind="expired", run_id=run_id, option=choice[1], via="button", pending_resolved=True)
        return CardReply(
            kind="run", run_id=run_id, option=choice[1], via="button", pause=pause,
            card_message_id=card_message_id, analyzer=dict(analyzer),
        )

    typed = normalize_label(reply)
    for option_id in option_ids:
        if typed and typed == normalize_label(OPTION_LABELS_AR.get(option_id, "")):
            return CardReply(
                kind="run", run_id=run_id, option=option_id, via="typed", pause=pause,
                card_message_id=card_message_id, analyzer=dict(analyzer),
            )

    # D32 — anything else is a normal turn through the router; the plan expires.
    _close("typed", "extended_plan_typed", via="typed", reply_chars=len(reply or ""))
    return CardReply(kind="route", run_id=run_id, pending_resolved=True)


# --------------------------------------------------------------------------- running a choice


def history_before_card(messages: Sequence[Any] | None) -> list[Any]:
    """The conversation as the planning turn saw it: the click's history minus its last
    two rows (the card, then the click) — what the searches and the merger read as "the
    conversation before" the user's message (D17). Unchanged when the tail is not
    assistant-then-user."""
    rows = list(messages or [])
    if len(rows) >= 2 and getattr(rows[-1], "role", None) == "user" and getattr(rows[-2], "role", None) == "assistant":
        return rows[:-2]
    return rows


def make_deps_factory(
    input: Any,
    supabase: Any,
    *,
    http_client: Any,
    detail_level: str,
) -> Callable[[int], Any]:
    """``index → fresh PlannerDeps`` built exactly as ``_run_deep_search`` builds the turn's
    (same loaders, same ``build_planner_deps`` arguments; the loads run once). Each call
    returns a NEW deps with its own lists and its own copies of the attached snapshots —
    ``run_retrieval`` writes onto the deps it is given. ``emit_sse`` is left unset: the
    runner wires each search to its slot of the combined stream."""
    from agents.deep_search_v4.planner import build_planner_deps
    from agents.utils.embeddings import embed_regulation_query_alibaba
    from backend.app.services.masking_service import active_codec, persist_new_mappings
    from shared.config import get_settings

    orch = _orch()
    settings = get_settings()
    case_brief = (
        orch._load_case_brief(supabase, input.case_id, input.user_id) if input.case_id else None
    )
    prior_searches = orch._load_prior_search_summaries(supabase, input.conversation_id, input.user_id)
    wi_alias_map: dict[int, str] = {}
    for prior in prior_searches:
        if prior.wi_seq is not None and prior.item_id:
            wi_alias_map[int(prior.wi_seq)] = prior.item_id
    for snap in input.attached_items:
        if snap.wi_seq is not None and snap.item_id:
            wi_alias_map[int(snap.wi_seq)] = snap.item_id
    planner_attached = orch._encode_attached_for_planner(list(input.attached_items))
    persist_new_mappings(supabase, input.user_id, active_codec())

    def factory(_index: int) -> Any:
        deps = build_planner_deps(
            supabase=supabase,
            embedding_fn=embed_regulation_query_alibaba,
            http_client=http_client,
            jina_api_key=settings.JINA_RERANKER_API_KEY or "",
            detail_level=detail_level,
            user_id=input.user_id,
            conversation_id=input.conversation_id,
            case_brief=case_brief,
            recent_messages=list(input.recent_messages),
            compaction_summary_md=input.compaction_summary_md,
            conversation_memory_md=input.conversation_memory_md,
            prior_searches=list(prior_searches),
            attached_items=[s.model_copy(deep=True) for s in planner_attached],
            wi_alias_map=dict(wi_alias_map),
            welcome_instruction=input.welcome_instruction,
            emit_sse=None,
            router_context=getattr(input, "router_context", None),
            welcome=getattr(input, "welcome", None),
            analyzer_handoff=input.analyzer_handoff,
            analyzer_checked=input.analyzer_checked,
        )
        deps.after_handback = False
        deps.handback_allowed = False
        return deps

    return factory


async def _drive(
    coro: Any, queue: "asyncio.Queue[dict]", box: dict, *, deadline: float | None = None,
) -> AsyncGenerator[dict, None]:
    """Run ``coro`` as a task and yield its translated progress while it works (the
    orchestrator's queue bridge). Its result lands in ``box["result"]``; it re-raises.

    ``deadline`` (``perf_counter`` time): past it, ``TimeoutError`` — the task is
    cancelled and NOT awaited, so a model call that ignores the cancellation cannot hold
    the turn (``asyncio.wait_for`` waits for the cancelled task; a hung call once outlived
    it — angle mapper, 2026-11-23)."""
    orch = _orch()
    task = asyncio.create_task(coro)
    try:
        while not task.done():
            if deadline is not None and perf_counter() >= deadline:
                raise TimeoutError("deadline passed")
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=_POLL_S)
            except asyncio.TimeoutError:
                continue
            prog = orch._progress_from_planner_event(raw)
            if prog is not None:
                yield prog
        while True:
            try:
                raw = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            prog = orch._progress_from_planner_event(raw)
            if prog is not None:
                yield prog
        box["result"] = await task
    finally:
        if not task.done():
            task.cancel()


def _merged_mode(requests: Sequence[ExtendedRequest]) -> str:
    modes = {r.mode for r in requests}
    return modes.pop() if len(modes) == 1 else "full"


def option_fits(option: StoredOption, *, points_left: float | None, plan_id: str | None) -> tuple[bool, Any]:
    """§4.3 at the click: may ``option`` still run on the points left NOW? Returns
    ``(fits, mode_now)``.

    «بحث عادي» is today's search: it fits whenever some points are left (or the account
    is unlimited) — today's own gates still run inside ``_dispatch``. A plan / cheaper
    option fits when code's mode for what is left now (D23 ``pick_mode`` on the
    EFFECTIVE plan) exists and is either no budget (D33) or holds the option's requests:
    no more searches than ``max_searches``, no more ``full`` than ``max_full``, every
    ``max_sub_queries`` ≤ ``max_per_side`` (an uncapped request never fits a budget mode).
    The estimate is never compared (D25: the actual is charged)."""
    if option.id == "ordinary":
        if points_left is None:
            return True, None
        try:
            return float(points_left) >= 0, None
        except (TypeError, ValueError):
            return False, None
    from .budget import pick_mode

    mode = pick_mode(points_left, plan_id)
    if mode is None:
        return False, None
    if mode.no_budget:
        return True, mode
    requests = list(option.requests)
    fits = (
        len(requests) <= int(mode.max_searches)
        and sum(1 for r in requests if r.mode == "full") <= int(mode.max_full)
        and all(r.max_sub_queries is not None and r.max_sub_queries <= int(mode.max_per_side) for r in requests)
    )
    return fits, mode


def dispatch_refusal(events: Sequence[dict]) -> tuple[str, str]:
    """``(outcome, why)`` of a «بحث عادي» whose ``_dispatch`` ended before starting the
    search — its two pre-flight refusals: the deep-search plan gate (a
    ``deep_search_gated`` event / ``done`` model) → ``"insufficient"``; the workspace
    cap (``done`` model ``cap_rejected``) → ``"expired"``. Anything else → ``"expired"``."""
    for ev in events:
        if ev.get("type") == "deep_search_gated":
            return "insufficient", "deep_search_gate"
    for ev in events:
        if ev.get("type") == "done":
            model = (ev.get("usage") or {}).get("model")
            if model == "deep_search_gated":
                return "insufficient", "deep_search_gate"
            if model == "cap_rejected":
                return "expired", "workspace_cap"
    return "expired", "dispatch_refused"


async def run_card_reply(
    reply: CardReply,
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None,
    memory_task: "asyncio.Task | None" = None,
) -> AsyncGenerator[dict, None]:
    """Stream the turn for an ``"expired"`` or ``"run"`` :class:`CardReply`."""
    from agents.utils.usage_sink import bind_run_id

    orch = _orch()
    if reply.kind == "expired":
        yield {"type": "token", "text": EXPIRED_REPLY_AR}
        yield {"type": "done", "usage": orch._zero_usage("extended_plan_expired")}
        return
    with bind_run_id(reply.run_id), _logfire.span(
        "deep_search.extended.choice",
        conversation_id=conversation_id,
        run_id=reply.run_id,
        option=reply.option,
        via=reply.via,
    ) as span:
        async for ev in _run_option(
            reply, span=span, supabase=supabase, user_id=user_id, conversation_id=conversation_id,
            case_id=case_id, user_message_id=user_message_id,
            assistant_message_id=assistant_message_id, memory_task=memory_task,
        ):
            yield ev


async def _run_option(
    reply: CardReply,
    *,
    span: Any,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None,
    memory_task: "asyncio.Task | None",
) -> AsyncGenerator[dict, None]:
    orch = _orch()
    pause = reply.pause
    assert pause is not None
    run_id, option = reply.run_id, reply.option
    stored: StoredOption = next(o for o in pause.options if o.id == option)
    tag_user_reply(supabase, user_message_id, run_id=run_id, option=option)

    gate = _turn_gate()
    points_left = getattr(gate, "remaining", None) if gate is not None else None
    plan_id = getattr(gate, "plan_id", None) if gate is not None else None
    # §4.3 at the click — the points are re-read (another conversation may have spent
    # some) and code picks the budget mode again (D23). The estimate is for display
    # only (D25: the actual cost is charged) and is never compared here.
    fits, mode_now = option_fits(stored, points_left=points_left, plan_id=plan_id)
    log_attrs = {
        "via": reply.via,
        "budget_mode": pause.budget_mode,
        "mode_now": getattr(mode_now, "label", None),
        "estimate": round(stored.estimate_points, 2),
        "points_left": None if points_left is None else round(float(points_left), 2),
        "searches": len(stored.requests) or 1,
    }
    _set(span, plan_id=plan_id, **log_attrs)

    def _record(outcome: str, **extra: Any) -> None:
        record_outcome(
            supabase, conversation_id=conversation_id, run_id=run_id, outcome=outcome,
            card_message_id=reply.card_message_id,
        )
        log_outcome(outcome, run_id=run_id, conversation_id=conversation_id, option=option, **log_attrs, **extra)
        _set(span, outcome=outcome)

    def _close(outcome: str, where: str, **extra: Any) -> None:
        orch._resolve_pause_loud(supabase, run_id, where=where)
        _record(outcome, **extra)

    if not fits:
        _close("insufficient", "extended_plan_insufficient")
        yield {
            "type": "token",
            "text": INSUFFICIENT_ORDINARY_REPLY_AR if option == "ordinary" else INSUFFICIENT_REPLY_AR,
        }
        yield {"type": "done", "usage": orch._zero_usage("extended_plan_insufficient")}
        return

    if option == "ordinary":
        # D31 — today's ordinary deep search on the message, exactly: _dispatch runs it
        # (cap, plan gate, run id, progress, pause / revert / delivery) with the extended
        # planner off for this run. The card is resolved now; its outcome waits for
        # _dispatch: "ordinary" only once the search starts — a pre-flight refusal
        # (workspace cap / deep-search gate) streams today's reply and records what
        # stopped it.
        orch._resolve_pause_loud(supabase, run_id, where="extended_plan_ordinary")
        held: list[dict] = []   # _dispatch's events until it starts the search or refuses
        decided = False
        try:
            yield {"type": "agent_resumed", "run_id": run_id, "agent_family": AGENT_FAMILY}
            await orch._settle_memory_writer(memory_task, conversation_id, consumer="deep_planner")
            compaction = orch._load_compaction_summary(supabase, conversation_id, user_id)
            memory_md = orch._load_conversation_memory(supabase, conversation_id, user_id)
            async for ev in orch._dispatch(
                agent_family=AGENT_FAMILY,
                describe_query=pause.describe_query,
                task_label=pause.task_label or DEFAULT_TASK_LABEL_AR,
                target_item_id=None,
                attached_item_ids=list(pause.attached_item_ids),
                subtype=None,
                supabase=supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                compaction_summary_md=compaction,
                conversation_memory_md=memory_md,
                analyzer_handoff=reply.analyzer.get("handoff"),
                analyzer_checked=bool(reply.analyzer.get("checked")),
                extended_allowed=False,
            ):
                if decided:
                    yield ev
                    continue
                held.append(ev)
                if ev.get("type") == "agent_run_started":
                    decided = True
                    _record("ordinary")
                elif ev.get("type") == "done":
                    decided = True
                    outcome, why = dispatch_refusal(held)
                    _record(outcome, why=why)
                else:
                    continue
                for held_ev in held:
                    yield held_ev
                held = []
            if not decided:  # ended with neither a start nor a ``done``
                decided = True
                _record("expired", why="dispatch_refused")
                for held_ev in held:
                    yield held_ev
        finally:
            if not decided:  # closed / cancelled before _dispatch said either way
                _record("expired", why="aborted")
        return

    # ── plan / cheaper ──────────────────────────────────────────────────────
    # Workspace cap (_dispatch's pre-flight): the run adds one item per report + the
    # merged answer — there must be room for all of them before anything runs, so a
    # plan is never half-published.
    requests = list(stored.requests)
    new_items = len(requests) + (1 if len(requests) > 1 else 0)
    count = orch._count_artifact_kinds(supabase, conversation_id)
    if count + new_items > orch._WORKSPACE_CAP:
        _close("expired", "extended_plan_workspace_full", why="workspace_full", workspace_count=count)
        yield {
            "type": "token",
            "text": WORKSPACE_CAP_REPLY_AR if count >= orch._WORKSPACE_CAP
            else WORKSPACE_FULL_REPLY_AR.format(new=new_items, count=count, cap=orch._WORKSPACE_CAP),
        }
        yield {"type": "done", "usage": orch._zero_usage("cap_rejected")}
        return

    gated = await orch._check_deep_search_gate(
        supabase, user_id, entry="extended_choice", conversation_id=conversation_id,
    )
    if gated is not None:
        _close("insufficient", "extended_plan_gated", why="deep_search_gate")
        for ev in orch._gated_events(gated):
            yield ev
        yield {"type": "done", "usage": orch._zero_usage("deep_search_gated")}
        return

    try:
        angle_map = await asyncio.to_thread(_load_angle_map, supabase, pause.map_id)
        if not angle_map:
            raise LookupError("angle map gone")
        # A map a «الملفات» copy carried: its members back on this conversation's
        # copies, as when the card was planned (eligibility._best_fresh_map).
        angle_map = await asyncio.to_thread(_localize, supabase, angle_map, list(pause.attached_item_ids))
        view = await asyncio.to_thread(_planner_view, supabase, angle_map, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - the map changed since the card
        logger.warning("extended_search: plan %s cannot run — map %s (%s)", run_id, pause.map_id, exc)
        _close("expired", "extended_plan_map_gone", why="map_gone")
        yield {"type": "token", "text": MAP_GONE_REPLY_AR}
        yield {"type": "done", "usage": orch._zero_usage("extended_plan_map_gone")}
        return

    _close(option, f"extended_plan_{option}")
    yield {"type": "agent_resumed", "run_id": run_id, "agent_family": AGENT_FAMILY}
    yield orch._make_progress(
        "planning", "تجهيز البحوث المتوازية", searches={"total": len(requests), "done": 0},
    )
    async for ev in _run_plan(
        pause, option, requests, view, span=span, supabase=supabase, user_id=user_id,
        conversation_id=conversation_id, case_id=case_id, analyzer=reply.analyzer,
        assistant_message_id=assistant_message_id, memory_task=memory_task,
    ):
        yield ev
    yield {"type": "agent_run_finished", "agent_family": AGENT_FAMILY}


async def _run_plan(
    pause: ExtendedPlanPause,
    option: str,
    requests: list[ExtendedRequest],
    view: Any,
    *,
    span: Any,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    analyzer: dict,
    assistant_message_id: str | None,
    memory_task: "asyncio.Task | None",
) -> AsyncGenerator[dict, None]:
    """The searches → merger → publish → chat summary, streamed like today's resume leg."""
    import httpx

    from agents.models import MajorAgentInput, SpecialistResult
    from backend.app.services.preferences_service import get_detail_level

    orch = _orch()
    t0 = perf_counter()
    plan = pause.plan if option == "plan" else pause.plan.model_copy(update={"requests": list(requests)})
    try:
        await orch._settle_memory_writer(memory_task, conversation_id, consumer="deep_planner")
        recent = history_before_card(
            orch._load_recent_messages(supabase, conversation_id, user_id=user_id, full=True)
        )
        major_input = MajorAgentInput(
            describe_query=pause.describe_query,
            task_label=pause.task_label or DEFAULT_TASK_LABEL_AR,
            attached_items=orch._load_attached_items(
                supabase, list(pause.attached_item_ids), user_id, conversation_id,
            ),
            recent_messages=recent,
            compaction_summary_md=orch._load_compaction_summary(supabase, conversation_id, user_id),
            conversation_memory_md=orch._load_conversation_memory(supabase, conversation_id, user_id),
            target_item_id=None,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            analyzer_handoff=analyzer.get("handoff"),
            analyzer_checked=bool(analyzer.get("checked")),
        )
        try:
            detail_level = get_detail_level(supabase, user_id)
        except Exception:  # noqa: BLE001
            detail_level = "medium"

        queue: asyncio.Queue[dict] = asyncio.Queue()
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            factory = make_deps_factory(major_input, supabase, http_client=http_client, detail_level=detail_level)

            box: dict = {}
            async for prog in _drive(
                _searches(
                    requests, plan=plan, view=view, query=pause.describe_query,
                    deps_factory=factory, emit_sse=queue.put_nowait,
                ),
                queue, box,
            ):
                yield prog
            reports = list(box["result"])
            ok = [r for r in reports if r.ok]
            _set(span, reports_ok=len(ok), reports=len(reports))
            if not ok:
                raise RuntimeError("no search of the plan produced a report")

            merged_item: Any = None   # what publish stores as the merged answer (≥ 2 reports)
            answer: Any               # what the chat summary is written from
            if len(ok) >= 2:
                budget = merge_budget_s(t0)
                _set(span, merge_budget_s=round(budget))
                try:
                    if budget < MERGE_MIN_S:
                        raise TimeoutError(f"no time left to merge ({budget:.0f}s)")
                    box = {}
                    async for prog in _drive(
                        _merge(
                            reports, query=pause.describe_query, recent_messages=recent, plan=plan,
                            view=view, supabase=supabase, user_id=user_id, conversation_id=conversation_id,
                        ),
                        queue, box, deadline=perf_counter() + budget,
                    ):
                        yield prog
                    merged_item = answer = box["result"]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the reports stand on their own
                    logger.error("extended_search: merger failed (%s) — reports only", exc, exc_info=True)
                    _set(span, merger_failed=type(exc).__name__)
                    answer = _single_answer(ok[0])
            else:
                answer = _single_answer(ok[0])

            published = await _publish(
                reports, merged_item, supabase=supabase, user_id=user_id,
                conversation_id=conversation_id, case_id=case_id, message_id=assistant_message_id,
                query=pause.describe_query, plan=plan, detail_level=detail_level,
                task_label=pause.task_label or None, run_id=pause.run_id,
            )

            summary_deps = factory(0)
            summary_deps.emit_sse = queue.put_nowait
            box = {}
            try:
                async for prog in _drive(
                    _summary(answer, query=pause.describe_query, deps=summary_deps, mode=_merged_mode(requests)),
                    queue, box,
                ):
                    yield prog
                turn = box.get("result")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the answer is published; say so
                logger.error("extended_search: chat summary failed (%s)", exc, exc_info=True)
                turn = None

        response = getattr(turn, "response", None)
        chat_summary = (getattr(response, "chat_summary_md", "") or "") if response is not None else ""
        result = SpecialistResult(
            output_item_id=published.merged_item_id or next(iter(published.report_item_ids), None),
            chat_summary=chat_summary or SUMMARY_FALLBACK_AR,
            next_steps=orch._coerce_next_steps(getattr(response, "next_steps", None)) if response is not None else [],
            sse_events=list(published.sse_events),
            model_used="deep_search_extended",
            per_phase_stats={
                "extended": {
                    "run_id": pause.run_id,
                    "option": option,
                    "reports": [(r.index, r.ok, r.error[:80]) for r in reports],
                    "merged_item_id": published.merged_item_id,
                },
            },
            follow_up_question=getattr(turn, "follow_up_question", None),
            question_basis=getattr(turn, "question_basis", None),
        )
        yield orch._make_progress(
            "done",
            "اكتمل البحث",
            sources=sum(int(getattr(r, "sources", 0) or 0) for r in reports),
            elapsed_s=round(perf_counter() - t0, 1),
            searches={"total": len(reports), "done": len(ok)},
        )
        _set(span, status="ok", merged_item_id=published.merged_item_id, seconds=round(perf_counter() - t0, 1))
        async for ev in orch._delivery_events(result, AGENT_FAMILY, supabase, user_id, None):
            yield ev
    except asyncio.CancelledError:
        logger.info("extended_search: run %s cancelled mid-run", pause.run_id)
        _set(span, status="cancelled")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("extended_search: run %s failed: %s", pause.run_id, exc, exc_info=True)
        _set(span, status="error", error=type(exc).__name__)
        yield {"type": "token", "text": RUN_FAILED_REPLY_AR}
        yield {"type": "done", "usage": orch._zero_usage("error")}


__all__ = [
    "CardReply",
    "ExtendedTurn",
    "PlannedTurn",
    "answer_card_reply",
    "dispatch_refusal",
    "history_before_card",
    "make_deps_factory",
    "normalize_label",
    "plan_extended_turn",
    "record_extended_pause",
    "option_fits",
    "record_outcome",
    "run_card_reply",
]
