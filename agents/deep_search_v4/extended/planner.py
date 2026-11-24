"""The extended planner («بحث موسّع», plan §4.2) — one pydantic-ai run per eligible turn.

Slot ``extended_planner`` (V4.1 on Wafer, D2 — the model the 11-23 budget planner ran
on). Input = ``view.render_planner_input`` (D28 + the budget block), instructions =
``prompts.system_prompt(mode)``. Output = :class:`~.models.ExtendedPlan`, structured
(with the JSON-in-text salvager every V4.1 structured agent carries).

**Validator** (``ModelRetry``, as tested): every angle id and section ref exists; under
a budget mode the plan AND its cheaper option stay inside the mode's limits (searches,
``full`` searches, the per-side cap) and every request names ``max_sub_queries``; under
no budget (D33) only the ids are checked. Section refs are normalised to ``D1.S7`` (a
bare ``S7`` becomes ``D1.S7`` when the map has one document).

**Masking (وضع السرية):** the input is encoded with the turn's active codec (idempotent:
the message and the history are already intake-encoded; titles and angle text get
their fakes here), new fakes are persisted BEFORE the model reads them, and every text
field of the output is decoded before it leaves this module. Telemetry records the
encoded output only.

**Billing:** one ``llm_calls`` row ``deep_search.extended_planner`` through the usage
sink — on success AND on failure (tokens spent on a failed run are charged like any
other call); ``PlannerRun.points`` = that row's cost × 100.

Raises on any failure (the caller logs and falls back to today's planner).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import RunUsage, UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.tracking import track_stage
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .budget import NO_BUDGET_MAX_SEARCHES
from .models import BudgetMode, ExtendedPlan, ExtendedRequest, PlannerView
from .prompts import RETRY_PREFIX, SALVAGE_RETRY, system_prompt
from .view import render_planner_input

logger = logging.getLogger(__name__)

SLOT = "extended_planner"
#: The ``llm_calls.agent`` of the planner's row (build spec rule 9).
LEDGER_AGENT = "deep_search.extended_planner"
AGENT_FAMILY = "deep_search"
#: Per-response output limit sent to the model: Wafer's maximum (as tested). The run
#: still stays under the shared ``OUTPUT_TOKENS_CAP`` — never a per-agent cap.
MAX_OUTPUT_TOKENS = 65_536
#: Output-validation retries (as tested): the first answer + 3 → at most 4 requests.
OUTPUT_RETRIES = 3
LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=1 + OUTPUT_RETRIES)
#: The whole run's ceiling (tested: 19–44 s per plan; a retry can double it).
PLANNER_TIMEOUT_S = 180.0
_POINTS_PER_USD = 100.0

_ANGLE_ID = re.compile(r"^\s*[Aa]\s*0*(\d+)\s*$")


@dataclass
class PlannerRun:
    plan: ExtendedPlan          # decoded
    points: float               # the call's cost in points (USD × 100), as billed
    usage: Any                  # pydantic_ai RunUsage
    model: str                  # the model that answered (or the slot's head)


# --------------------------------------------------------------------------- validation


def _canonical_angle(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    m = _ANGLE_ID.match(text)
    return f"A{int(m.group(1))}" if m else text


def _normalise_request(req: ExtendedRequest, *, single_doc: bool) -> None:
    from agents.memory.angle_mapper.refs import qualify

    req.angles = list(dict.fromkeys(_canonical_angle(a) for a in req.angles))
    req.sections = list(dict.fromkeys(qualify(s, single_doc) for s in req.sections))


def normalise_plan(plan: ExtendedPlan, view: PlannerView) -> ExtendedPlan:
    """Angle ids → ``A7``; section refs → ``D1.S7`` (a bare ``S7`` → ``D1.S7`` on a
    one-document map). In place; returns ``plan``."""
    single = len(view.documents) == 1
    for req in plan.requests:
        _normalise_request(req, single_doc=single)
    if plan.cheaper:
        for req in plan.cheaper.requests:
            _normalise_request(req, single_doc=single)
    for lo in plan.left_out:
        lo.angle = _canonical_angle(lo.angle)
    return plan


def limits_problems(
    requests: Sequence[ExtendedRequest], mode: BudgetMode, view: PlannerView, what: str,
) -> list[str]:
    """What is wrong with one option (the plan or the cheaper one) — English, for the
    model's retry. Under no budget only the ids (and the Q18 ceiling, when set)."""
    out: list[str] = []
    reqs = list(requests)
    if mode.no_budget:
        if NO_BUDGET_MAX_SEARCHES is not None and len(reqs) > NO_BUDGET_MAX_SEARCHES:
            out.append(f"{what}: {len(reqs)} searches > the ceiling of {NO_BUDGET_MAX_SEARCHES}")
    else:
        if len(reqs) > mode.max_searches:
            out.append(f"{what}: {len(reqs)} searches > the budget's {mode.max_searches}")
        n_full = sum(r.mode == "full" for r in reqs)
        if n_full > mode.max_full:
            out.append(f"{what}: {n_full} `full` searches > the budget's {mode.max_full}")
        missing = [i for i, r in enumerate(reqs, 1) if r.max_sub_queries is None]
        if missing:
            out.append(f"{what}: max_sub_queries is required (a number) in request(s) {missing}")
        over = [i for i, r in enumerate(reqs, 1) if r.max_sub_queries is not None and r.max_sub_queries > mode.max_per_side]
        if over:
            out.append(f"{what}: max_sub_queries above the budget's {mode.max_per_side} in request(s) {over}")
    known_angles = {a.id for a in view.angles}
    bad_a = sorted({a for r in reqs for a in r.angles} - known_angles)
    bad_s = sorted({s for r in reqs for s in r.sections} - set(view.section_refs))
    if bad_a or bad_s:
        out.append(f"{what}: unknown angle ids {bad_a} / section refs {bad_s}")
    return out


def plan_problems(plan: ExtendedPlan, view: PlannerView, mode: BudgetMode) -> list[str]:
    """Every problem of the plan and of its cheaper option (empty = valid)."""
    bad = limits_problems(plan.requests, mode, view, "plan")
    if plan.cheaper:
        bad += limits_problems(plan.cheaper.requests, mode, view, "cheaper option")
    return bad


# --------------------------------------------------------------------------- the agent


def create_extended_planner(view: PlannerView, mode: BudgetMode) -> Agent[None, ExtendedPlan]:
    """The planner for one turn: the mode decides the instructions and the limits the
    validator holds the plan to; the view decides the ids that exist."""
    agent: Agent[None, ExtendedPlan] = Agent(
        get_agent_model(SLOT),
        name="extended_planner",
        output_type=[ExtendedPlan, TextOutput(make_json_salvager(ExtendedPlan, retry_msg=SALVAGE_RETRY))],
        instructions=system_prompt(mode),
        retries=1,
        output_retries=OUTPUT_RETRIES,
        model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
    )

    @agent.output_validator
    def _check(ctx: RunContext[None], plan: ExtendedPlan) -> ExtendedPlan:
        plan = normalise_plan(plan, view)
        bad = plan_problems(plan, view, mode)
        if bad:
            raise ModelRetry(RETRY_PREFIX + "; ".join(bad))
        return plan

    return agent


# --------------------------------------------------------------------------- masking


def _active_codec() -> Any:
    try:
        from backend.app.services.masking_service import active_codec

        return active_codec()
    except Exception:  # noqa: BLE001 — passthrough
        return None


def _encode(codec: Any, text: str) -> str:
    if codec is None or not text:
        return text
    try:
        return codec.encode(text)
    except Exception:  # noqa: BLE001
        logger.debug("extended_planner: encode failed — raw text", exc_info=True)
        return text


def _persist_fakes(supabase: Any, user_id: str, codec: Any) -> None:
    """New fakes reach the DB before the model reads them (a resumed run decodes)."""
    if codec is None or not user_id or not getattr(codec, "new_mappings", None):
        return
    try:
        if supabase is None:
            from shared.db.client import get_supabase_client

            supabase = get_supabase_client()
        from backend.app.services.masking_service import persist_new_mappings

        persist_new_mappings(supabase, str(user_id), codec)
    except Exception:  # noqa: BLE001
        logger.warning("extended_planner: masking fakes not persisted", exc_info=True)


def _decode_text(codec: Any, text: str) -> str:
    if codec is None or not text:
        return text
    try:
        from backend.app.services.masking_service import decode_text

        return decode_text(codec, text, emit=False)
    except Exception:  # noqa: BLE001
        return text


def _decode_value(codec: Any, value: Any) -> Any:
    if isinstance(value, str):
        return _decode_text(codec, value)
    if isinstance(value, list):
        return [_decode_value(codec, v) for v in value]
    if isinstance(value, dict):
        return {k: _decode_value(codec, v) for k, v in value.items()}
    return value


#: Arabic-Indic / Persian digits → Latin (Latin digits only on every surface the user reads).
_TO_LATIN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)


def _latin_shown_text(plan: ExtendedPlan) -> ExtendedPlan:
    """The fields the user reads on the plan card and as report titles — every search
    title and the cheaper note — in Latin digits. The model copies the file's own digits
    («٢١٠/٣١٠ ألف», e2e run 2026-11-24); the rest of the plan is internal."""
    data = plan.model_dump()
    for req in data.get("requests") or []:
        req["title"] = str(req.get("title") or "").translate(_TO_LATIN_DIGITS)
    cheaper = data.get("cheaper")
    if cheaper:
        cheaper["note"] = str(cheaper.get("note") or "").translate(_TO_LATIN_DIGITS)
        for req in cheaper.get("requests") or []:
            req["title"] = str(req.get("title") or "").translate(_TO_LATIN_DIGITS)
    return ExtendedPlan.model_validate(data)


def decode_plan(codec: Any, plan: ExtendedPlan) -> ExtendedPlan:
    """Every text field of the plan decoded (the stored / shown plan is real text), with
    the user-facing titles and note in Latin digits."""
    if codec is not None:
        plan = ExtendedPlan.model_validate(_decode_value(codec, plan.model_dump()))
    return _latin_shown_text(plan)


# --------------------------------------------------------------------------- billing


def _usage_numbers(usage: Any) -> tuple[int, int, int, int, int]:
    ti = int(getattr(usage, "input_tokens", 0) or 0)
    to = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "details", None) or {}
    reasoning = int(details.get("reasoning_tokens", 0) or 0) if isinstance(details, dict) else 0
    cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
    requests = int(getattr(usage, "requests", 0) or 0)
    return ti, to, reasoning, cached, requests


def _slot_head() -> str:
    try:
        from agents.utils.agent_models import primary_model_of_slot

        return primary_model_of_slot(SLOT)
    except Exception:  # noqa: BLE001
        return ""


def _priced(result: Any, usage: Any) -> tuple[str, float]:
    """``(model, cost_usd)`` the way the tracking layer prices every agent: the model
    that answered when the price list knows it, else the slot's head."""
    if result is not None:
        try:
            from agents.utils.tracking import _usage_attrs

            attrs = _usage_attrs(result, slot=SLOT)
            if "cost_usd" in attrs:
                return str(attrs.get("model_used") or _slot_head()), float(attrs.get("cost_usd") or 0.0)
        except Exception:  # noqa: BLE001
            logger.debug("extended_planner: pricing from the result failed", exc_info=True)
    model = _slot_head()
    try:
        from agents.utils.agent_models import cost_usd

        ti, to, reasoning, cached, _ = _usage_numbers(usage)
        return model, round(cost_usd(model, ti, to, reasoning, cached), 6)
    except Exception:  # noqa: BLE001
        return model, 0.0


def _record_ledger(usage: Any, *, model: str, cost: float, duration_ms: int, outcome: str) -> None:
    """The planner's ``llm_calls`` row (no-op outside a capture scope, or with no tokens)."""
    ti, to, reasoning, cached, requests = _usage_numbers(usage)
    if not ti and not to:
        return
    try:
        from agents.utils.usage_sink import record_call

        record_call(
            agent=LEDGER_AGENT, model=model or None, agent_family=AGENT_FAMILY,
            tokens_in=ti, tokens_out=to, tokens_reasoning=reasoning, tokens_cached=cached,
            cost_usd=cost, requests=requests or 1, duration_ms=duration_ms, outcome=outcome,
        )
    except Exception:  # noqa: BLE001
        logger.debug("extended_planner: ledger row failed", exc_info=True)


# --------------------------------------------------------------------------- run


async def run_extended_planner(
    view: PlannerView,
    *,
    query: str,
    recent_messages: Sequence[Any] | None,
    mode: BudgetMode,
    user_id: str,
    supabase: Any = None,
) -> PlannerRun:
    """Plan the extended search for one turn. ``query`` — the user's message as the turn
    received it (intake-encoded, verbatim); ``recent_messages`` — the conversation
    before it (``ChatMessageSnapshot`` or ``{"role", "content"}``); ``supabase`` — to
    persist new masking fakes before the model reads them (default: the service
    client). Raises on any failure; a ledger row is written either way."""
    codec = _active_codec()
    prompt = render_planner_input(view, query=query, recent_messages=recent_messages, mode=mode)
    encoded = _encode(codec, prompt)
    _persist_fakes(supabase, user_id, codec)
    agent = create_extended_planner(view, mode)

    usage = RunUsage()
    result: Any = None
    t0 = time.perf_counter()
    with track_stage(
        LEDGER_AGENT, agent_family=AGENT_FAMILY, budget_mode=mode.label, map_id=view.map_id,
        documents=len(view.documents), angles=len(view.angles), masked=codec is not None and encoded != prompt,
    ) as span:
        try:
            result = await asyncio.wait_for(
                agent.run(encoded, usage=usage, usage_limits=LIMITS), timeout=PLANNER_TIMEOUT_S,
            )
        except BaseException as exc:
            outcome = "timeout" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            )
            model, cost = _priced(None, usage)
            _record_ledger(usage, model=model, cost=cost, outcome=outcome,
                           duration_ms=int((time.perf_counter() - t0) * 1000))
            span.set(points=round(cost * _POINTS_PER_USD, 4), error_type=type(exc).__name__)
            raise
        model, cost = _priced(result, usage)
        _record_ledger(usage, model=model, cost=cost, outcome="ok",
                       duration_ms=int((time.perf_counter() - t0) * 1000))
        raw = result.output
        if not isinstance(raw, ExtendedPlan):
            raise TypeError(f"extended_planner: unexpected output {type(raw).__name__}")
        span.record_output(raw)  # the encoded output — telemetry never sees real text
        plan = decode_plan(codec, raw)
        points = round(cost * _POINTS_PER_USD, 4)
        span.set(
            points=points,
            requests=len(plan.requests),
            full=sum(r.mode == "full" for r in plan.requests),
            cheaper=len(plan.cheaper.requests) if plan.cheaper else 0,
            left_out=len(plan.left_out),
            model=model,
        )
    return PlannerRun(plan=plan, points=points, usage=usage, model=model)


__all__ = [
    "AGENT_FAMILY",
    "LEDGER_AGENT",
    "LIMITS",
    "MAX_OUTPUT_TOKENS",
    "OUTPUT_RETRIES",
    "PLANNER_TIMEOUT_S",
    "PlannerRun",
    "SLOT",
    "create_extended_planner",
    "decode_plan",
    "limits_problems",
    "normalise_plan",
    "plan_problems",
    "run_extended_planner",
]
