"""Public entry point for the sector_picker agent.

:func:`run_sector_picker` is the one-shot runner the orchestrator spawns via
``asyncio.create_task`` in :func:`run_retrieval`. It wraps the agent call in:

1. A Logfire span (``deep_search.sector_picker``) so the picker shows up as a
   first-class stage in the trace tree.
2. An *optional* internal timeout (``timeout_s``, default ``None`` = uncapped).
   The orchestrator path leaves it uncapped: the picker runs in the shadow of
   the executors, each of which grants it a bounded grace at its own filter
   join point (``sector_picker.consume.resolve_sector_filter``) and
   ``run_full_loop`` reaps the still-pending task once retrieval ends. So the
   picker is bounded by retrieval lifetime, not a fixed kill-at-N-seconds cap
   that fires before the loop even needs the result. Standalone / CLI callers
   may still pass ``timeout_s`` for a self-contained bound.
3. A bound check on the ``[MIN_SECTORS, MAX_SECTORS]`` count, downgrading to
   ``None`` (no filter) when the list is too short or too long.

Sector-*name* validity is enforced upstream in the output schema
(``SectorPickerOutput.sectors`` is ``list[Literal[*VALID_SECTORS]]``): a single
invalid name fails the whole output and triggers a Pydantic AI output-retry.
When the model exhausts its retry budget without producing a fully-valid list,
Pydantic AI raises — caught here by the generic exception handler and degraded
to ``None``. So by the time we read ``result.output`` the names are guaranteed
canonical and deduplicated, and the runner only has to police the count.

The runner **never raises** — every failure mode degrades to ``None``. The
sector filter is a coarse pre-filter on top of semantic retrieval; running
unfiltered is a strictly safe fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from agents.utils.agent_models import AGENT_MODELS, ModelPolicy, cost_usd, resolve_chain
from agents.utils.tracking import track_stage
from shared.observability import get_logfire

from .agent import SECTOR_PICKER_LIMITS, create_sector_picker
from .deps import Mode, SectorPickerDeps
from .models import MAX_SECTORS, MIN_SECTORS, SectorPickerOutput
from .prompts import build_sector_picker_user_message

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents.deep_search_v4.shared.context import ContextBlock

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# Opt-in internal timeout (seconds) for standalone / CLI callers that await the
# picker to completion themselves and want a self-contained bound. The
# orchestrator path passes ``timeout_s=None`` (uncapped) and bounds the picker
# at the consumer instead — see the module docstring and
# ``sector_picker.consume.SECTOR_PICKER_GRACE_S``.
SECTOR_PICKER_TIMEOUT_S: float = 15.0


async def run_sector_picker(
    query: str,
    mode: Mode,
    *,
    planner_brief: str = "",
    context_blocks: "list[ContextBlock] | None" = None,
    model_override: ModelPolicy | str | None = None,
    query_id: int = 0,
    conversation_id: str = "",
    timeout_s: float | None = None,
) -> list[str] | None:
    """Run the sector_picker once and return the canonical sector list (or None).

    Returns:
        - ``list[str]`` of 2-5 canonical sector names when the picker emits a
          valid filter.
        - ``None`` in all other cases: picker timed out, picker raised, picker
          returned ``sectors=None``, the canonicalized list fell below
          ``MIN_SECTORS``, or it exceeded ``MAX_SECTORS``.

    Args:
        query: the raw user query (Arabic).
        mode: planner-decided mode — passed in as context for the picker.
        planner_brief: ``decision.planner_brief`` when non-empty.
        context_blocks: the same filtered ``ContextBlock`` list the executor
            expanders receive (case_brief / planner_brief / prior_search_lessons).
        model_override: optional tier override token / ModelPolicy for the
            ``sector_picker`` slot.
        query_id / conversation_id: telemetry only.
        timeout_s: optional internal cap. ``None`` (default, the orchestrator
            path) runs the picker uncapped — it is bounded at the consumer via
            the per-executor grace + the ``run_full_loop`` reaper. A float
            applies an :func:`asyncio.wait_for` cap for standalone callers.
    """
    blocks = list(context_blocks or [])

    with track_stage(
        "deep_search.sector_picker",
        conversation_id=conversation_id or None,
        agent_family="deep_search",
        query_id=query_id,
        mode=mode,
    ) as span:
        t0 = time.perf_counter()
        deps = SectorPickerDeps(
            query=query,
            mode=mode,
            planner_brief=planner_brief or "",
            context_blocks=blocks,
            model_override=(
                model_override if isinstance(model_override, str) else None
            ),
        )
        user_msg = build_sector_picker_user_message(
            query=query,
            mode=mode,
            planner_brief=planner_brief,
            context_blocks=blocks,
        )

        try:
            agent = create_sector_picker(model_override=model_override)
            run_coro = agent.run(
                user_msg, deps=deps, usage_limits=SECTOR_PICKER_LIMITS
            )
            # ``timeout_s=None`` (orchestrator path): run uncapped and let the
            # consumer-side grace + run_full_loop reaper bound the picker. A
            # float (standalone / CLI) applies a self-contained wait_for cap.
            if timeout_s is not None:
                result = await asyncio.wait_for(run_coro, timeout=timeout_s)
            else:
                result = await run_coro
        except asyncio.TimeoutError:
            logger.warning(
                "sector_picker: timed out after %.1fs (query_id=%s)",
                timeout_s, query_id,
            )
            span.set(kind="timeout", duration_s=round(time.perf_counter() - t0, 3))
            return None
        except asyncio.CancelledError:
            # Reaped by run_full_loop after retrieval finished without needing
            # us (the uncapped picker is bounded by retrieval lifetime). Tag the
            # span and honour the cancellation — never swallow it.
            span.set(kind="cancelled", duration_s=round(time.perf_counter() - t0, 3))
            raise
        except Exception as exc:
            logger.warning(
                "sector_picker: raised %s (query_id=%s); running unfiltered",
                type(exc).__name__, query_id, exc_info=True,
            )
            span.set(
                kind="error",
                error=repr(exc),
                duration_s=round(time.perf_counter() - t0, 3),
            )
            return None

        output: SectorPickerOutput = result.output
        duration_s = round(time.perf_counter() - t0, 3)

        # Usage + cost — same shape as the other tier_2 agents.
        try:
            usage = result.usage()
            span.set(
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
            )
            details = usage.details or {}
            reasoning = int(details.get("reasoning_tokens", 0) or 0)
            cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
            span.set(reasoning_tokens=reasoning, cached_tokens=cached)
            _picker_model = resolve_chain(AGENT_MODELS["sector_picker"])[0]
            cost = cost_usd(
                model_name=_picker_model,
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                reasoning_tokens=reasoning,
                cached_tokens=cached,
            )
            span.set(cost_usd=round(cost, 6))
            # Per-call cost ledger. Runs inside the dispatch's capture scope
            # (this task inherits the bound run_id + buffer at creation time).
            from agents.utils.usage_sink import record_call
            record_call(
                agent="deep_search.sector_picker",
                model=_picker_model,
                agent_family="deep_search",
                tokens_in=int(usage.input_tokens or 0),
                tokens_out=int(usage.output_tokens or 0),
                tokens_reasoning=reasoning,
                tokens_cached=cached,
            )
        except Exception:
            pass

        rationale = (output.rationale or "").strip()
        span.set(rationale_chars=len(rationale), duration_s=duration_s)

        if output.sectors is None:
            logger.info(
                "sector_picker: emitted null (too broad). rationale=%r",
                rationale[:120],
            )
            span.set(kind="null", sectors=[])
            return None

        # Names are already canonical + deduped (enforced by the output schema).
        # The runner only polices the count.
        picked = list(output.sectors)
        n = len(picked)
        if n < MIN_SECTORS:
            logger.info(
                "sector_picker: %d sector(s) below MIN=%d -> null. picked=%s",
                n, MIN_SECTORS, picked,
            )
            span.set(kind="under_min", sectors=picked)
            return None
        if n > MAX_SECTORS:
            logger.info(
                "sector_picker: %d sector(s) above MAX=%d -> null. picked=%s",
                n, MAX_SECTORS, picked,
            )
            span.set(kind="over_max", sectors=picked)
            return None

        logger.info(
            "sector_picker: picked %s (n=%d) in %.2fs. rationale=%r",
            picked, n, duration_s, rationale[:120],
        )
        span.set(kind="ok", sectors=picked)
        return picked


__all__ = ["SECTOR_PICKER_TIMEOUT_S", "run_sector_picker"]
