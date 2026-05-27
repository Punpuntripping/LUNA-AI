"""Public entry point for the sector_picker agent.

:func:`run_sector_picker` is the one-shot runner the orchestrator spawns via
``asyncio.create_task`` in :func:`run_retrieval`. It wraps the agent call in:

1. A Logfire span (``deep_search.sector_picker``) so the picker shows up as a
   first-class stage in the trace tree.
2. A hard timeout (``SECTOR_PICKER_TIMEOUT_S``) — picker is on the critical
   path of every deep_search invocation, so we cannot let it hang.
3. Post-validation: canonicalize sector names against ``VALID_SECTORS``,
   re-check the ``[MIN_SECTORS, MAX_SECTORS]`` bound, and downgrade to ``None``
   on any failure (which the executor filter steps interpret as "no filter").

The runner **never raises** — every failure mode degrades to ``None``. The
sector filter is a coarse pre-filter on top of semantic retrieval; running
unfiltered is a strictly safe fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from agents.deep_search_v4.shared.sector_vocab.regulations import canonicalize_sectors
from agents.utils.agent_models import ModelPolicy, cost_usd
from shared.observability import get_logfire

from .agent import SECTOR_PICKER_LIMITS, create_sector_picker
from .deps import Mode, SectorPickerDeps
from .models import MAX_SECTORS, MIN_SECTORS, SectorPickerOutput
from .prompts import build_sector_picker_user_message

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents.deep_search_v4.shared.context import ContextBlock

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# Hard timeout on one sector_picker call (seconds). tier_2 deepseek-flash
# typically returns in ~1-2s; 15s is "something is very wrong, give up". On
# timeout the runner returns ``None`` and the executors run unfiltered.
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
    timeout_s: float = SECTOR_PICKER_TIMEOUT_S,
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
        timeout_s: per-call timeout. Defaults to :data:`SECTOR_PICKER_TIMEOUT_S`.
    """
    blocks = list(context_blocks or [])

    with _logfire.span(
        "deep_search.sector_picker",
        query_id=query_id,
        conversation_id=conversation_id or None,
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
            result = await asyncio.wait_for(
                agent.run(user_msg, deps=deps, usage_limits=SECTOR_PICKER_LIMITS),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "sector_picker: timed out after %.1fs (query_id=%s)",
                timeout_s, query_id,
            )
            span.set_attribute("kind", "timeout")
            span.set_attribute("duration_s", round(time.perf_counter() - t0, 3))
            return None
        except Exception as exc:
            logger.warning(
                "sector_picker: raised %s (query_id=%s); running unfiltered",
                type(exc).__name__, query_id, exc_info=True,
            )
            span.set_attribute("kind", "error")
            span.set_attribute("error", repr(exc))
            span.set_attribute("duration_s", round(time.perf_counter() - t0, 3))
            return None

        output: SectorPickerOutput = result.output
        duration_s = round(time.perf_counter() - t0, 3)

        # Usage + cost — same shape as the other tier_2 agents.
        try:
            usage = result.usage()
            span.set_attribute("input_tokens", int(usage.input_tokens or 0))
            span.set_attribute("output_tokens", int(usage.output_tokens or 0))
            details = usage.details or {}
            reasoning = int(details.get("reasoning_tokens", 0) or 0)
            span.set_attribute("reasoning_tokens", reasoning)
            cost = cost_usd(
                tier="tier_2",
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                reasoning_tokens=reasoning,
                cached_tokens=int(details.get("cached_tokens", 0) or 0),
            )
            span.set_attribute("cost_usd", round(cost, 6))
        except Exception:
            pass

        rationale = (output.rationale or "").strip()
        span.set_attribute("rationale_chars", len(rationale))
        span.set_attribute("duration_s", duration_s)

        if output.sectors is None:
            logger.info(
                "sector_picker: emitted null (too broad). rationale=%r",
                rationale[:120],
            )
            span.set_attribute("kind", "null")
            span.set_attribute("sectors", [])
            return None

        canonical = canonicalize_sectors(list(output.sectors))
        n = len(canonical)
        if n < MIN_SECTORS:
            logger.info(
                "sector_picker: %d canonical sector(s) below MIN=%d -> null. "
                "raw=%s canonical=%s",
                n, MIN_SECTORS, output.sectors, canonical,
            )
            span.set_attribute("kind", "under_min")
            span.set_attribute("sectors", canonical)
            return None
        if n > MAX_SECTORS:
            logger.info(
                "sector_picker: %d canonical sector(s) above MAX=%d -> null. "
                "raw=%s canonical=%s",
                n, MAX_SECTORS, output.sectors, canonical,
            )
            span.set_attribute("kind", "over_max")
            span.set_attribute("sectors", canonical)
            return None

        logger.info(
            "sector_picker: picked %s (n=%d) in %.2fs. rationale=%r",
            canonical, n, duration_s, rationale[:120],
        )
        span.set_attribute("kind", "ok")
        span.set_attribute("sectors", canonical)
        return canonical


__all__ = ["SECTOR_PICKER_TIMEOUT_S", "run_sector_picker"]
